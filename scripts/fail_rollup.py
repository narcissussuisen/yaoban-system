"""EvoAlpha 近期 FAIL 跨日整合汇总 v1（2026-09-10）。

把一段时间窗内（默认近交易日）的全部失败信号跨日聚合、去重、归类、定态，
作为人工整合排查（docs/FAIL_RCA_*.md）的机器证据底稿。

设计要点：
- **复用既有 reader**：直接 import log_error_digest（led）并调用其
  read_acceptance / read_chain / read_delivery_failures / read_risk_events / read_preflight /
  read_streak 与 RC_HINTS / CHECK_HINTS / FAILURE_KEY_HINTS，不重复实现。
- **修正 BOM 漏采**：task_logs/<date>/*.json 带 UTF-8 BOM，led._json() 用 encoding='utf-8'
  读会抛 JSONDecodeError 并静默返回 None，导致 led.read_task_failures() 恒为空
  （即 v1 复盘整体丢弃「任务失败」错误源）。本脚本用 utf-8-sig 读，并**同时调用 led 版本
  输出差值** —— 该差值就是漏采量的机械证据。
- **只读**：不调用 led.main()、不写 iteration_proposals/、不推送、不联网。
  唯一副产物是本脚本自己的 fail_rollup_<from>_to_<to>.md（可选 .json）。

覆盖 8 类错误源：任务失败 / 验收检查项 / 验收任务 / 盘后链 stage / 失败推送 /
风控事件 / preflight 门禁 / 晨检 / 看门狗隔离态，外加工具缺陷（漏采差值）。

用法：
  python -X utf8 scripts/fail_rollup.py --from 2026-09-03 --to 2026-09-10 [--json] [--out PATH]
"""
import argparse
import datetime
import json
import pathlib
import re
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / "outputs"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import log_error_digest as led  # noqa: E402

# 退出码 -> 归类（rc 语义见 led.RC_HINTS）
RC_CLASS = {
 20: "门禁传播", 21: "门禁传播", 22: "门禁传播", 23: "门禁传播",
 6: "风控联动", 4: "数据不完整", 2: "数据/计划缺失",
 1: "链路失败", 3: "链路失败", 7: "链路失败", -1: "异常退出（无退出码）",
}

WEEKEND = {5, 6}


def _read_text(p):
 raw = p.read_bytes()
 for enc in ("utf-8-sig", "gbk", "latin-1"):
  try:
   return raw.decode(enc)
  except UnicodeDecodeError:
   continue
 return raw.decode("utf-8", "replace")


def _json_sig(p):
 """BOM 安全读取（这是相对 led._json 的关键修正）。"""
 try:
  return json.loads(_read_text(p))
 except Exception:
  return None


def bom_files(day):
 """当日 task_logs json 中带 UTF-8 BOM 的个数（根因事实，与任何 reader 实现无关）。"""
 n = 0
 d = OUT / "task_logs" / day
 if not d.exists():
  return 0
 for fj in d.glob("*.json"):
  try:
   if fj.read_bytes()[:3] == b"\xef\xbb\xbf":
    n += 1
  except OSError:
   continue
 return n


def v1_task_failures(day):
 """**故意模拟 v1 的读法**（encoding='utf-8' 严格解析），返回 (组数, 次数)。

 不调用 led.read_task_failures()：v1 修好之后那个调用会返回正确结果，
 本表就会自我消失、无法复现历史。这里固定按 v1 原文行为度量，保证结论长期可复现。
 """
 groups = set()
 n = 0
 d = OUT / "task_logs" / day
 if not d.exists():
  return 0, 0
 for fj in sorted(d.glob("*.json")):
  try:
   j = json.loads(fj.read_text(encoding="utf-8"))  # v1 原文：遇 BOM 抛 JSONDecodeError
  except Exception:
   continue  # v1 的 _json() 在此 except 返回 None -> 整个文件被跳过
  rc = j.get("exit_code")
  if rc in (None, 0, "0"):
   continue
  n += 1
  groups.add((str(j.get("mode") or "?"), rc))
 return len(groups), n


def task_runs(day):
 """当日全部任务运行（BOM 安全）。"""
 d = OUT / "task_logs" / day
 if not d.exists():
  return []
 runs = []
 for fj in sorted(d.glob("*.json")):
  j = _json_sig(fj)
  if not j:
   continue
  runs.append(j)
 return runs


def task_failures_bom(day):
 """任务失败聚合（mode, rc）-> 次数 / 首末 / stderr 样本。"""
 agg = {}
 for j in task_runs(day):
  rc = j.get("exit_code")
  if rc in (None, 0, "0"):
   continue
  try:
   rc = int(rc)
  except (TypeError, ValueError):
   continue
  mode = str(j.get("mode") or "?")
  k = (mode, rc)
  st = agg.setdefault(k, {"n": 0, "first": None, "last": None, "stderr": ""})
  st["n"] += 1
  ts = str(j.get("finished_at") or j.get("started_at") or "")
  st["first"] = st["first"] or ts
  st["last"] = ts
 return agg


