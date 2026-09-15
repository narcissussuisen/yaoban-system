"""EvoAlpha 单日日志时间线还原 v1（2026-09-10）。

把某一天从盘前到盘后链的全部信号源合并成一条可追溯的分钟级时间线，
每个事件挂证据路径，用于事故复盘与门禁传播分析。

输入（全部本地只读，无网络）：
- outputs/task_logs/<date>/*.json                  每次任务运行的 mode/started_at/finished_at/exit_code
- outputs/notifications/delivery_<YYYYMMDD>.jsonl  全部推送审计（failure/alert/fill/close/review/selfcheck）
- outputs/intraday/risk_events.jsonl               风控与看门狗事件
- portfolio/ledger.json                            account.fills 当日成交（资金真相源）
- outputs/selfcheck/<date>_*.md                    每次自检快照（不可变轨迹，可还原被覆盖的门禁历史态）
- outputs/preflight_<date>_{infra,post_plan}.json  末次自检快照（注意：会被重跑覆盖）
- outputs/acceptance/chain_manifest_<date>.jsonl   盘后链 stage（每 stage 取 attempt_no 最大行）
- outputs/acceptance/acceptance_<date>.json        日终验收
- outputs/validation/morning_check_<date>.json     晨检
- outputs/validation/tdx_state.json                数据源降级状态
- outputs/intraday/tick_guard_state.json           看门狗状态
- outputs/_tick_watch_<YYYYMMDD>.{stdout,stderr}.log  看门狗输出
- outputs/premarket_<date>.log、outputs/intraday/*_<date>.log  盘中产物
- outputs/reviews/log_review_<date>.md             当日复盘（交叉引用）

输出：outputs/reviews/timeline_<date>.md（**只写这一个文件**）。

编码注意（重要）：task_logs/*.json 带 UTF-8 BOM；同一目录下 .stderr.log 有的是 UTF-8、有的是 GBK；
selfcheck/anomalies.log 是 GBK。因此统一用 utf-8-sig -> gbk -> latin-1 逐级回退解码，
不做单编码假设。

边界：只读既有产物，不写任何生产文件、不推送、不联网；缺失源记为「待补」而非报错。

用法：python -X utf8 scripts/day_timeline.py [--date 2026-09-10] [--out PATH]
"""
import argparse
import datetime
import json
import pathlib
import statistics
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "portfolio"))
from ledger import LEDGER  # noqa: E402  env-aware (EVOALPHA_LEDGER) — R0.2 单一真相源
try:
    _LEDGER_LABEL = str(LEDGER.relative_to(BASE))
except ValueError:
    _LEDGER_LABEL = str(LEDGER)
OUT = BASE / "outputs"

try:  # 复用既有退出码语义表，避免重复定义
 sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
 from log_error_digest import RC_HINTS
except Exception:
 RC_HINTS = {}

CAT_ORDER = ["门禁", "任务", "自检", "推送", "成交", "风控", "守护", "数据源", "盘后链", "产物", "复盘"]


def _read_text(p):
 """utf-8-sig -> gbk -> latin-1 逐级回退（仓库内编码混杂，见模块 docstring）。"""
 raw = p.read_bytes()
 for enc in ("utf-8-sig", "gbk", "latin-1"):
  try:
   return raw.decode(enc)
  except UnicodeDecodeError:
   continue
 return raw.decode("utf-8", "replace")


def _json(p):
 try:
  return json.loads(_read_text(p))
 except Exception:
  return None


def _jsonl(p):
 rows = []
 if not p.exists():
  return rows
 for line in _read_text(p).splitlines():
  line = line.strip()
  if not line:
   continue
  try:
   rows.append(json.loads(line))
  except Exception:
   continue
 return rows


def _ts(v):
 """兼容 '20260910_152104_697' 与 '2026-09-10 15:21:06.766' 两种时间戳。"""
 s = str(v or "").strip()
 if not s:
  return None
 if len(s) >= 19 and s[4] == "-":
  try:
   return datetime.datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
  except ValueError:
   pass
  try:
   return datetime.datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
  except ValueError:
   return None
 if len(s) >= 15 and s[8] == "_":
  try:
   return datetime.datetime.strptime(s[:15], "%Y%m%d_%H%M%S")
  except ValueError:
   return None
 return None


def _hm(dt):
 return dt.strftime("%H:%M:%S") if dt else "--:--:--"


