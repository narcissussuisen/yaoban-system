"""EvoAlpha 每日日志复盘 v1（2026-09-09）。

复盘缺口修复：此前 trader_daily 只看交易统计，任务日志/验收/推送失败中的大量报错信息
无人消费（如 9/9 next_plan baostock 日历失败、live_tick 三日连败）。本脚本聚合当日全部
错误信号源，生成：
- outputs/reviews/log_review_<date>.md    错误摘要（人读）
- outputs/iteration_proposals/<date>.json 迭代提案（合并 trader_daily 已有提案，不覆盖）
- --push 时推送摘要到飞书并写 delivery 审计（kind=review, 幂等 event_key=log-review:<date>）

输入（全部本地；飞书仅为镜像）：
- task_logs/<date>/*.json 任务运行结果（exit_code != 0）+ 同名 .stderr.log 尾部
- outputs/acceptance/acceptance_<date>.json 验收失败检查项
- outputs/acceptance/chain_manifest_<date>.jsonl 盘后链失败/跳过 stage
- outputs/notifications/delivery_<date>.jsonl failure 推送事件
- outputs/intraday/risk_events.jsonl 当日风控事件（halt/watch_limit）
- outputs/preflight_<date>_{infra,post_plan}.json 门禁失败项
- outputs/validation/morning_check_<date>.json 晨检
- portfolio/tick_guard_state.json 看门狗隔离状态
- 前 3 个交易日 acceptance：重复失败升级（streak）

自迭代边界：本脚本只"发现+提案"（进化闭环第一环），不做自动改码；修复由 L-A 会话
按提案执行并走版本化验证。用法：python scripts/log_error_digest.py [--date 2026-09-09] [--push]
"""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / "outputs"
WEBHOOK_FILE = pathlib.Path(os.environ.get(
    "YAOBAN_FEISHU_SECRET_FILE", "C:/Users/YZP/WorkBuddy/yaoban_tasks/feishu_webhook.txt"))

# 任务退出码语义（run_trading_task.ps1 / 各脚本约定，2026-09 生效）
RC_HINTS = {
    2: "数据/计划缺失或校验失败（premarket 计划缺失、Vibe tick 校验失败等）",
    6: "看门狗 degraded（重启额度耗尽/伴随监控失效，安全拒绝新仓）",
    7: "收盘链子任务失败",
    20: "gate 文件缺失（上游门禁未产出，fail-closed）",
    21: "gate 状态/日期/stage 不合法（上游门禁失败传播）",
    22: "未知 mode（任务配置错误）",
    23: "gate 陈旧/超时（上游产物过期，fail-closed）",
}
# 验收检查项 -> 修复方向提示
CHECK_HINTS = {
    "live_tick": "VibeResearchLiveTickValidation 任务失败或 tick 数据陈旧（not_stale=false）；检查 09:35/13:05 触发窗口与 tick 源新鲜度",
    "tick_snapshot": "pos_live 收盘陈旧（需 >=14:55）；tick daemon/watchdog 盘中死过",
    "tick_watchdog": "当日出现 watch_limit 重启耗尽或 data_failure halt；查 risk_events 与 tick_guard_state",
    "offplan_fills": "计划外成交；查 scan 盘中捕捉路径与计划匹配契约",
    "next_plan": "次日计划未生成（常见根因：交易日历 fetch 失败如 baostock 登录错误）；计划缺失会导致次日 PlanGate 连锁拦截",
    "tasks": "计划任务 LastResult != 0（常伴随 live_tick 的 Vibe 任务）",
    "ledger_mode": "账本模式/修订异常",
    "ledger_start": "账本起点校验异常",
    "task_log_continuity": "盘中任务连续性缺口（scan/monitor/notify 中断）",
}
# stderr 错误模式 -> 分类与提示
STDERR_PATTERNS = [
    (re.compile("baostock|登录失败|login fail", re.I), "交易日历源失败", "baostock 日历接口失败时生成次日计划中断；给 fetch_trade_calendar 增加本地交易日历/腾讯源回退"),
    (re.compile("WinError 5|WinError5|PermissionError"), "文件锁/权限冲突", "pos_live 等共享文件撞锁；确认原子写重试（tick_monitor 已修）无回归"),
    (re.compile("ModuleNotFoundError|ImportError"), "依赖缺失", "py_libs/venv 缺包；补齐或改用生产 python env"),
    (re.compile("SyntaxError|Non-UTF-8|UnicodeDecodeError"), "编码/语法破坏", "禁止 PowerShell Get-Content/Set-Content 改含中文 py 文件；py_compile 后上线"),
    (re.compile("timeout|timed out", re.I), "网络超时", "数据源超时；评估重试/降级（TDX 开盘前 WARN 降级先例）"),
    (re.compile("ConnectionError|connect fail|refused", re.I), "连接失败", "数据源不可达；检查服务器清单（TDX 实测可用节点 115.238.56.198/115.238.90.165）"),
]
FAILURE_KEY_HINTS = [
    ("monitor-gap", "监控数据缺口（tick/scan 数据源中断）"),
    ("post-close", "盘后链部分失败（看 chain_manifest 定位 stage）"),
]