def norm_push_key(event_key):
 """把带日期的推送键归一为跨日可比：monitor-gap:2026-09-10:scan:1023 -> monitor-gap:scan"""
 s = str(event_key or "")
 s = re.sub(r":\d{4}-\d{2}-\d{2}", "", s)
 s = re.sub(r":\d{4}$", "", s)
 return s


def collect_day(day):
 """收集单日全部失败信号 -> 规范记录列表。"""
 recs = []
 runs = task_runs(day)
 total = len(runs)
 nz = 0

 for (mode, rc), st in task_failures_bom(day).items():
  nz += st["n"]
  hint = led.RC_HINTS.get(rc, "")
  recs.append({
   "date": day, "key": f"task:{mode}:rc{rc}", "source": "任务失败",
   "cls": RC_CLASS.get(rc, "其他"), "n": st["n"],
   "detail": f"mode={mode} rc={rc} × {st['n']}" + (f" | {hint}" if hint else ""),
   "first": st["first"], "last": st["last"],
   "evidence": f"outputs/task_logs/{day}/（*.json exit_code={rc}）",
  })

 # 漏采差值（工具缺陷证据）—— 用 v1_task_failures 固定模拟 v1 读法，见其 docstring
 v1_groups, v1_n = v1_task_failures(day)
 bom_n = bom_files(day)
 recs.append({
  "date": day, "key": "tool:digest-bom", "source": "工具缺陷",
  "cls": "工具缺陷", "n": max(0, nz - v1_n),
  "detail": f"按 v1 读法(encoding='utf-8')仅可读 {v1_groups} 组 / {v1_n} 次；实际非零 rc {nz} 次 "
            f"→ 漏采 {max(0, nz - v1_n)} 次；当日带 BOM 的任务 json {bom_n} 个",
  "first": "", "last": "",
  "evidence": "scripts/log_error_digest.py · _json() 原 encoding='utf-8'（2026-09-10 已修为 utf-8-sig）",
  "v1_n": v1_n, "bom_n": bom_n,
 })

 acc = led.read_acceptance(day)
 if acc:
  for c in acc["failed_checks"]:
   streak = led.read_streak(day, c)
   extra = f"（连续 {streak} 日）" if streak >= 2 else ""
   recs.append({
    "date": day, "key": f"accept:{c}", "source": "验收检查项", "cls": "验收硬性项",
    "n": 1, "detail": f"验收检查项 {c} 未通过{extra}",
    "first": "", "last": "",
    "evidence": f"outputs/acceptance/acceptance_{day}.json · checks.{c}=false",
   })
  for t, rc in (acc.get("task_fail") or {}).items():
   rc_i = None
   try:
    rc_i = int(rc)
   except (TypeError, ValueError):
    pass
   recs.append({
    "date": day, "key": f"accept_task:{t}", "source": "验收任务", "cls": "验收硬性项",
    "n": 1, "detail": f"计划任务 {t} LastResult={rc}" + (f" | {led.RC_HINTS.get(rc_i, '')}" if rc_i in led.RC_HINTS else ""),
    "first": "", "last": "",
    "evidence": f"outputs/acceptance/acceptance_{day}.json · task_checks.{t}=false",
   })

 for st in led.read_chain(day):
  recs.append({
   "date": day, "key": f"chain:{st['stage']}", "source": "盘后链", "cls": "盘后链",
   "n": 1, "detail": f"chain stage {st['stage']} status={st['status']} exit_code={st.get('exit_code')}"
                     + (f" skipped_due_to={st.get('skipped_due_to')}" if st.get("skipped_due_to") else ""),
   "first": "", "last": "",
   "evidence": f"outputs/acceptance/chain_manifest_{day}.jsonl",
  })

 for d in led.read_delivery_failures(day):
  nk = norm_push_key(d.get("event_key"))
  hint = ""
  for k, h in led.FAILURE_KEY_HINTS:
   if k in nk:
    hint = h
  recs.append({
   "date": day, "key": f"push:{nk}", "source": "失败推送", "cls": "失败告警",
   "n": 1, "detail": f"失败告警推送 {d.get('event_key')} @ {d.get('time')}" + (f" | {hint}" if hint else ""),
   "first": "", "last": "",
   "evidence": f"outputs/notifications/delivery_{day.replace('-','')}.jsonl · kind=failure",
  })

 for r in led.read_risk_events(day):
  act = r.get("action")
  if act in ("halt", "block", "watch_limit"):
   rule = r.get("rule") or r.get("label") or "?"
   recs.append({
    "date": day, "key": f"risk:{rule}:{act}", "source": "风控事件", "cls": "风控",
    "n": 1, "detail": f"{rule} action={act} {str(r.get('detail') or '')[:60]}",
    "first": "", "last": "outputs/intraday/risk_events.jsonl",
    "evidence": "outputs/intraday/risk_events.jsonl",
   })

 for p in led.read_preflight(day):
  if not p.get("critical"):
   continue
  recs.append({
   "date": day, "key": f"gate:{p['stage']}:{p['name']}", "source": "preflight 门禁", "cls": "门禁",
   "n": 1, "detail": f"{p['stage']}/{p['name']}: {p['detail']}",
   "first": "", "last": "",
   "evidence": f"outputs/preflight_{day}_{p['stage']}.json（会被重跑覆盖，历史态见 selfcheck/）",
  })

 mc = _json_sig(OUT / "validation" / f"morning_check_{day}.json")
 if mc and not ((mc.get("summary") or {}).get("pass")):
  recs.append({
   "date": day, "key": "morning:fail", "source": "晨检", "cls": "晨检", "n": 1,
   "detail": "晨检未通过 " + json.dumps(mc.get("summary"), ensure_ascii=False),
   "first": "", "last": "",
   "evidence": f"outputs/validation/morning_check_{day}.json",
  })

 # 看门狗隔离态：正确路径是 outputs/intraday/（led L377 误写 portfolio/）
 # 2026-09-11: 判据扩展到双信号 —— state=restart_exhausted 只是其中一种;
 # daemon_alive=False 或 tick_fresh=False 同样属守护失败(旧判据会漏掉)。
 gs = _json_sig(OUT / "intraday" / "tick_guard_state.json")
 if gs and str(gs.get("date")) == day and (
         gs.get("state") == "restart_exhausted"
         or gs.get("degraded") is True
         or gs.get("daemon_alive") is False
         or gs.get("tick_fresh") is False):
  recs.append({
   "date": day, "key": "guard:exhausted", "source": "看门狗", "cls": "看门狗", "n": 1,
   "detail": (f"tick 看门狗守护异常 state={gs.get('state')} "
              f"daemon_alive={gs.get('daemon_alive')} tick_fresh={gs.get('tick_fresh')}"),
   "first": "", "last": "",
   "evidence": "outputs/intraday/tick_guard_state.json",
  })

 return {"date": day, "runs": total, "nonzero": nz, "recs": recs,
         "accept": (acc or {}).get("status"), "morning": (mc or {}).get("summary", {}).get("pass") if mc else None}


