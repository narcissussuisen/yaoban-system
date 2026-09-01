"""逐妖持仓秒级守护。

默认只读；--execute-risk 时是唯一盘中卖出执行器：止损、炸板、破VWAP减半。
保守成交：跌停附近、最近3根无量、无T+1可卖份额时只写告警不成交。
"""
from __future__ import annotations
import argparse,json,os,pathlib,sys,time
from datetime import datetime
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent/'src'))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent.parent/'portfolio'))
import pandas as pd
from pytdx.hq import TdxHq_API
from core.intraday import vwap_series
from core.sell import limit_price,limit_pct_of
from ledger import load,transact,sell,sellable_qty
BASE=pathlib.Path(__file__).resolve().parent.parent
OUT=BASE/'outputs'/'intraday'; OUT.mkdir(parents=True,exist_ok=True)
SERVERS=[('115.238.56.198',7709),('115.238.90.165',7709)]

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
 os.replace(tmp,path)

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

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--interval',type=int,default=5);ap.add_argument('--rounds',type=int,default=0);ap.add_argument('--daemon',action='store_true');ap.add_argument('--execute-risk',action='store_true')
 a=ap.parse_args(); now=datetime.now(); day=now.strftime('%Y-%m-%d'); hm=now.strftime('%H:%M')
 if now.weekday()>=5: print(f'[{hm}] 周末非交易日，退出'); return 0
 if not a.daemon and not (('09:30' <= hm <= '11:30') or ('13:00' <= hm <= '15:00')):
  print(f'[{hm}] 非交易时段，退出'); return 0
 if a.daemon and hm>'15:05': print(f'[{hm}] 已过收盘，退出'); return 0
 api=TdxHq_API(heartbeat=False); connected=False
 for h,p in SERVERS:
  try:
   if api.connect(h,p,time_out=8):connected=True;break
  except Exception:pass
 if not connected: print('连接失败',file=sys.stderr); return 2
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
  rounds+=1; n=datetime.now(); hm2=n.strftime('%H:%M')
  if a.daemon and not(('09:30'<=hm2<='11:30')or('13:00'<=hm2<='15:05')):
   if hm2>'15:05':break
   time.sleep(60);continue
  st=load(); live={'date':day,'time':n.strftime('%H:%M:%S'),'positions':[]}
  for sym,pos in list(st['account']['positions'].items()):
   pc=prev_close(sym,day)
   if not pc:continue
   try:
    bars=api.get_security_bars(0,market_of(sym),sym,0,300); tx=api.get_transaction_data(market_of(sym),sym,0,30)
   except Exception:bars=tx=None
   if bars is None:
    err+=1
    if err>=3:interval=min(interval*2,30)
    if err>=6 and not reconnect():
     append_event({'date':day,'time':n.strftime('%H:%M:%S'),'sym':sym,'trigger':'data_failure','action':'halt','detail':'TDX重连失败'})
     print('TDX重连失败',file=sys.stderr);return 3
    continue
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
      if s.get('policy', {}).get('account_mode') != 'autonomous_paper' or s.get('policy', {}).get('require_human_decision'):
       raise ValueError('risk execution is limited to autonomous_paper')
      q=min(qty,sellable_qty(s,sym,day))//100*100
      if q<100:raise ValueError('无可卖份额')
      sell(s,sym,f'{day} {ev["time"]}',epx,q,trig,plan_ref='tick-risk');return {'sym':sym,'qty':q,'px':epx,'trigger':trig}
     try:st,res=transact(mut);print('[RISK-EXEC] '+json.dumps(res,ensure_ascii=False),flush=True)
     except Exception as e:ev['action']='failed';ev['blocked_reason']=str(e);append_event(ev);print('[RISK-FAIL] '+str(e),file=sys.stderr);return 4
  atomic_json(OUT/'pos_live.json',live);time.sleep(interval)
 try:api.disconnect()
 except Exception:pass
 return 0
if __name__=='__main__':sys.exit(main())
