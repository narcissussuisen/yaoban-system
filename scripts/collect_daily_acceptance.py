"""Collect one auditable daily acceptance record after the trading pipeline.

P0.5 重构（2026-09-01，蓝图 B0-P0-5 修复）:
  - 双轨: probe(缺省, 写 acceptance_{day}_probe.json, 永不触碰正式文件) / final(--final, 正式报告)
  - final 三重门槛: ①显式 --date ②墙钟与 --date 同日且 >= 15:05 ③首份优先(已存在 final 且
    status=pass 的报告拒绝覆盖; --force 仅当既有报告 status!=pass 时允许盖写并标记 regenerated)
  - 输入完整性: inputs 枚举 ok/missing; 任一必需输入 missing → status=incomplete
  - F7 修复: required 任务移除已禁用的 EvoAlphaDailyRebuild; rebuild 完成性改查
    daily_rebuilt 产物 mtime 证据
  - exit code: 0=pass / 2=fail / 3=incomplete或降级 / 4=拒绝覆盖

P0 硬性验收项加固（2026-09-04 凌晨, 9/3 复盘裁定）:
  - tick_snapshot: 升级为"收盘新鲜度"—— pos_live.time >= 14:55, daemon 盘中死亡留下的
    陈旧快照(9/3 实录 11:15:57)不再蒙混过关(进程存在≠数据持续更新)
  - tick_watchdog: 当日 risk_events 无 watch_limit(看门狗重启额度耗尽)与
    data_failure halt(守护自弃)事件
  - offplan_fills: 当日买入全部计划内(plan_match.in_plan / plan_ref), 计划外成交即 fail
"""
from __future__ import annotations
import argparse,json,os,pathlib,subprocess,sys
from datetime import datetime
BASE=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "portfolio"))
from ledger import LEDGER  # noqa: E402  env-aware (EVOALPHA_LEDGER) — R0.2 单一真相源
OUT=BASE/'outputs'/'acceptance'
REBUILT_DIR=pathlib.Path('F:/WorkBuddyItem/a股level2/daily_rebuilt')
# 信息采集含 Rebuild(可见性); 必需清单不含(任务已禁用, 完成性走产物证据)
TASKS=['EvoAlphaAuctionMonitor','EvoAlphaPreflight','EvoAlphaPremarket','EvoAlphaPlanGate','EvoAlphaTickDaemon','EvoAlphaScanConfirm','EvoAlphaIntradayMonitor','EvoAlphaEventNotify','EvoAlphaClosePipeline','EvoAlphaDailyRebuild','VibeResearchLiveTickValidation']
REQUIRED_TASKS=[n for n in TASKS if n!='EvoAlphaDailyRebuild']
FINAL_NOT_BEFORE=(15,5)  # 墙钟门槛: 当日 15:05 起（收盘后）
# 2026-09-12 R0.2: 主账本重建为 50 万独立主账本(8.1 裁决), start_date 随之变更。
# 原为字面量 '2026-08-31' 内联在 checks 里 —— 抽成单一常量, 避免账本重建时漏改。
# 该检查的语义 = "账户未被静默重建"; R5 版本 registry 上线后应由当前净值段的起点驱动。
EXPECTED_LEDGER_START='2026-09-14'

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

