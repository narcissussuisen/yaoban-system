"""Collect one auditable daily acceptance record after the trading pipeline.

P0.5 重构（2026-09-01，蓝图 B0-P0-5 修复）:
  - 双轨: probe(缺省, 写 acceptance_{day}_probe.json, 永不触碰正式文件) / final(--final, 正式报告)
  - final 三重门槛: ①显式 --date ②墙钟与 --date 同日且 >= 15:05 ③首份优先(已存在 final 且
    status=pass 的报告拒绝覆盖; --force 仅当既有报告 status!=pass 时允许盖写并标记 regenerated)
  - 输入完整性: inputs 枚举 ok/missing; 任一必需输入 missing → status=incomplete
  - F7 修复: required 任务移除已禁用的 YaobanDailyRebuild; rebuild 完成性改查
    daily_rebuilt 产物 mtime 证据
  - exit code: 0=pass / 2=fail / 3=incomplete或降级 / 4=拒绝覆盖
"""
from __future__ import annotations
import argparse,json,os,pathlib,subprocess,sys
from datetime import datetime
BASE=pathlib.Path(__file__).resolve().parent.parent
OUT=BASE/'outputs'/'acceptance'
REBUILT_DIR=pathlib.Path('F:/WorkBuddyItem/a股level2/daily_rebuilt')
# 信息采集含 Rebuild(可见性); 必需清单不含(任务已禁用, 完成性走产物证据)
TASKS=['YaobanAuctionMonitor','YaobanPreflight','YaobanPremarket','YaobanPlanGate','YaobanTickDaemon','YaobanScanConfirm','YaobanIntradayMonitor','YaobanEventNotify','YaobanClosePipeline','YaobanDailyRebuild','VibeResearchLiveTickValidation']
REQUIRED_TASKS=[n for n in TASKS if n!='YaobanDailyRebuild']
FINAL_NOT_BEFORE=(15,5)  # 墙钟门槛: 当日 15:05 起（收盘后）

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

def rebuild_products_ok(day:str)->bool:
 """F7 修复: rebuild 完成性以产物 mtime 为证据（当日有 parquet 更新）."""
 try:
  for p in REBUILT_DIR.iterdir():
   if p.suffix=='.parquet' and datetime.fromtimestamp(p.stat().st_mtime).strftime('%Y-%m-%d')==day:
    return True
 except OSError:
  return False
 return False

def final_window_ok(now:datetime,day:str)->bool:
 return now.strftime('%Y-%m-%d')==day and (now.hour,now.minute)>=FINAL_NOT_BEFORE

def main(argv=None,now=None):
 now=now or datetime.now()
 p=argparse.ArgumentParser()
 p.add_argument('--date',default=None,help='目标交易日 YYYY-MM-DD（final 必填）')
 p.add_argument('--final',action='store_true',help='正式验收报告（三重门槛）')
 p.add_argument('--force',action='store_true',help='事故恢复：仅当既有正式报告 status!=pass 时允许盖写')
 a=p.parse_args(argv)
 day=a.date or now.strftime('%Y-%m-%d')
 is_final=a.final;probe_reason=None
 # ---- final 三重门槛（按序） ----
 if is_final:
  if not a.date:
   is_final=False;probe_reason='final_requires_explicit_date'
  elif not final_window_ok(now,day):
   is_final=False;probe_reason='outside_final_window'
 path=OUT/(f'acceptance_{day}.json' if is_final else f'acceptance_{day}_probe.json')
 regenerated=False
 if is_final and path.exists():
  prev=read_json(path) or {}
  if prev.get('final') is True and prev.get('status')=='pass':
   print(json.dumps({'status':'refused_overwrite','reason':'final_pass_report_exists','path':str(path)},ensure_ascii=False))
   return 4
  if not a.force:
   print(json.dumps({'status':'refused_overwrite','reason':'final_report_exists_use_force','path':str(path)},ensure_ascii=False))
   return 4
  regenerated=True
 # ---- 数据采集 ----
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
 next_plan_ok=any((lambda p: p.stem[:10]>day and read_json(p).get('date')==p.stem[:10] and bool(read_json(p).get('picks')))(p) for p in next_plans)
 rebuild_ok=rebuild_products_ok(day)
 # ---- 输入完整性枚举（missing != fail） ----
 inputs={
  'preflight_infra':'ok' if infra else 'missing',
  'preflight_post_plan':'ok' if post else 'missing',
  'live_tick':'ok' if live else 'missing',
  'auction_latest':'ok' if auction_latest else 'missing',
  'auction_freeze':'ok' if auction_freeze else 'missing',
  'tick_snapshot':'ok' if tick_snapshot else 'missing',
  'delivery_log':'ok' if delivery.exists() else 'missing',
  'next_plan':'ok' if next_plan_ok else 'missing',
  'ledger':'ok' if ledger.get('account') else 'missing',
  'rebuild_products':'ok' if rebuild_ok else 'missing',
 }
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
  'next_plan':next_plan_ok,
  'rebuild_products':rebuild_ok,
 }
 # 修复后引擎的挂账纪律: 当日 fill 无 decision_id 视为数据质量问题(2026-09-01 起 P0.2+P0.4 落地前先观测)
 task_checks={n:str(tasks[n].get('last_run','')).startswith(day) and int(tasks[n].get('last_result',-1))==0 for n in REQUIRED_TASKS}
 checks['tasks']=all(task_checks.values())
 missing_inputs=[k for k,v in inputs.items() if v=='missing']
 if missing_inputs:
  status='incomplete'
 elif all(checks.values()):
  status='pass'
 else:
  status='fail'
 report={'date':day,'generated_at':now.isoformat(timespec='seconds'),'status':status,'final':is_final,
  'probe_reason':probe_reason,'regenerated':regenerated,'inputs':inputs,
  'checks':checks,'task_checks':task_checks,'required_tasks':REQUIRED_TASKS,'tasks':tasks,
  'ledger':{'revision':ledger.get('_revision'),'cash':ledger.get('account',{}).get('cash'),'positions':len(ledger.get('account',{}).get('positions',{})),'fills_today':len(fills),'risk_state':ledger.get('risk_state',{})},
  'deliveries':[{'kind':x.get('kind'),'event_key':x.get('event_key'),'ok':x.get('ok'),'message_sha256':x.get('message_sha256')} for x in deliveries],
  'continuity_checks':continuity_checks,'continuity_stats':continuity_stats,'auction_latest':auction_latest,'auction_freeze':auction_freeze,'tick_snapshot':tick_snapshot,'live_tick':live,'next_plan':next_plans[-1].name if next_plans else None}
 atomic(path,report)
 print(json.dumps({'status':status,'final':is_final,'probe_reason':probe_reason,'missing_inputs':missing_inputs,'path':str(path)},ensure_ascii=False))
 if probe_reason:return 3
 if status=='incomplete':return 3
 return 0 if status=='pass' else 2
if __name__=='__main__':raise SystemExit(main())