def _hm_day(dt, day):
 """当日事件只显示时刻；跨日事件带 MM-DD 前缀，避免与当日时刻混淆。"""
 if not dt:
  return "--:--:--"
 return dt.strftime("%H:%M:%S") if dt.strftime("%Y-%m-%d") == day else dt.strftime("%m-%d %H:%M:%S")


class Day:
 """一天的全部证据与派生视图。"""

 def __init__(self, day):
  self.day = day
  self.dot = day.replace("-", "")
  self.events = []      # (dt, category, text, evidence)
  self.missing = []
  self.runs = []        # 任务运行记录
  self.gates = []       # 自检快照
  self.pushes = []
  self.risk = []
  self.fills = []
  self.chain = []
  self.acc = None
  self.morning = None
  self.tdx = None
  self.guard = None
  self.artifacts = []
  self.review = None

 def add(self, dt, cat, text, evidence=""):
  if dt is None:
   return
  self.events.append((dt, cat, text, evidence))


def load_tasks(d):
 p = OUT / "task_logs" / d.day
 if not p.exists():
  d.missing.append(f"task_logs/{d.day}/（目录不存在，可能休市）")
  return
 for fj in sorted(p.glob("*.json")):
  j = _json(fj)
  if not j:
   continue
  mode = str(j.get("mode") or fj.stem.split("_")[-1])
  rc = j.get("exit_code")
  try:
   rc = int(rc)
  except (TypeError, ValueError):
   rc = None
  fin = _ts(j.get("finished_at"))
  start = _ts(j.get("started_at"))
  run = {"mode": mode, "rc": rc, "start": start, "finish": fin, "stem": fj.stem}
  d.runs.append(run)
  if rc not in (None, 0):
   hint = RC_HINTS.get(rc, "")
   txt = f"任务 {mode} 失败 rc={rc}"
   if hint:
    txt += f"（{hint}）"
   d.add(fin or start, "任务", txt, f"task_logs/{d.day}/{fj.name}")


def load_delivery(d):
 p = OUT / "notifications" / f"delivery_{d.dot}.jsonl"
 if not p.exists():
  d.missing.append(f"notifications/delivery_{d.dot}.jsonl")
  return
 for ev in _jsonl(p):
  t = _ts(ev.get("time"))
  kind = str(ev.get("kind") or "")
  key = str(ev.get("event_key") or "")
  ok = ev.get("ok")
  d.pushes.append(ev)
  d.add(t, "推送", f"[{kind}] {key} ok={ok}", f"notifications/delivery_{d.dot}.jsonl")


def load_risk(d):
 p = OUT / "intraday" / "risk_events.jsonl"
 if not p.exists():
  d.missing.append("intraday/risk_events.jsonl")
  return
 for ev in _jsonl(p):
  if not str(ev.get("date") or "").startswith(d.day):
   continue
  t = _ts(f"{d.day} {ev.get('time')}") if ev.get("time") else None
  rule = ev.get("rule") or ev.get("trigger") or "-"
  sym = ev.get("sym") or "-"
  act = ev.get("action") or "-"
  detail = str(ev.get("detail") or ev.get("blocked_reason") or "")[:60]
  d.risk.append(ev)
  cat = "守护" if str(rule).startswith("tick watch") or "watchdog" in str(detail) else "风控"
  d.add(t, cat, f"{sym} {rule} action={act} {detail}", "intraday/risk_events.jsonl")


def load_fills(d):
 led = _json(LEDGER)
 if not led:
  d.missing.append(_LEDGER_LABEL)
  return
 for f in ((led.get("account") or {}).get("fills") or []):
  if str(f.get("date")) != d.day:
   continue
  t = _ts(f.get("ts")) or _ts(f"{d.day} 15:00:00")
  d.fills.append(f)
  d.add(t, "成交", f"{f.get('side')} {f.get('sym')} {f.get('qty')}股 @{f.get('px')} reason={f.get('reason')}",
        f"{_LEDGER_LABEL} · account.fills")


def load_selfcheck(d):
 p = OUT / "selfcheck"
 if not p.exists():
  d.missing.append("selfcheck/")
  return
 for md in sorted(p.glob(f"{d.day}_*.md")):
  txt = _read_text(md)
  tail = md.stem[len(d.day) + 1:] if md.stem.startswith(d.day) else md.stem
  hhmmss = tail.split("_")[0]
  stamp = None
  if len(hhmmss) == 6 and hhmmss.isdigit():
   stamp = _ts(f"{d.day} {hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}")
  stage = "post_plan" if md.stem.endswith("_post_plan") else "infra"
  fails = []
  for line in txt.splitlines():
   if line.startswith("| ❌ FAIL |"):
    cells = [c.strip() for c in line.strip("|").split("|")]
    if len(cells) >= 3:
     fails.append(f"{cells[1]}:{cells[2][:50]}")
  rec = {"stage": stage, "time": stamp, "file": md.name, "fails": fails}
  d.gates.append(rec)
  if fails:
   d.add(stamp, "门禁", f"自检 {stage} FAIL {len(fails)} 项 → {'; '.join(fails)}", f"selfcheck/{md.name}")