def date_range(a, b):
 d0 = datetime.date.fromisoformat(a)
 d1 = datetime.date.fromisoformat(b)
 out = []
 d = d0
 while d <= d1:
  out.append(d.isoformat())
  d += datetime.timedelta(days=1)
 return out


def status_of(dates, to_day):
 last = max(dates)
 if last == to_day:
  return "持续"
 gap = (datetime.date.fromisoformat(to_day) - datetime.date.fromisoformat(last)).days
 return f"已消失（最后出现 {last}，距今 {gap} 天）"


def trend_of(dates):
 ds = sorted(dates)
 idx = [datetime.date.fromisoformat(x).toordinal() for x in ds]
 gaps = [idx[i + 1] - idx[i] for i in range(len(idx) - 1)]
 if any(g > 1 for g in gaps):
  return "间歇/回归"
 if len(ds) == 1:
  return "单日"
 return "连续"


def render(day_stats, groups, frm, to):
 L = []
 L.append(f"# EvoAlpha 近期 FAIL 整合汇总 · {frm} ~ {to}")
 L.append("")
 L.append(f"> 自动生成（fail_rollup.py v1）· {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
 L.append("> 复用 log_error_digest 的 reader；任务失败一栏用 utf-8-sig 修正了 v1 的 BOM 漏采。")
 L.append("")

 L.append("## 逐日概览")
 L.append("")
 L.append("| 日期 | 任务运行 | 非零 rc | 时段验收 | 晨检 | 失败信号条数 |")
 L.append("|---|---|---|---|---|---|")
 for s in day_stats:
  if s is None:
   L.append(f"| — | — | — | — | — | （非交易日 / 无数据） |")
   continue
  L.append(f"| {s['date']} | {s['runs']} | {s['nonzero']} | {s['accept']} | {s['morning']} | {len(s['recs'])} |")
 L.append("")

 L.append("## 工具缺陷：v1 复盘的漏采量（机械证据）")
 L.append("")
 L.append("「按 v1 读法」= 固定用 `encoding='utf-8'` 严格解析（即 `log_error_digest._json()` 的原文行为），"
          "**不调用其当前实现**，因此本表在 v1 被修好之后仍能复现历史事实。")
 L.append("")
 L.append("| 日期 | 实际非零 rc 次数 | 按 v1 读法能读到的次数 | 漏采 | 当日带 BOM 的任务 json |")
 L.append("|---|---|---|---|---|")
 tot_miss = 0
 for s in day_stats:
  if s is None:
   continue
  bom = [r for r in s["recs"] if r["key"] == "tool:digest-bom"]
  rec = bom[0] if bom else {}
  miss = rec.get("n", 0)
  tot_miss += miss
  L.append(f"| {s['date']} | {s['nonzero']} | {rec.get('v1_n', 0)} | **{miss}** | {rec.get('bom_n', 0)} |")
 L.append(f"| **合计** | | | **{tot_miss}** | |")
 L.append("")

 L.append("## 跨日 FAIL 分组（按根因键聚合）")
 L.append("")
 L.append("| 键 | 来源 | 归类 | 出现天数 | 总次数 | 首次 | 末次 | 趋势 | 状态 |")
 L.append("|---|---|---|---|---|---|---|---|---|")
 for g in groups:
  L.append(f"| `{g['key']}` | {g['source']} | {g['cls']} | {len(g['dates'])} | {g['n']} | {min(g['dates'])} | {max(g['dates'])} | {g['trend']} | {g['status']} |")
 L.append("")

 L.append("## 明细")
 L.append("")
 for g in groups:
  L.append(f"### `{g['key']}` · {g['cls']} · {g['trend']} · {g['status']}")
  L.append("")
  L.append(f"- 出现日期：{', '.join(sorted(g['dates']))}")
  L.append(f"- 累计次数：{g['n']}")
  for ex in g["examples"][:3]:
   L.append(f"- {ex['date']}：{ex['detail'][:200]}")
  L.append(f"- 证据：`{g['evidence']}`")
  L.append("")
 return "\n".join(L)


def main():
 ap = argparse.ArgumentParser()
 ap.add_argument("--from", dest="frm", required=True)
 ap.add_argument("--to", dest="to", required=True)
 ap.add_argument("--json", action="store_true")
 ap.add_argument("--out", default="")
 a = ap.parse_args()

 day_stats = []
 all_recs = []
 skipped = []
 for day in date_range(a.frm, a.to):
  wd = datetime.date.fromisoformat(day).weekday()
  if wd in WEEKEND:
   skipped.append(day)
   day_stats.append(None)
   continue
  if not (OUT / "task_logs" / day).exists():
   skipped.append(day)
   day_stats.append(None)
   continue
  s = collect_day(day)
  day_stats.append(s)
  all_recs.extend(s["recs"])

 groups = {}
 for r in all_recs:
  if r["key"] == "tool:digest-bom" and r["n"] == 0:
   continue
  g = groups.setdefault(r["key"], {"key": r["key"], "source": r["source"], "cls": r["cls"],
                                   "dates": set(), "n": 0, "examples": [], "evidence": r["evidence"]})
  g["dates"].add(r["date"])
  g["n"] += r["n"]
  g["examples"].append(r)
 for g in groups.values():
  g["status"] = status_of(g["dates"], a.to)
  g["trend"] = trend_of(g["dates"])
  g["dates"] = sorted(g["dates"])
 order = {"门禁": 0, "门禁传播": 1, "风控联动": 2, "验收硬性项": 3, "盘后链": 4, "晨检": 5,
          "监控数据缺口": 6, "数据不完整": 7, "数据/计划缺失": 8, "风控": 9, "看门狗": 10,
          "链路失败": 11, "异常退出（无退出码）": 12, "失败告警": 13, "工具缺陷": 14}
 gl = sorted(groups.values(), key=lambda x: (order.get(x["cls"], 99), -len(x["dates"]), -x["n"]))

 text = render(day_stats, gl, a.frm, a.to)
 out = pathlib.Path(a.out) if a.out else (OUT / "reviews" / f"fail_rollup_{a.frm}_to_{a.to}.md")
 out.parent.mkdir(parents=True, exist_ok=True)
 out.write_text(text, encoding="utf-8")
 print(f"wrote {out}  ({len(gl)} groups, {len(all_recs)} records, skipped={skipped})")

 if a.json:
  jp = out.with_suffix(".json")
  jp.write_text(json.dumps({"from": a.frm, "to": a.to, "skipped_days": skipped,
                            "day_stats": [s for s in day_stats if s],
                            "groups": gl, "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                           ensure_ascii=False, indent=1), encoding="utf-8")
  print(f"wrote {jp}")
 return 0


if __name__ == "__main__":
 sys.exit(main())