def _json(p: pathlib.Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def read_task_failures(day: str) -> list:
    """task_logs/<day>/*.json 中 exit_code!=0 的任务，按 mode+rc 聚合，附 stderr 尾部样本。"""
    d = OUT / "task_logs" / day
    fails = {}
    if not d.exists():
        return []
    for fj in sorted(d.glob("*.json")):
        j = _json(fj)
        if not j:
            continue
        rc = j.get("exit_code")
        if rc in (None, 0, "0"):
            continue
        mode = str(j.get("mode") or fj.stem.split("_")[-1])
        key = (mode, int(rc))
        st = fails.setdefault(key, {"mode": mode, "rc": int(rc), "count": 0, "first": None, "last": None, "stderr_sample": ""})
        st["count"] += 1
        ts = str(j.get("finished_at") or j.get("started_at") or "")
        st["first"] = st["first"] or ts
        st["last"] = ts
        if not st["stderr_sample"]:
            errf = fj.with_name(fj.stem + ".stderr.log")
            if errf.exists():
                try:
                    tail = errf.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-8:]
                    st["stderr_sample"] = chr(10).join(tail)
                except Exception:
                    pass
    return sorted(fails.values(), key=lambda x: (x["rc"], x["mode"]))


def read_acceptance(day: str):
    a = _json(OUT / "acceptance" / f"acceptance_{day}.json")
    if not a:
        return None
    failed = [k for k, v in (a.get("checks") or {}).items() if v is False]
    task_fail = {k: (a.get("tasks") or {}).get(k, {}).get("last_result") for k, v in (a.get("task_checks") or {}).items() if v is False}
    return {"status": a.get("status"), "failed_checks": failed, "task_fail": task_fail,
            "offplan": a.get("offplan_fills_today") or [], "watchdog": a.get("tick_watchdog") or {}}


def read_chain(day: str) -> list:
    """chain_manifest：每 stage 取 attempt 最大行，收集 failed/skipped。"""
    mf = OUT / "acceptance" / f"chain_manifest_{day}.jsonl"
    if not mf.exists():
        return []
    best = {}
    try:
        for line in mf.read_text(encoding="utf-8").splitlines():
            try:
                j = json.loads(line)
            except Exception:
                continue
            st = j.get("stage")
            if not st:
                continue
            cur = best.get(st)
            if cur is None or (j.get("attempt_no") or 0) >= (cur.get("attempt_no") or 0):
                best[st] = j
    except Exception:
        pass
    return [j for j in best.values() if j.get("status") in ("failed", "skipped")]