def load_preflight(d):
 for stage in ("infra", "post_plan"):
  p = OUT / f"preflight_{d.day}_{stage}.json"
  if not p.exists():
   d.missing.append(p.name)
   continue
  j = _json(p)
  if not j:
   d.missing.append(p.name + "（解析失败）")
   continue
  t = _ts(j.get("time"))
  bad = [r.get("name") for r in (j.get("results") or []) if not r.get("ok")]
  d.add(t, "门禁", f"preflight {stage} 末次快照 status={j.get('status')} 未过项={bad}",
        f"outputs/{p.name}（会被重跑覆盖，历史态见 selfcheck/）")


def load_chain(d):
 p = OUT / "acceptance" / f"chain_manifest_{d.day}.jsonl"
 if not p.exists():
  d.missing.append(f"acceptance/chain_manifest_{d.day}.jsonl（盘后链未收尾）")
  return
 best = {}
 for row in _jsonl(p):
  st = row.get("stage")
  if not st:
   continue
  cur = best.get(st)
  if cur is None or (row.get("attempt_no") or 0) >= (cur.get("attempt_no") or 0):
   best[st] = row
 for st, row in best.items():
  t = _ts(row.get("finished_at")) or _ts(row.get("started_at"))
  d.chain.append(row)
  txt = f"盘后链 {st} status={row.get('status')} rc={row.get('exit_code')}"
  if row.get("skipped_due_to"):
   txt += f" skipped_due_to={row.get('skipped_due_to')}"
  d.add(t, "盘后链", txt, f"acceptance/chain_manifest_{d.day}.jsonl")


def load_acceptance(d):
 p = OUT / "acceptance" / f"acceptance_{d.day}.json"
 if not p.exists():
  d.missing.append(f"acceptance/acceptance_{d.day}.json（盘后链未收尾）")
  return
 d.acc = _json(p)
 if not d.acc:
  d.missing.append(p.name + "（解析失败）")
  return
 t = _ts(str(d.acc.get("generated_at") or ""))
 bad = [k for k, v in (d.acc.get("checks") or {}).items() if v is False]
 d.add(t, "盘后链", f"日终验收 status={d.acc.get('status')} 未过项={bad}",
       f"acceptance/acceptance_{d.day}.json")


def load_misc(d):
 p = OUT / "validation" / f"morning_check_{d.day}.json"
 if p.exists():
  d.morning = _json(p)
  s = (d.morning or {}).get("summary") or {}
  d.add(_ts(f"{d.day} 08:58:00"), "自检", f"晨检 pass={s.get('pass')} {json.dumps(s, ensure_ascii=False)}",
        f"validation/morning_check_{d.day}.json")
 else:
  d.missing.append(f"validation/morning_check_{d.day}.json")

 q = OUT / "validation" / "tdx_state.json"
 if q.exists():
  d.tdx = _json(q)
  if not isinstance(d.tdx, dict):
   d.tdx = None
   d.missing.append("validation/tdx_state.json（结构异常，非 dict）")
 else:
  d.missing.append("validation/tdx_state.json")
 if d.tdx and d.tdx.get("state") == "down":
  d.add(_ts(d.tdx.get("last_check")), "数据源",
        f"TDX 停供中 state=down since={d.tdx.get('since')} last_check={d.tdx.get('last_check')}",
        "validation/tdx_state.json")

 # tdx_probe_<date>.json 实测是 list（逐服务器探测结果），只作参考记录，不当状态 dict 用
 q = OUT / "validation" / f"tdx_probe_{d.day}.json"
 if q.exists():
  probe = _json(q)
  n = len(probe) if isinstance(probe, (list, dict)) else 0
  d.add(_ts(f"{d.day} 09:00:00"), "数据源", f"TDX 恢复探测记录 {n} 条", f"validation/tdx_probe_{d.day}.json")
 else:
  d.missing.append(f"validation/tdx_probe_{d.day}.json")

 q = OUT / "intraday" / "tick_guard_state.json"
 if q.exists():
  d.guard = _json(q)
  if d.guard:
   # 2026-09-11: 补双信号读数 —— 只显示 state 无法区分"进程死"与"数据路径卡住"
   # (9/11 这正是排障耗时最长的一处)。
   _alive = d.guard.get('daemon_alive')
   _fresh = d.guard.get('tick_fresh')
   _sig = (f" daemon_alive={_alive} tick_fresh={_fresh}"
           f" beat_age={d.guard.get('daemon_beat_age_s')}s tick_age={d.guard.get('tick_age_s')}s"
           if (_alive is not None or _fresh is not None) else "")
   d.add(_ts(f"{d.day} {d.guard.get('time')}"), "守护",
         f"看门狗状态 state={d.guard.get('state')}{_sig} detail={d.guard.get('detail')}",
         "outputs/intraday/tick_guard_state.json")
 else:
  d.missing.append("intraday/tick_guard_state.json")

 for rel in (f"premarket_{d.day}.log", f"intraday/trader_daily_{d.day}.log",
             f"intraday/build_board_{d.day}.log", f"intraday/board_refresh.latest.log",
             f"_tick_watch_{d.dot}.stdout.log", f"_tick_watch_{d.dot}.stderr.log"):
  q = OUT / rel
  if q.exists():
   st = datetime.datetime.fromtimestamp(q.stat().st_mtime)
   d.artifacts.append((rel, st, q.stat().st_size))
  else:
   d.missing.append(rel)

 rp = OUT / "reviews" / f"log_review_{d.day}.md"
 if rp.exists():
  d.review = rp
 else:
  d.missing.append(f"reviews/log_review_{d.day}.md")


