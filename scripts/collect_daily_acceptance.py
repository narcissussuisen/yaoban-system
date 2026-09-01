"""Collect one auditable daily acceptance record after the trading pipeline."""
from __future__ import annotations
import argparse,json,os,pathlib,subprocess,sys
from datetime import datetime
BASE=pathlib.Path(__file__).resolve().parent.parent
OUT=BASE/'outputs'/'acceptance'
TASKS=['YaobanAuctionMonitor','YaobanPreflight','YaobanPremarket','YaobanPlanGate','YaobanTickDaemon','YaobanScanConfirm','YaobanIntradayMonitor','YaobanEventNotify','YaobanClosePipeline','YaobanDailyRebuild','VibeResearchLiveTickValidation']

def task_info(name):
 ps=f"$i=Get-ScheduledTaskInfo -TaskName '{name}';[pscustomobject]@{{last_run=$i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss');last_result=$i.LastTaskResult;next_run=$i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')}}|ConvertTo-Json -Compress"
 cp=subprocess.run(['powershell','-NoProfile','-Command',ps],capture_output=True,text=True,encoding='utf-8',errors='replace')
 try:return json.loads(cp.stdout)
 except Exception:return {'error':'task_info_unavailable','exit':cp.returncode}

def read_json(path):
 try:return json.loads(path.read_text(encoding='utf-8-sig'))
 except Exception:return None