def read_delivery_failures(day: str) -> list:
    dl = OUT / "notifications" / ("delivery_" + day.replace("-", "") + ".jsonl")
    out = []
    if dl.exists():
        try:
            for line in dl.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("kind") == "failure":
                    out.append({"event_key": ev.get("event_key"), "time": ev.get("time")})
        except Exception:
            pass
    return out


def read_risk_events(day: str) -> list:
    re_f = OUT / "intraday" / "risk_events.jsonl"
    out = []
    if re_f.exists():
        try:
            for line in re_f.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if str(ev.get("date") or ev.get("ts") or "").startswith(day):
                    out.append({k: ev.get(k) for k in ("time", "sym", "rule", "label", "action", "detail")})
        except Exception:
            pass
    return out


def read_preflight(day: str) -> list:
    out = []
    for stage in ("infra", "post_plan"):
        p = _json(OUT / f"preflight_{day}_{stage}.json")
        if not p:
            continue
        for r in p.get("results") or []:
            if not r.get("ok"):
                out.append({"stage": stage, "name": r.get("name"), "detail": str(r.get("detail", ""))[:120],
                            "critical": bool(r.get("critical", True))})
    return out


def read_streak(day: str, check: str, days: int = 3) -> int:
    """同一检查项此前连续失败天数（含当日）。"""
    d0 = datetime.date.fromisoformat(day)
    n = 0
    for i in range(days + 1):
        d = (d0 - datetime.timedelta(days=i)).isoformat()
        a = _json(OUT / "acceptance" / f"acceptance_{d}.json")
        if not a:
            continue
        if (a.get("checks") or {}).get(check) is False:
            n += 1
        elif i > 0:
            break
    return n


def classify(errors: list) -> list:
    """为每条错误附加分类与修复提示 -> 提案文本。"""
    proposals = []
    for e in errors:
        hint = e.get("hint", "")
        if not hint:
            for rx, cat, h in STDERR_PATTERNS:
                if rx.search(e.get("stderr_sample", "") or e.get("evidence", "")):
                    e["category"] = cat
                    hint = h
                    break
        if hint:
            e["hint"] = hint
            proposals.append("[{s}] {m} => {h}".format(s=e["source"], m=e.get("summary", "")[:80], h=hint))
    return proposals


def build_digest(day: str, data: dict) -> str:
    L = []
    L.append(f"# EvoAlpha 日志复盘 · {day}")
    L.append("")
    L.append(f"> 自动生成（log_error_digest.py v1）· {data['generated_at']} · 飞书镜像同步推送（kind=review）")
    L.append("")
    errs = data["errors"]
    L.append(f"## 汇总：{len(errs)} 条错误 / {len(data['warnings'])} 条警告")
    if data["acceptance"]:
        L.append(f"- 日终验收：**{data['acceptance']['status']}**")
    L.append("")
    if not errs:
        L.append("✅ 当日无错误信号。")
        return chr(10).join(L)
    for e in errs:
        L.append(f"## [{e['source']}] {e.get('summary', '')}")
        if e.get("detail"):
            L.append(f"- {e['detail']}")
        if e.get("evidence"):
            L.append(f"- 证据：{e['evidence']}")
        if e.get("category"):
            L.append(f"- 分类：{e['category']}")
        if e.get("hint"):
            L.append(f"- **修复方向**：{e['hint']}")
        L.append("")
    L.append("## 迭代提案（iteration_proposals 已合并）")
    for p in data["proposals"]:
        L.append(f"- {p}")
    return chr(10).join(L)