def task_gaps(d):
 """同类任务相邻运行的异常间隔（判链路中断窗口）。"""
 by_mode = {}
 for r in d.runs:
  t = r["start"] or r["finish"]
  if t:
   by_mode.setdefault(r["mode"], []).append(t)
 gaps = []
 for mode, ts in by_mode.items():
  ts.sort()
  ivs = [(ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1)]
  if len(ivs) < 3:
   continue
  med = statistics.median(ivs)
  for i, iv in enumerate(ivs):
   if iv > max(180.0, med * 3):
    gaps.append((ts[i], ts[i + 1], mode, iv, med))
 gaps.sort()
 return gaps


def fail_windows(d, max_gap_s=300.0):
 """同类任务的连续失败窗口（按时间序、内部间隔不超过 max_gap_s 的最大连续非零 rc 段）。

 与 task_gaps 互补：门禁拦截期间任务仍在按分钟运行（只是全部失败），
 因此「运行间隔」看不出中断，只有「连续失败」能暴露真实的中断窗口。
 max_gap_s 用于剔除稀疏 mode（tick/premarket/plan-gate 每日仅跑 1-2 次，
 否则会把相隔数小时的三次失败拼成一个假窗口）。
 """
 by_mode = {}
 for r in d.runs:
  t = r["start"] or r["finish"]
  if t:
   by_mode.setdefault(r["mode"], []).append((t, r["rc"]))
 wins = []
 for mode, items in by_mode.items():
  items.sort()
  cur = None
  for t, rc in items:
   if rc not in (None, 0):
    if cur is None:
     cur = {"mode": mode, "start": t, "end": t, "n": 0, "rc": {}, "max_gap": 0.0}
    else:
     cur["max_gap"] = max(cur["max_gap"], (t - cur["_prev"]).total_seconds())
    cur["_prev"] = t
    cur["end"] = t
    cur["n"] += 1
    cur["rc"][rc] = cur["rc"].get(rc, 0) + 1
   elif cur is not None:
    wins.append(cur)
    cur = None
  if cur is not None:
   wins.append(cur)
 wins = [w for w in wins if w["n"] >= 2 and w["max_gap"] <= max_gap_s]
 for w in wins:
  w.pop("_prev", None)
 wins.sort(key=lambda x: (-x["n"], x["start"]))
 return wins


def rc_table(d):
 agg = {}
 for r in d.runs:
  if r["rc"] in (None, 0):
   continue
  k = (r["mode"], r["rc"])
  st = agg.setdefault(k, {"n": 0, "first": None, "last": None})
  st["n"] += 1
  t = r["finish"] or r["start"]
  if t:
   st["first"] = st["first"] or t
   st["last"] = t
 return agg


