"""EvoAlpha 持仓秒级守护。

默认只读；--execute-risk 时是唯一盘中卖出执行器：止损、炸板、破VWAP减半。
保守成交：跌停附近、最近3根无量、无T+1可卖份额时只写告警不成交。
"""
from __future__ import annotations
import argparse,json,os,pathlib,sys,time
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent/'src'))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent/'portfolio'))
import pandas as pd
from pytdx.hq import TdxHq_API
from core.intraday import vwap_series
from core.tencent_minline import min_df as tencent_min_df, quote as tencent_quote
from core.sell import limit_price,limit_pct_of
from ledger import load,transact,sell,sellable_qty,record_autonomous_decision
BASE=pathlib.Path(__file__).resolve().parent.parent
OUT=BASE/'outputs'/'intraday'; OUT.mkdir(parents=True,exist_ok=True)
SERVERS=[('59.36.5.11',7709),('117.34.114.18',7709),('117.34.114.13',7709),('117.34.114.27',7709),
 ('117.34.114.16',7709),('117.34.114.20',7709),('117.34.114.17',7709),('117.34.114.14',7709),
 ('117.34.114.15',7709),('115.238.56.198',7709)]

def market_of(s):
 if s.startswith('900'): return 1
 if s[0] in ('4','8') or s.startswith('92'): return 2
 return 1 if s[0] in ('6','9','5') else 0

def prev_close(s,d):
 from core.daily_src import prev_close_of
 return prev_close_of(s,d)

def atomic_json(path,obj):
 tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
 with tmp.open('w',encoding='utf-8') as f:
  json.dump(obj,f,ensure_ascii=False); f.flush(); os.fsync(f.fileno())
 # 2026-09-03 修复(daemon 11:16 死亡根因): Windows 下 monitor/scan/notify 并发读 pos_live.json 时
 # os.replace 目标被短暂锁住抛 PermissionError(WinError 5); 原实现无重试, 一次撞锁即打死唯一卖出执行器。
 # 读者读完即释放, 50ms 级退避重试可覆盖; 耗尽后抛出交由调用方兜底。
 for _i in range(8):
  try:os.replace(tmp,path);return
  except PermissionError:
   if _i==7:raise
   time.sleep(0.05)

def append_event(ev):
 with (OUT/'risk_events.jsonl').open('a',encoding='utf-8') as f:
  f.write(json.dumps(ev,ensure_ascii=False)+chr(10)); f.flush(); os.fsync(f.fileno())

def prev_limit_close(sym,day):
 try:
  from core.daily_src import load_daily
  d=load_daily(sym)
  if d is None or len(d)<3:return False
  if str(d['date'].iloc[-1])==day:c1,c2=float(d['close'].iloc[-2]),float(d['close'].iloc[-3])
  else:c1,c2=float(d['close'].iloc[-1]),float(d['close'].iloc[-2])
  return c1>=round(c2*(1+limit_pct_of(sym)),2)-0.005
 except Exception:return False

def execute_tick_risk_sell(s,sym,day,ev_time,epx,qty,trig):
 """tick 风险卖出执行器（P0.4/C1）：先登记自主决策 provenance，后卖出。

 模块级可测函数（计划 §3.4 真实路径测试直接驱动，替代原 mut 闭包）；
 登记返回 dec-auto-* 合法 decision_id 透传给 ledger 卖出调用，退役人工拼接的 dec-tick-*。
 """
 if s.get('policy', {}).get('account_mode') != 'autonomous_paper' or s.get('policy', {}).get('require_human_decision'):
  raise ValueError('risk execution is limited to autonomous_paper')
 q=min(qty,sellable_qty(s,sym,day))//100*100
 if q<100:raise ValueError('无可卖份额')
 ts=f'{day} {ev_time}'
 did=record_autonomous_decision(s,{'sym':sym,'signal_ts':ts,'signal_px':epx,
  'rule':f'tick_risk:{trig}','plan_ref':'tick-risk'})
 sell(s,sym,ts,epx,q,trig,plan_ref='tick-risk',signal_ts=ts,decision_ts=ts,decision_id=did)
 return {'sym':sym,'qty':q,'px':epx,'trigger':trig,'decision_id':did}