def atomic(path,value):
 path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+f'.{os.getpid()}.tmp')
 with tmp.open('w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
 os.replace(tmp,path)

def task_log_times(day,mode):
 rows=[]
 for path in sorted((BASE/'outputs'/'task_logs'/day).glob(f'*_{mode}.json')):
  data=read_json(path)
  # P0-6修复: exit=6(伴随监控失效·安全拒绝新仓, 任务本身已运行)计入连续性; 仅崩溃/失败(1,2,3...)剔除
  if not data or data.get('date')!=day or int(data.get('exit_code',-1)) not in (0, 6):continue
  try:rows.append(datetime.strptime(data['started_at'],'%Y%m%d_%H%M%S_%f'))
  except Exception:continue
 return rows

def session_ok(times,start_by,end_after,max_gap_minutes=15):
 rows=[x for x in times if start_by.replace(hour=9,minute=0)<=x<=end_after.replace(hour=15,minute=0)]
 if not rows or rows[0]>start_by or rows[-1]<end_after:return False
 return all((b-a).total_seconds()<=max_gap_minutes*60 for a,b in zip(rows,rows[1:]))

def continuity(day):
 base=datetime.strptime(day,'%Y-%m-%d');stats={}
 for mode in ('auction','scan','monitor','notify'):
  ts=task_log_times(day,mode);stats[mode]={'success_count':len(ts),'first':ts[0].isoformat(sep=' ') if ts else None,'last':ts[-1].isoformat(sep=' ') if ts else None}
 auction=task_log_times(day,'auction');auction_ok=bool(auction and auction[0]<=base.replace(hour=9,minute=20) and auction[-1]>=base.replace(hour=9,minute=25) and all((b-a).total_seconds()<=300 for a,b in zip(auction,auction[1:])))
 checks={}
 for mode in ('scan','monitor'):
  ts=task_log_times(day,mode)
  am=[x for x in ts if x.hour<12];pm=[x for x in ts if x.hour>=12]
  checks[mode]=session_ok(am,base.replace(hour=9,minute=40),base.replace(hour=11,minute=20)) and session_ok(pm,base.replace(hour=13,minute=10),base.replace(hour=14,minute=50))
 notify=task_log_times(day,'notify');checks['notify']=session_ok(notify,base.replace(hour=9,minute=20),base.replace(hour=14,minute=50))
 return {'auction':auction_ok,**checks},stats

def main():
 p=argparse.ArgumentParser();p.add_argument('--date',default=datetime.now().strftime('%Y-%m-%d'));a=p.parse_args();day=a.date
 tasks={n:task_info(n) for n in TASKS};ledger=read_json(BASE/'portfolio'/'ledger.json') or {}
 infra=read_json(BASE/'outputs'/f'preflight_{day}_infra.json');post=read_json(BASE/'outputs'/f'preflight_{day}_post_plan.json')
 live=read_json(BASE.parent/'Vibe-Research'/'validation'/'live-ticks'/'latest.json');next_plans=sorted((BASE/'outputs'/'plans').glob('*_plan.json'))
 fills=[x for x in ledger.get('account',{}).get('fills',[]) if x.get('date')==day]
 auction_latest=read_json(BASE/'outputs'/'auction'/'latest.json');auction_freeze=read_json(BASE/'outputs'/'auction'/f'auction_freeze_{day}.json');tick_snapshot=read_json(BASE/'outputs'/'intraday'/'pos_live.json')
 delivery=BASE/'outputs'/'notifications'/f'delivery_{day.replace("-","")}.jsonl';deliveries=[]
 if delivery.exists():
  for line in delivery.read_text(encoding='utf-8').splitlines():
   try:deliveries.append(json.loads(line))
   except Exception:pass
 continuity_checks,continuity_stats=continuity(day)
 checks={
  'task_log_continuity':all(continuity_checks.values()),
  'auction_latest':bool(auction_latest and auction_latest.get('date')==day and auction_latest.get('read_only') is True),
  'auction_freeze':bool(auction_freeze and auction_freeze.get('date')==day and auction_freeze.get('orders_allowed') is False),
  'auction_delivery':any(x.get('kind')=='auction' and x.get('ok') for x in deliveries),
  'infra_gate':bool(infra and infra.get('status')=='pass'),
  'post_plan_gate':bool(post and post.get('status')=='pass'),
  'premarket_delivery':any(x.get('kind')=='premarket' and x.get('ok') for x in deliveries),
  'close_delivery':any(x.get('kind')=='close' and x.get('ok') for x in deliveries),
  'live_tick':bool(live and live.get('pass') is True and live.get('expected_date')==day),
  'tick_snapshot':bool(tick_snapshot and tick_snapshot.get('date')==day and tick_snapshot.get('time')),
  'ledger_mode':ledger.get('policy',{}).get('account_mode')=='autonomous_paper',
  'ledger_start':ledger.get('start_date')=='2026-08-31',
  'next_plan':any((lambda p: p.stem[:10]>day and read_json(p).get('date')==p.stem[:10] and bool(read_json(p).get('picks')))(p) for p in next_plans),
 }
 required_task_names=['YaobanAuctionMonitor','YaobanPreflight','YaobanPremarket','YaobanPlanGate','YaobanTickDaemon','YaobanScanConfirm','YaobanIntradayMonitor','YaobanEventNotify','YaobanClosePipeline','YaobanDailyRebuild','VibeResearchLiveTickValidation']  # never include this collector itself
 task_checks={n:str(tasks[n].get('last_run','')).startswith(day) and int(tasks[n].get('last_result',-1))==0 for n in required_task_names}
 checks['tasks']=all(task_checks.values());status='pass' if all(checks.values()) else 'fail'
 report={'date':day,'generated_at':datetime.now().isoformat(timespec='seconds'),'status':status,'checks':checks,'task_checks':task_checks,'tasks':tasks,'ledger':{'revision':ledger.get('_revision'),'cash':ledger.get('account',{}).get('cash'),'positions':len(ledger.get('account',{}).get('positions',{})),'fills_today':len(fills),'risk_state':ledger.get('risk_state',{})},'deliveries':[{'kind':x.get('kind'),'event_key':x.get('event_key'),'ok':x.get('ok'),'message_sha256':x.get('message_sha256')} for x in deliveries],'continuity_checks':continuity_checks,'continuity_stats':continuity_stats,'auction_latest':auction_latest,'auction_freeze':auction_freeze,'tick_snapshot':tick_snapshot,'live_tick':live,'next_plan':next_plans[-1].name if next_plans else None}
 path=OUT/f'acceptance_{day}.json';atomic(path,report);print(json.dumps({'status':status,'checks':checks,'path':str(path)},ensure_ascii=False));return 0 if status=='pass' else 2
if __name__=='__main__':raise SystemExit(main())