def llm_health(day, days=7, max_fail=0.20):
    """R3.2 裁量层健康度：最近 `days` 个自然日内 LLM 调用的 **schema 通过率**。

    ⚠️ 为什么按「最近 N 天滚动」而不是「当日」：链序里 `acceptance` 排在 `decision-chain`
       **之前**（`acceptance` 是 15:35 链里"唯一正式验收入口"，位置固定）→ 当天验收时
       当日 LLM 记录**还没产生**，按当日算会恒空。滚动窗口既避开时序，又能反映趋势。

    ⚠️ 为什么纳入 `checks`：`schema_failed` 会让裁量**回落保守档**（=降级），直接削弱决策质量；
       但它**不是链故障**（不并入 `$codes`/`$dataFailed`），而是「当日评级」事件 → 由本检查承载，
       呈现在 alert 卡片上，既不误报链故障、又不静默。

    返回 `(ok, detail)`。**从未运行过时返回 ok**（避免上线首日误报）。
    """
    import datetime as _dt
    import json as _json
    root = pathlib.Path(__file__).resolve().parents[1] / "outputs" / "decision_chain" / "llm"
    if not root.exists():
        return True, {"n": 0, "note": "尚无裁量记录"}
    cut = (_dt.date.fromisoformat(day) - _dt.timedelta(days=days)).isoformat()
    tot = bad = 0
    by = {}
    for fp in root.rglob("*.json"):
        if "cache" in fp.parts:
            continue
        try:
            d = _json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        dt = str(d.get("date") or "")
        if not dt or dt < cut:
            continue
        tot += 1
        st = str(d.get("status"))
        by[st] = by.get(st, 0) + 1
        if st != "ok":
            bad += 1
    if tot == 0:
        return True, {"n": 0, "window_days": days}
    rate = bad / tot
    return (rate <= max_fail), {"n": tot, "bad": bad, "fail_rate": round(rate, 4),
                               "window_days": days, "threshold": max_fail, "by_status": by}


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
 tasks={n:task_info(n) for n in TASKS};ledger=read_json(LEDGER) or {}
 infra=read_json(BASE/'outputs'/f'preflight_{day}_infra.json');post=read_json(BASE/'outputs'/f'preflight_{day}_post_plan.json')
 live=read_json(BASE.parent/'Vibe-Research'/'validation'/'live-ticks'/'latest.json');next_plans=sorted((BASE/'outputs'/'plans').glob('*_plan.json'))
 fills=[x for x in ledger.get('account',{}).get('fills',[]) if x.get('date')==day]
 auction_latest=read_json(BASE/'outputs'/'auction'/'latest.json');auction_freeze=read_json(BASE/'outputs'/'auction'/f'auction_freeze_{day}.json');tick_snapshot=read_json(BASE/'outputs'/'intraday'/'pos_live.json')
 # ---- P0 硬性项输入: 看门狗/守护事件 + 计划外成交检测(2026-09-04) ----
 risk_events_path=BASE/'outputs'/'intraday'/'risk_events.jsonl'
 day_risk_events=[]
 if risk_events_path.exists():
  for _ln in risk_events_path.read_text(encoding='utf-8').splitlines():
   try:
    _ev=json.loads(_ln)
    if _ev.get('date')==day:day_risk_events.append(_ev)
   except Exception:pass
 guard_fail=[e for e in day_risk_events if e.get('trigger')=='watch_limit' or (e.get('trigger')=='data_failure' and e.get('action')=='halt')]
 guard_restarts=sum(1 for e in day_risk_events if e.get('trigger')=='watch_restart')
 # 2026-09-11: 看门狗判据补两个盲区 ——
 #   ① daemon_crash（tick_monitor 致命异常退出, 新增留痕）必须计入守护失败;
 #   ② 隔离态不止来自 watch_limit 事件: 收盘时 tick_guard_state 仍停在
 #      restart_exhausted/degraded, 或双信号显示"进程死/数据不新鲜", 同样算守护失败。
 #      旧实现只看 watch_limit 事件, 一旦隔离由其它路径写成就会从验收口径漏掉。
 guard_state=read_json(BASE/'outputs'/'intraday'/'tick_guard_state.json') or {}
 guard_state_fail=bool(guard_state.get('date')==day and (
     str(guard_state.get('state')) in ('restart_exhausted',)
     or guard_state.get('degraded') is True
     or guard_state.get('daemon_alive') is False
     or guard_state.get('tick_fresh') is False))
 guard_crash=[e for e in day_risk_events if e.get('trigger')=='daemon_crash']
 guard_fail=guard_fail+guard_crash
 guard_fail_closed=bool(guard_fail) or guard_state_fail
 plan_syms={p.get('sym') for p in (ledger.get('plans',{}).get(day,{}).get('picks') or []) if isinstance(p,dict)}
 offplan_today=[]
 for f in fills:
  if f.get('side')!='buy':continue
  pm=f.get('plan_match') or {}
  _in=pm.get('in_plan')
  if _in is None:_in=f.get('sym') in plan_syms
  if _in is False or str(f.get('plan_ref','')).startswith('offplan'):offplan_today.append(f.get('sym'))
 delivery=BASE/'outputs'/'notifications'/f'delivery_{day.replace("-","")}.jsonl';deliveries=[]
 if delivery.exists():
  for line in delivery.read_text(encoding='utf-8').splitlines():
   try:deliveries.append(json.loads(line))
   except Exception:pass
 continuity_checks,continuity_stats=continuity(day)
 next_plan_ok=any((lambda p: p.stem[:10]>day and read_json(p).get('date')==p.stem[:10] and bool(read_json(p).get('picks')))(p) for p in next_plans)
 rebuild_ok=rebuild_products_ok(day)
 # R3.2 裁量层健康度（滚动 7 天 schema 通过率；阈值 20%）
 llm_ok,llm_detail=llm_health(day)
 # ---- 输入完整性枚举（missing != fail） ----
 inputs={
  'preflight_infra':'ok' if infra else 'missing',
  'preflight_post_plan':'ok' if post else 'missing',
  'live_tick':'ok' if live else 'missing',
  'auction_latest':'ok' if auction_latest else 'missing',
  'auction_freeze':'ok' if auction_freeze else 'missing',
  'tick_snapshot':'ok' if tick_snapshot else 'missing',
  'risk_events':'ok' if risk_events_path.exists() else 'missing',
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
  # P0 硬性项(2026-09-04): 收盘新鲜度(进程存在≠数据更新) + 看门狗未耗尽 + 无计划外成交
  'tick_snapshot':bool(tick_snapshot and tick_snapshot.get('date')==day and str(tick_snapshot.get('time',''))>='14:55'),
  'tick_watchdog':not guard_fail_closed,
  'offplan_fills':not offplan_today,
  'ledger_mode':ledger.get('policy',{}).get('account_mode')=='autonomous_paper',
  'ledger_start':ledger.get('start_date')==EXPECTED_LEDGER_START,
  'next_plan':next_plan_ok,
  'rebuild_products':rebuild_ok,
  # R3.2: LLM 裁量层 schema 通过率（滚动 7 天，阈值 20%）—— 降级率过高即"当日评级"不通过
  'llm_health':llm_ok,
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
 'llm_health':llm_detail,
  'ledger':{'revision':ledger.get('_revision'),'cash':ledger.get('account',{}).get('cash'),'start_date':ledger.get('start_date'),'expected_start':EXPECTED_LEDGER_START,'positions':len(ledger.get('account',{}).get('positions',{})),'fills_today':len(fills),'risk_state':ledger.get('risk_state',{})},
  'deliveries':[{'kind':x.get('kind'),'event_key':x.get('event_key'),'ok':x.get('ok'),'message_sha256':x.get('message_sha256')} for x in deliveries],
  'continuity_checks':continuity_checks,'continuity_stats':continuity_stats,'auction_latest':auction_latest,'auction_freeze':auction_freeze,'tick_snapshot':tick_snapshot,'live_tick':live,'next_plan':next_plans[-1].name if next_plans else None,
  'tick_watchdog':{'restart_events':guard_restarts,'fail_events':guard_fail,
                   'state_fail':guard_state_fail,
                   'state':{k:guard_state.get(k) for k in ('state','daemon_alive','tick_fresh','degraded') if k in guard_state}},'offplan_fills_today':offplan_today}
 atomic(path,report)
 print(json.dumps({'status':status,'final':is_final,'probe_reason':probe_reason,'missing_inputs':missing_inputs,'path':str(path)},ensure_ascii=False))
 if probe_reason:return 3
 if status=='incomplete':return 3
 return 0 if status=='pass' else 2
if __name__=='__main__':raise SystemExit(main())
