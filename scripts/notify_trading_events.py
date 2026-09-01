"""Send deduplicated intraday risk and simulated-fill events to Feishu."""
from __future__ import annotations
import argparse, json, os, pathlib, sys
from datetime import datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from feishu_notify import send_text
BASE=pathlib.Path(__file__).resolve().parent.parent

def _json(path, default):
 try:return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default
 except Exception:return default

def _risk_rows(day):
 rows=[]
 alert_path=BASE/'outputs'/'intraday'/f'alerts_{day.replace("-","")}.json'
 for row in _json(alert_path,[]):
  if isinstance(row,dict) and row.get('event_kind') in ('risk','data_failure'):
   rows.append({'sym':row.get('sym','market'),'time':row.get('ts',''),'rule':row.get('rule_id','unknown'),'label':row.get('event_label','风险或数据事件'),'action':row.get('action','alert')})
 event_path=BASE/'outputs'/'intraday'/'risk_events.jsonl'
 if event_path.exists():
  for line in event_path.read_text(encoding='utf-8').splitlines():
   try:row=json.loads(line)
   except Exception:continue
   if row.get('date')!=day:continue
   rows.append({'sym':row.get('sym','market'),'time':row.get('time',''),'rule':row.get('trigger','unknown'),'label':'秒级持仓风险或数据事件','action':row.get('action','alert'),'detail':row.get('detail') or row.get('blocked_reason','')})
 return rows

def _atomic(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
 with tmp.open('w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)

def _latest_success(day,mode):
 latest=None
 for path in (BASE/'outputs'/'task_logs'/day).glob(f'*_{mode}.json'):
  row=_json(path,{})
  if row.get('date')!=day or int(row.get('exit_code',-1))!=0:continue
  try:ts=datetime.strptime(row['finished_at'],'%Y-%m-%d %H:%M:%S.%f')
  except Exception:continue
  latest=max(latest,ts) if latest else ts
 return latest

def _tick_time(day):
 row=_json(BASE/'outputs'/'intraday'/'pos_live.json',{})
 if row.get('date')!=day:return None
 try:return datetime.strptime(day+' '+row['time'],'%Y-%m-%d %H:%M:%S')
 except Exception:return None

def monitoring_gaps(day,now):
 hm=now.strftime('%H:%M');gaps={}
 if '09:20'<=hm<='09:29':
  ts=_latest_success(day,'auction')
  if ts is None or (now-ts).total_seconds()>300:gaps['auction']='集合竞价观察最近5分钟无成功记录'
 if ('09:40'<=hm<='11:30') or ('13:10'<=hm<='15:00'):
  for mode,label in (('scan','全市场扫描'),('monitor','候选/持仓规则监控')):
   ts=_latest_success(day,mode)
   if ts is None or (now-ts).total_seconds()>900:gaps[mode]=label+'最近15分钟无成功记录'
 if ('09:35'<=hm<='11:30') or ('13:05'<=hm<='15:00'):
  ts=_tick_time(day)
  if ts is None or (now-ts).total_seconds()>120:gaps['tick']='持仓tick最近2分钟无新鲜快照'
 return gaps

def notify_monitoring_health(day,now):
 path=BASE/'outputs'/'notifications'/f'monitor_health_{day}.json';state=_json(path,{'active':{}});old=state.get('active',{});new=monitoring_gaps(day,now);committed=dict(old);sent=failed=0
 for mode,detail in new.items():
  if mode in old:continue
  key=f'monitor-gap:{day}:{mode}:{now:%H%M}'
  text=f'逐妖交易团队｜监控中断 {day} {now:%H:%M:%S}\n模块：{mode}\n详情：{detail}\n状态：禁止依赖该模块产生新仓，等待恢复。'
  if send_text(text,event_key=key,kind='failure'):sent+=1;committed[mode]=detail
  else:failed+=1
 for mode in set(old)-set(new):
  key=f'monitor-recovered:{day}:{mode}:{now:%H%M}'
  text=f'逐妖交易团队｜监控恢复 {day} {now:%H:%M:%S}\n模块：{mode}\n状态：数据连续性已恢复，交易仍服从其他门禁。'
  if send_text(text,event_key=key,kind='alert'):sent+=1;committed.pop(mode,None)
  else:failed+=1
 state={'date':day,'checked_at':now.strftime('%Y-%m-%d %H:%M:%S'),'active':committed};_atomic(path,state)
 return sent,failed

def main():
 p=argparse.ArgumentParser();p.add_argument('--date',default=datetime.now().strftime('%Y-%m-%d'));a=p.parse_args();sent=0;failed=0
 hs,hf=notify_monitoring_health(a.date,datetime.now());sent+=hs;failed+=hf
 freeze=_json(BASE/'outputs'/'auction'/f'auction_freeze_{a.date}.json',{})
 if freeze.get('date')==a.date and freeze.get('orders_allowed') is False:
  picks=freeze.get('picks',[]);names='、'.join(str(x.get('sym','')) for x in picks[:8]) or '无'
  text=f"逐妖交易团队｜集合竞价冻结 {freeze.get('frozen_at','')}\n关注标的：{names}\n全市场活跃样本：{len(freeze.get('active_top',[]))}只\n状态：仅观察，不在集合竞价阶段生成模拟订单。"
  if send_text(text,event_key=f'auction-freeze:{a.date}',kind='auction'):sent+=1
  else:failed+=1
 for row in _risk_rows(a.date):
  key=f"alert:{a.date}:{row['sym']}:{row['rule']}:{row['time']}:{row['action']}"
  detail=f"\n详情：{row['detail']}" if row.get('detail') else ''
  text=f"逐妖交易团队｜盘中重大事件 {a.date} {row['time']}\n标的：{row['sym']}\n事件：{row['label']}\n规则：{row['rule']}\n执行状态：{row['action']}{detail}\n说明：仅为自主模拟盘事件，不涉及真实资金。"
  if send_text(text,event_key=key,kind='alert'):sent+=1
  else:failed+=1
 ledger=_json(BASE/'portfolio'/'ledger.json',{})
 for fill in ledger.get('account',{}).get('fills',[]):
  if fill.get('date')!=a.date:continue
  key=f"fill:{fill.get('ts')}:{fill.get('sym')}:{fill.get('side')}:{fill.get('qty')}:{fill.get('px')}"
  side='买入' if fill.get('side')=='buy' else '卖出'
  text=f"逐妖交易团队｜模拟成交 {fill.get('ts')}\n{side} {fill.get('sym')} {fill.get('qty')}股，模拟成交价 {fill.get('px')}\n触发规则：{fill.get('reason','未标注')}\n说明：仅为10万元自主模拟盘记录，不涉及真实资金。"
  if send_text(text,event_key=key,kind='fill'):sent+=1
  else:failed+=1
 print(json.dumps({'sent_or_seen':sent,'failed':failed},ensure_ascii=False));return 2 if failed else 0
if __name__=='__main__':raise SystemExit(main())