def push_digest(day: str, n_err: int, acceptance_status: str) -> tuple:
    """推送复盘摘要到飞书 + 审计。幂等：event_key 相同飞书去重，审计追加。"""
    event_key = f"log-review:{day}"
    status = code = None
    err = None
    ok = False
    body = b""
    try:
        hook = WEBHOOK_FILE.read_text(encoding="utf-8").strip()
        if not hook.startswith("https://open.feishu.cn/open-apis/bot/v2/hook/"):
            return (False, "PUSH_FAIL: invalid webhook")
        lines = [f"EvoAlpha｜日志复盘 {day}", f"错误信号 {n_err} 条 · 日终验收 {acceptance_status}",
                 f"完整摘要 | outputs/reviews/log_review_{day}.md", "—— 每日自动生成（log_error_digest v1），报错不再漏消费"]
        body = json.dumps({"msg_type": "text", "content": {"text": chr(10).join(lines)}}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(hook, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            raw = resp.read()
            code = json.loads(raw.decode("utf-8")).get("code") if raw else None
        ok = (status == 200 and code == 0)
    except Exception as exc:
        err = type(exc).__name__
    finally:
        try:
            audit = OUT / "notifications" / ("delivery_" + day.replace("-", "") + ".jsonl")
            audit.parent.mkdir(parents=True, exist_ok=True)
            row = {"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": "review",
                   "event_key": event_key, "message_sha256": hashlib.sha256(body).hexdigest(),
                   "http_status": status, "business_code": code, "ok": ok, "error_type": err}
            with audit.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + chr(10))
        except Exception:
            pass
    return (ok, "" if ok else f"PUSH_FAIL: {err}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--push", action="store_true", help="推送复盘摘要到飞书（写审计 kind=review）")
    a = ap.parse_args()
    day = a.date or datetime.datetime.now().strftime("%Y-%m-%d")
    errors, warnings = [], []

    # 1) 任务失败（task_logs）
    for f in read_task_failures(day):
        src = f"任务 {f['mode']} rc={f['rc']}"
        hint = RC_HINTS.get(f["rc"], "")
        ev = f"{f['count']} 次 · {f['first']} ~ {f['last']}"
        if f["stderr_sample"]:
            ev += " · stderr 尾部:" + chr(10) + "【" + chr(10) + f["stderr_sample"][:600] + chr(10) + "】"
        errors.append({"source": src, "summary": f"mode={f['mode']} 失败 {f['count']} 次", "detail": "",
                       "evidence": ev, "category": "", "hint": hint, "stderr_sample": f["stderr_sample"]})

    # 2) 验收失败检查项
    acc = read_acceptance(day)
    if acc:
        for c in acc["failed_checks"]:
            streak = read_streak(day, c)
            extra = f"（连续 {streak} 日失败）" if streak >= 2 else ""
            errors.append({"source": f"验收/{c}{extra}", "summary": f"验收检查项 {c} 未通过{extra}",
                           "detail": "", "evidence": "", "category": "验收门禁",
                           "hint": CHECK_HINTS.get(c, "查看 acceptance_<date>.json 对应检查项"), "stderr_sample": ""})
        for t, rc in (acc.get("task_fail") or {}).items():
            errors.append({"source": f"验收/任务 {t}", "summary": f"计划任务 {t} LastResult={rc}",
                           "detail": "", "evidence": "", "category": "计划任务",
                           "hint": RC_HINTS.get(int(rc), f"任务 {t} 失败，查 task_logs 与该任务 stderr"), "stderr_sample": ""})
        if acc.get("watchdog") and (acc["watchdog"].get("restart_events") or 0) > 2:
            warnings.append(f"tick 看门狗当日重启 {acc['watchdog']['restart_events']} 次（未耗尽，观察）")

    # 3) 盘后链失败 stage
    for st in read_chain(day):
        errors.append({"source": f"盘后链/{st['stage']}", "summary": f"chain stage {st['stage']} status={st['status']}",
                       "detail": st.get("skipped_due_to") or "", "evidence": f"exit_code={st.get('exit_code')}",
                       "category": "盘后链", "hint": "查 chain_manifest 与对应 stage 的 stdout.log（next_plan 常见根因=日历源失败）",
                       "stderr_sample": ""})

    # 4) 失败推送
    for d in read_delivery_failures(day):
        hint = ""
        for key, h in FAILURE_KEY_HINTS:
            if key in (d.get("event_key") or ""):
                hint = h
        errors.append({"source": f"推送/{d.get('event_key')}", "summary": f"失败告警推送 {d.get('event_key')}",
                       "detail": "", "evidence": f"time={d.get('time')}", "category": "失败告警", "hint": hint, "stderr_sample": ""})

    # 5) 风控事件
    for r in read_risk_events(day):
        if r.get("action") in ("halt", "block", "watch_limit"):
            errors.append({"source": f"风控/{r.get('rule')}", "summary": f"风控事件 {r.get('rule')} action={r.get('action')}",
                           "detail": str(r.get("detail", ""))[:120], "evidence": f"{r.get('time')} {r.get('sym')}",
                           "category": "风控", "hint": "查 risk_events.jsonl 与看门狗状态", "stderr_sample": ""})
        elif r.get("action") not in (None, "info"):
            warnings.append(f"风控事件 {r.get('rule')} {r.get('sym')} action={r.get('action')}（alert_only，记录）")

    # 6) preflight 门禁失败项（critical=FAIL 入错误；非 critical=WARN 入警告）
    for p in read_preflight(day):
        item = {"source": f"门禁/{p['stage']}", "summary": f"preflight 检查项 {p['name']} 未通过",
                "detail": p["detail"], "evidence": "", "category": "门禁",
                "hint": "查 preflight_<date>_<stage>.json；门禁失败会 fail-closed 拦截盘中任务", "stderr_sample": ""}
        if p.get("critical"):
            errors.append(item)
        else:
            warnings.append(f"门禁 WARN {p['stage']}/{p['name']}：{p['detail'][:60]}")

    # 7) 晨检
    mc = _json(OUT / "validation" / f"morning_check_{day}.json")
    if mc and not (mc.get("summary") or {}).get("pass"):
        errors.append({"source": "晨检", "summary": "晨检未通过（盘前链/gate/失败推送有异常）",
                       "detail": json.dumps(mc.get("summary"), ensure_ascii=False), "evidence": "",
                       "category": "晨检", "hint": "见 morning_check_<date>.json 与当日失败推送", "stderr_sample": ""})

    # 8) 看门狗隔离状态
    gs = _json(BASE / "portfolio" / "tick_guard_state.json")
    if gs and gs.get("state") == "restart_exhausted":
        errors.append({"source": "看门狗", "summary": "tick 看门狗重启额度耗尽（隔离状态）",
                       "detail": json.dumps(gs, ensure_ascii=False)[:200], "evidence": "",
                       "category": "看门狗", "hint": "人工复活 daemon 记 watch_recovered；评估重启根因（锁/数据源）", "stderr_sample": ""})

    new_proposals = classify(errors)
    # 合并 trader_daily 已有提案
    prop_f = OUT / "iteration_proposals" / f"{day}.json"
    existing = (_json(prop_f) or {}).get("proposals") or []
    proposals = list(dict.fromkeys(existing + new_proposals))
    prop_out = {
        "date": day,
        "proposals": proposals,
        "status": "pending_confirm",
        "log_review": {"errors": len(errors), "warnings": len(warnings),
                       "digest": f"outputs/reviews/log_review_{day}.md",
                       "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
    }
    prop_f.parent.mkdir(parents=True, exist_ok=True)
    prop_f.write_text(json.dumps(prop_out, ensure_ascii=False, indent=1), encoding="utf-8")

    acc_status = (acc or {}).get("status") or "n/a"
    data = {"errors": errors, "warnings": warnings, "acceptance": {"status": acc_status},
            "proposals": proposals, "generated_at": prop_out["log_review"]["generated_at"]}
    rev_f = OUT / "reviews"
    rev_f.mkdir(parents=True, exist_ok=True)
    digest = build_digest(day, data)
    (rev_f / f"log_review_{day}.md").write_text(digest, encoding="utf-8")
    print(digest)
    if a.push:
        ok, msg = push_digest(day, len(errors), acc_status)
        print("review_push:", "OK" if ok else msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