def _acquire_daemon_lock(out):
 """daemon 单实例锁(2026-09-10 加固): 环境层进程复制会造出双 daemon 交替写
 pos_live(9/10 实录 count 1/2 每5秒跳变); 持锁者每轮刷新 mtime, 重复者 120s 内检测到活锁即退出。"""
 lock = out / '_tick_daemon.lock'
 for _ in range(2):
  try:
   fd=os.open(str(lock), os.O_CREAT|os.O_EXCL|os.O_WRONLY)
   os.write(fd, str(os.getpid()).encode()); os.close(fd)
   return True
  except FileExistsError:
   try:
    if time.time()-lock.stat().st_mtime > 120:
     lock.unlink(); continue
   except OSError:
    continue
   return False
 return False


def main():
 ap=argparse.ArgumentParser();ap.add_argument('--interval',type=int,default=5);ap.add_argument('--rounds',type=int,default=0);ap.add_argument('--daemon',action='store_true');ap.add_argument('--execute-risk',action='store_true')
 a=ap.parse_args(); now=datetime.now(ZoneInfo('Asia/Shanghai')); day=now.strftime('%Y-%m-%d'); hm=now.strftime('%H:%M')
 if a.daemon and not _acquire_daemon_lock(OUT):
  print('[daemon] 已有活 daemon 持有单实例锁, 退出', file=sys.stderr)
  return 0
 if now.weekday()>=5: print(f'[{hm}] 周末非交易日，退出'); return 0
 if not a.daemon and not (('09:30' <= hm <= '11:30') or ('13:00' <= hm <= '15:00')):
  print(f'[{hm}] 非交易时段，退出'); return 0
 if a.daemon and hm>'15:05': print(f'[{hm}] 已过收盘，退出'); return 0
 api=TdxHq_API(heartbeat=False); connected=False
 for h,p in SERVERS:
  try:
   if api.connect(h,p,time_out=8):connected=True;break
  except Exception:pass
 if not connected:
  print('[WARN] TDX连接失败, 启用腾讯备胎(min5+quote)',file=sys.stderr)
  api=None
 def reconnect():
  nonlocal api
  for _ in range(3):
   try:api.disconnect()
   except Exception:pass
   for h,p in SERVERS:
    try:
     api=TdxHq_API(heartbeat=False)
     if api.connect(h,p,time_out=8):return True
    except Exception:pass
   time.sleep(3)
  return False
 fired=set(); rounds=0; interval=a.interval; err=0
 while a.rounds==0 or rounds<a.rounds:
  rounds+=1; n=datetime.now(ZoneInfo('Asia/Shanghai')); hm2=n.strftime('%H:%M')
  if a.daemon and not(('09:30'<=hm2<='11:30')or('13:00'<=hm2<='15:05')):
   if hm2>'15:05':break
   time.sleep(60);continue
  st=load(); live={'date':day,'time':n.strftime('%H:%M:%S'),'positions':[]}
  for sym,pos in list(st['account']['positions'].items()):
   pc=prev_close(sym,day)
   if not pc:continue
   bars=tx=None
   if api is not None:
    try:
     bars=api.get_security_bars(0,market_of(sym),sym,0,300); tx=api.get_transaction_data(market_of(sym),sym,0,30)
    except Exception:bars=tx=None
   if bars is None:
    # 2026-09-10: TDX 不可用时降级腾讯 mkline m5 + qt 报价 (a-stock-data 备用源速查)
    fb=tencent_min_df(sym,day)
    if fb is None:
     err+=1
     if err>=3:interval=min(interval*2,30)
     if err>=6 and not reconnect():
      append_event({'date':day,'time':n.strftime('%H:%M:%S'),'sym':sym,'trigger':'data_failure','action':'halt','detail':'TDX/腾讯双源失败'})
      print('TDX/腾讯双源失败',file=sys.stderr);return 3
     continue
    df=fb;vw=vwap_series(df);last=float(df['close'].iloc[-1]);hi=float(df['high'].max());lo=float(df['low'].min())
    px=float(tencent_quote([sym]).get(sym) or last)
   else:
    err=0; interval=a.interval
    rows=[[str(b['datetime']),float(b['open']),float(b['high']),float(b['low']),float(b['close']),float(b['vol']),float(b['amount'])] for b in bars if str(b['datetime']).startswith(day)]
    if len(rows)<3:continue
    df=pd.DataFrame(rows,columns=['ts','open','high','low','close','volume','amount']);vw=vwap_series(df);last=float(df['close'].iloc[-1]);hi=float(df['high'].max());lo=float(df['low'].min());px=float(tx[-1]['price']) if tx else last
   t1=sellable_qty(st,sym,day); live['positions'].append({'sym':sym,'px':px,'chg':(px/pc-1)*100,'t1_locked':t1<=0})
   lup=limit_price(pc,sym); ldn=round(pc*(1-limit_pct_of(sym)),2); trig=None;qty=0;epx=px
   exec_window=('09:30'<=hm2<='11:30')or('13:00'<=hm2<='15:00')
   if pos.get('stop_px') and px<=float(pos['stop_px']):trig='stop_loss';qty=int(pos['qty'])
   elif hi>=lup-0.01 and last<lup*0.995:trig='zhaban_sell';qty=int(pos['qty']);epx=last
   elif not prev_limit_close(sym,day) and hi>=pc*1.07 and last<float(vw.iloc[-1])*0.997:trig='vwap_halve';qty=int(pos['qty'])//2//100*100;epx=last
   if exec_window and trig and (sym,trig) not in fired:
    fired.add((sym,trig));qty=min(qty,t1)//100*100;volok=float(df['volume'].iloc[-3:].sum())>0;blocked=epx<=ldn+0.005 or not volok or qty<100
    ev={'date':day,'time':n.strftime('%H:%M:%S'),'sym':sym,'trigger':trig,'px':round(epx,3),'qty':qty,'limit_down':ldn,'action':'alert_only' if(blocked or not a.execute_risk)else'execute','blocked_reason':'跌停/无量/无可卖份额' if blocked else ''}
    append_event(ev);print('[RISK] '+json.dumps(ev,ensure_ascii=False),flush=True)
    if a.execute_risk and not blocked:
     if st.get('policy', {}).get('account_mode') != 'autonomous_paper' or st.get('policy', {}).get('require_human_decision'):
      ev['action']='blocked_account_mode';ev['blocked_reason']='risk execution is limited to autonomous_paper';append_event(ev);print('[RISK-BLOCK] account mode',file=sys.stderr);return 5
     def mut(s):
      return execute_tick_risk_sell(s,sym,day,ev['time'],epx,qty,trig)
     try:st,res=transact(mut);print('[RISK-EXEC] '+json.dumps(res,ensure_ascii=False),flush=True)
     except Exception as e:ev['action']='failed';ev['blocked_reason']=str(e);append_event(ev);print('[RISK-FAIL] '+str(e),file=sys.stderr);return 4
   # 2026-09-03 修复: pos_live 单轮写失败(锁冲突耗尽等)不得打死 daemon —— 守护进程死 = 持仓裸奔,
   # 失败方向应安全(pos_live 陈旧 -> scan 禁新仓), 打警告后下一轮重写。
   # 注意: 本块必须在 while 循环体内(2 空格缩进) —— 首版修复误写成 1 空格导致语句被挪出循环,
   # daemon 空转不写 pos_live 不 sleep(9/3 14:00-14:05 全部"挂死"假象即此)。
   try:atomic_json(OUT/'pos_live.json',live)
   except Exception as e:print(f'[WARN] pos_live 写入失败(下轮重试): {e}',file=sys.stderr,flush=True)
   if a.daemon:
    try:os.utime(str(OUT/'_tick_daemon.lock'), None)  # 刷新单实例锁 mtime(存活证明)
    except OSError:pass
   time.sleep(interval)
 if api is not None:
  try:api.disconnect()
  except Exception:pass
 return 0
if __name__=='__main__':sys.exit(main())
