"""Send deduplicated intraday risk and simulated-fill events to Feishu."""
from __future__ import annotations
import argparse, json, os, pathlib, sys
from datetime import datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from feishu_notify import send_text
BASE=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "portfolio"))
from ledger import LEDGER  # noqa: E402  env-aware (EVOALPHA_LEDGER) — R0.2 单一真相源

def _json(path, default):
 try:return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default
 except Exception:return default

def _risk_rows(day):
 rows=[]
 # 2026-09-11 降噪: 看门狗**生命周期**事件不进逐条告警流。
 # 9/11 实录: 一次盘中事故产生 7 条 watch_restart("重启 #1..#7")推给用户, 其中仅 1 条有信息量,
 # 却与真故障混在同一通道 —— 会训练用户忽略真警。这些事件已在 risk_events.jsonl /
 # tick_guard_state.json 留痕, 且真正的失败有独立通道: watch_limit→feishu_alert、
 # daemon_crash→验收 tick_watchdog、monitor-gap→监控中断卡。
 NOISE_TRIGGERS={'watch_start','watch_restart','watch_exit','watch_recovered','duplicate_watcher'}
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
   if row.get('trigger') in NOISE_TRIGGERS:continue
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

def checked_windows(hm):
 """各模块当前是否处于检查窗口内。缺口检测与"恢复"判定必须共用本表, 否则两处窗口会漂移。

 2026-09-11 修复(假恢复): monitoring_gaps() 只在窗口内产出 gaps, 窗口外返回空表; 而
 notify_monitoring_health 的恢复判定用 set(old)-set(new) —— 窗口一关就把**所有**活跃缺口
 宣告"已恢复", 与底层是否真的恢复无关。实录: tick 自 10:37 死到收盘, 却在 11:31(午休窗口外)
 与 15:01(15:00 后窗口外)各推一条 monitor-recovered, monitor_health 的 active 被清成 {}。
 同类假恢复 9/1、9/3 亦有记载(INC-2026-09-01-02 §3 改进 3), 属反复复发缺陷。
 """
 return {
  'auction':'09:20'<=hm<='09:29',
  'scan':('09:40'<=hm<='11:30') or ('13:10'<=hm<='15:00'),
  'monitor':('09:40'<=hm<='11:30') or ('13:10'<=hm<='15:00'),
  'tick':('09:35'<=hm<='11:30') or ('13:05'<=hm<='15:00'),
 }

def monitoring_gaps(day,now):
 hm=now.strftime('%H:%M');gaps={};win=checked_windows(hm)
 if win['auction']:
  ts=_latest_success(day,'auction')
  if ts is None or (now-ts).total_seconds()>300:gaps['auction']='集合竞价观察最近5分钟无成功记录'
 if win['scan']:
  for mode,label in (('scan','全市场扫描'),('monitor','候选/持仓规则监控')):
   ts=_latest_success(day,mode)
   if ts is None or (now-ts).total_seconds()>900:gaps[mode]=label+'最近15分钟无成功记录'
 if win['tick']:
  ts=_tick_time(day)
  if ts is None or (now-ts).total_seconds()>120:gaps['tick']='持仓tick最近2分钟无新鲜快照'
 return gaps

def notify_monitoring_health(day,now):
 path=BASE/'outputs'/'notifications'/f'monitor_health_{day}.json';state=_json(path,{'active':{}});old=state.get('active',{});new=monitoring_gaps(day,now);committed=dict(old);sent=failed=0
 for mode,detail in new.items():
  if mode in old:continue
  key=f'monitor-gap:{day}:{mode}:{now:%H%M}'
  text=f'EvoAlpha｜监控中断 {day} {now:%H:%M:%S}\n模块：{mode}\n详情：{detail}\n状态：禁止依赖该模块产生新仓，等待恢复。'
  if send_text(text,event_key=key,kind='failure'):sent+=1;committed[mode]=detail
  else:failed+=1
 # 2026-09-11 修复: 只有"此刻仍在被检查"的模块才可被判为恢复(见 checked_windows); 窗口外一律
 # 保持 active 不清除 —— 宁可留一条未决状态到收盘, 也不发假恢复。跨日由 monitor_health_{day}.json
 # 的按日分文件自然承载, 不会残留到次日。
 recovered_win=checked_windows(now.strftime('%H:%M'))
 for mode in set(old)-set(new):
  if not recovered_win.get(mode):continue
  key=f'monitor-recovered:{day}:{mode}:{now:%H%M}'
  text=f'EvoAlpha｜监控恢复 {day} {now:%H:%M:%S}\n模块：{mode}\n状态：数据连续性已恢复，交易仍服从其他门禁。'
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
  text=f"EvoAlpha｜集合竞价冻结 {freeze.get('frozen_at','')}\n关注标的：{names}\n全市场活跃样本：{len(freeze.get('active_top',[]))}只\n状态：仅观察，不在集合竞价阶段生成模拟订单。"
  if send_text(text,event_key=f'auction-freeze:{a.date}',kind='auction'):sent+=1
  else:failed+=1
 for row in _risk_rows(a.date):
  key=f"alert:{a.date}:{row['sym']}:{row['rule']}:{row['time']}:{row['action']}"
  detail=f"\n详情：{row['detail']}" if row.get('detail') else ''
  text=f"EvoAlpha｜盘中重大事件 {a.date} {row['time']}\n标的：{row['sym']}\n事件：{row['label']}\n规则：{row['rule']}\n执行状态：{row['action']}{detail}\n说明：仅为自主模拟盘事件，不涉及真实资金。"
  if send_text(text,event_key=key,kind='alert'):sent+=1
  else:failed+=1
 ledger=_json(LEDGER,{})
 # 2026-09-15 缺陷修复: 原硬编码「仅为10万元自主模拟盘记录」, 主账本迁移至 50 万后未同步。
 # 本金一律从账本 start_cash 派生(与 feishu_notify._capital_label 同口径); 读不到就不写数字。
 try:_cap=float(ledger.get('start_cash') or 0)
 except (TypeError,ValueError):_cap=0
 cap_txt=f'{_cap/10000:g}万元' if _cap>0 else ''
 for fill in ledger.get('account',{}).get('fills',[]):
  if fill.get('date')!=a.date:continue
  key=f"fill:{fill.get('ts')}:{fill.get('sym')}:{fill.get('side')}:{fill.get('qty')}:{fill.get('px')}"
  side='买入' if fill.get('side')=='buy' else '卖出'
  text=f"EvoAlpha｜模拟成交 {fill.get('ts')}\n{side} {fill.get('sym')} {fill.get('qty')}股，模拟成交价 {fill.get('px')}\n触发规则：{fill.get('reason','未标注')}\n说明：仅为{cap_txt}自主模拟盘记录，不涉及真实资金。"
  if send_text(text,event_key=key,kind='fill'):sent+=1
  else:failed+=1
 print(json.dumps({'sent_or_seen':sent,'failed':failed},ensure_ascii=False));return 2 if failed else 0
if __name__=='__main__':raise SystemExit(main())