def render(d):
 L = []
 L.append(f"# EvoAlpha 单日时间线 · {d.day}")
 L.append("")
 L.append(f"> 自动生成（day_timeline.py v1）· {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
 L.append("> 证据全部为本地文件；`preflight_*.json` 会被重跑覆盖，门禁历史态以 `selfcheck/*.md` 为准。")
 L.append("")

 # 数据完整度
 have = len(d.events)
 L.append(f"## 数据完整度：{have} 个事件 · {len(d.missing)} 个源缺失")
 if d.missing:
  for m in d.missing:
   L.append(f"- 待补：`{m}`")
 else:
  L.append("- 全部来源齐备。")
 L.append("")

 # §1 分钟级总表
 L.append("## §1 时间线总表")
 L.append("")
 L.append(f"时刻为 `HH:MM:SS`；带 `MM-DD` 前缀者为跨日事件（如次日早晨才推送的 review、报告生成时的数据源状态）。")
 L.append("")
 L.append("| 时间 | 类别 | 事件 | 证据 |")
 L.append("|---|---|---|---|")
 for dt, cat, text, ev in sorted(d.events, key=lambda x: (x[0], CAT_ORDER.index(x[1]) if x[1] in CAT_ORDER else 99)):
  L.append(f"| {_hm_day(dt, d.day)} | {cat} | {text} | `{ev}` |")
 L.append("")

 # §2 计划 vs 实跑
 L.append("## §2 计划 vs 实跑")
 L.append("")
 L.append("| mode | 运行次数 | 首次 | 末次 | 非零 rc 明细 |")
 L.append("|---|---|---|---|---|")
 modes = {}
 for r in d.runs:
  t = r["start"] or r["finish"]
  m = modes.setdefault(r["mode"], {"n": 0, "first": None, "last": None, "rc": {}})
  m["n"] += 1
  if t:
   m["first"] = m["first"] or t
   m["last"] = t
  if r["rc"] not in (None, 0):
   m["rc"][r["rc"]] = m["rc"].get(r["rc"], 0) + 1
 for mode in sorted(modes):
  m = modes[mode]
  rcs = ", ".join(f"rc{k}×{v}" for k, v in sorted(m["rc"].items())) or "—"
  L.append(f"| {mode} | {m['n']} | {_hm(m['first'])} | {_hm(m['last'])} | {rcs} |")
 L.append("")

 # §3 门禁与失败传播
 L.append("## §3 门禁与失败传播")
 L.append("")
 L.append("| 自检时刻 | 阶段 | FAIL 项 | 证据 |")
 L.append("|---|---|---|---|")
 for g in sorted(d.gates, key=lambda x: x["time"] or datetime.datetime.min):
  L.append(f"| {_hm(g['time'])} | {g['stage']} | {'; '.join(g['fails']) if g['fails'] else '—（无 FAIL）'} | `selfcheck/{g['file']}` |")
 L.append("")
 agg = rc_table(d)
 gate_rc = {k: v for k, v in agg.items() if k[1] in (20, 21, 22, 23)}
 if gate_rc:
  L.append("门禁类退出码（20=gate 缺失 / 21=gate 不合法 / 23=gate 陈旧或盘外时段）：")
  L.append("")
  L.append("| mode | rc | 次数 | 首次 | 末次 |")
  L.append("|---|---|---|---|---|")
  for (mode, rc), v in sorted(gate_rc.items(), key=lambda x: -x[1]["n"]):
   L.append(f"| {mode} | {rc} | {v['n']} | {_hm(v['first'])} | {_hm(v['last'])} |")
  L.append("")
  tot = sum(v["n"] for v in gate_rc.values())
  L.append(f"**合计 {tot} 次任务运行被门禁拦截。**")
  L.append("")

 # §4 链路中断窗口
 gaps = task_gaps(d)
 wins = fail_windows(d)
 L.append("## §4 链路中断窗口")
 L.append("")
 L.append("### 4.1 连续失败窗口（门禁拦截期间任务照跑但全灭，只有此项能暴露真实中断）")
 L.append("")
 if wins:
  L.append("| mode | 起 | 止 | 连续失败次数 | 窗口内最大间隔 | rc 分布 |")
  L.append("|---|---|---|---|---|---|")
  for w in wins[:20]:
   rcs = ", ".join(f"rc{k}×{v}" for k, v in sorted(w["rc"].items()))
   L.append(f"| {w['mode']} | {_hm(w['start'])} | {_hm(w['end'])} | {w['n']} | {w['max_gap']:.0f} 秒 | {rcs} |")
  L.append("")
  L.append("（仅列出连续失败 ≥2 次且窗口内相邻失败间隔 ≤300 秒者；稀疏 mode 的分散失败不计入，避免拼出跨小时假窗口。）")
 else:
  L.append("- 无连续失败。")
 L.append("")
 L.append("### 4.2 运行间隔异常（同类任务相邻运行间隔 > 3 倍中位数且 > 3 分钟）")
 L.append("")
 if gaps:
  L.append("| 起点 | 终点 | mode | 间隔 | 该 mode 中位间隔 |")
  L.append("|---|---|---|---|---|")
  for a, b, mode, iv, med in gaps:
   L.append(f"| {_hm(a)} | {_hm(b)} | {mode} | {iv/60:.1f} 分钟 | {med:.0f} 秒 |")
 else:
  L.append("- 无异常间隔。")
 L.append("")

 # §5 交易与风控
 L.append("## §5 交易与风控事件")
 L.append("")
 if d.fills:
  L.append("| 时间 | 方向 | 标的 | 股数 | 价格 | 金额 | 缘由 |")
  L.append("|---|---|---|---|---|---|---|")
  for f in d.fills:
   qty = f.get("qty") or 0
   px = f.get("px") or 0
   L.append(f"| {_ts(f.get('ts')) and _hm(_ts(f.get('ts'))) or '—'} | {f.get('side')} | {f.get('sym')} | {qty} | {px} | {qty*px:.2f} | {f.get('reason')} |")
 else:
  L.append("- 当日无成交。")
 L.append("")
 if d.risk:
  L.append("风控/守护事件：")
  L.append("")
  L.append("| 时间 | 标的 | 规则 | 动作 | 详情 |")
  L.append("|---|---|---|---|---|")
  for r in d.risk:
   L.append(f"| {r.get('time')} | {r.get('sym')} | {r.get('rule') or r.get('trigger')} | {r.get('action')} | {str(r.get('detail') or r.get('blocked_reason') or '')[:60]} |")
  L.append("")

 # §6 盘后链
 L.append("## §6 盘后链 stage")
 L.append("")
 if d.chain:
  L.append("| stage | 开始 | 结束 | rc | status | skipped_due_to |")
  L.append("|---|---|---|---|---|---|")
  for row in sorted(d.chain, key=lambda x: str(x.get("started_at") or "")):
   L.append(f"| {row.get('stage')} | {row.get('started_at')} | {row.get('finished_at')} | {row.get('exit_code')} | {row.get('status')} | {row.get('skipped_due_to')} |")
 else:
  L.append("- 盘后链尚未收尾（manifest 缺失）。")
 L.append("")

 # §7 验收
 L.append("## §7 日终验收")
 L.append("")
 if d.acc:
  bad = [k for k, v in (d.acc.get("checks") or {}).items() if v is False]
  L.append(f"- status = **{d.acc.get('status')}**")
  L.append(f"- 未过检查项：{bad if bad else '无'}")
  tf = {k: (v if not isinstance(v, bool) else None) for k, v in (d.acc.get("task_checks") or {}).items() if v is False}
  L.append(f"- 失败任务项：{list(tf) if tf else '无'}")
 else:
  L.append("- 待补：`acceptance/acceptance_%s.json`" % d.day)
 L.append("")

 # §8 复盘交叉引用
 L.append("## §8 复盘交叉引用")
 L.append("")
 if d.review:
  L.append(f"- `reviews/log_review_{d.day}.md`（**注意**：log_error_digest v1 因 task_logs/*.json 的 UTF-8 BOM 会整体丢弃「任务失败」错误源，该文件的错误计数偏少）")
 else:
  L.append("- 待补：当日 log_review")
 L.append("")
 return "\n".join(L)


def main():
 ap = argparse.ArgumentParser()
 ap.add_argument("--date", default="")
 ap.add_argument("--out", default="")
 a = ap.parse_args()
 day = a.date or datetime.datetime.now().strftime("%Y-%m-%d")
 d = Day(day)
 load_tasks(d)
 load_delivery(d)
 load_risk(d)
 load_fills(d)
 load_selfcheck(d)
 load_preflight(d)
 load_chain(d)
 load_acceptance(d)
 load_misc(d)
 text = render(d)
 out = pathlib.Path(a.out) if a.out else (OUT / "reviews" / f"timeline_{day}.md")
 out.parent.mkdir(parents=True, exist_ok=True)
 out.write_text(text, encoding="utf-8")
 print(f"wrote {out}  ({len(d.events)} events, {len(d.missing)} missing sources)")
 return 0


if __name__ == "__main__":
 sys.exit(main())
