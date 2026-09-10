"""盘前自愈处置程序 v1(2026-09-10 用户裁定②)。

授权边界(硬编码, 不允许通过参数扩大):
  允许: 服务重启、进程残留清理、数据表重建(r5p/r6p)、计划重生成、任务重注册、TDX 探活;
  禁止: 改账本(portfolio/ledger.json)、手工写门禁通过、改生产代码 —— 本脚本内不存在这三条路径。

时点: 每个交易日 08:36(08:35 preflight 之后); 硬截止 08:48, 数据类可延至 09:08(距 09:15 竞价 7 分钟)。
复检: 一律重跑真实 preflight 产生新门禁文件, 绝不手写门禁判定。
产物: outputs/selfheal/<day>.json (每轮 before/after 证据)
退出码: 恒 0(状态由产物与卡片承载, 避免与自身告警重复推送)

用法: python scripts/selfheal.py [--date D] [--dry-run] [--deadline 08:48] [--no-push]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime

BASE = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
OUT = BASE / "outputs"
SELFHEAL_DIR = OUT / "selfheal"
PY = sys.executable
NOTIFY = str(SCRIPTS / "feishu_notify.py")
VIBE = BASE.parent / "Vibe-Research" / "scripts" / "ensure-dashboard-services.ps1"

# 剧本: 按顺序处置, 每项各自预算与截止档位(hard=08:48 / extended=09:08)
PLAYBOOK = [
    {"id": "process", "kind": "进程残留清理", "checks": ["残留进程"], "budget": 1, "deadline": "hard"},
    {"id": "dashboard", "kind": "看板服务重启", "checks": ["看板"], "budget": 1, "deadline": "hard"},
    # 只对"行动作损坏"自愈; 触发器漂移属人为改点可能, 不自愈(见 preflight 任务触发器检查)
    {"id": "tasks", "kind": "计划任务重注册", "checks": ["任务Action"], "budget": 1, "deadline": "hard"},
    {"id": "tdx", "kind": "行情源探活", "checks": ["TDX行情", "腾讯快照"], "budget": 1, "deadline": "hard"},
    {"id": "data", "kind": "数据表重建", "checks": ["情绪表", "候选表", "研究数据"], "budget": 1, "deadline": "extended"},
    {"id": "plan", "kind": "计划重生成", "checks": ["当日计划"], "budget": 2, "deadline": "extended"},
]
# 不自愈清单(必须升级给人工/助手, 绝不自动改)
NOT_REPAIRABLE = ["脚本语法", "账本/持仓", "净值守恒", "交易日历", "资源检查", "外盘新鲜度", "任务历史",
                 "CPU 使用率", "内存使用率", "DISK C 使用率", "DISK F 使用率"]
RERUN_BUDGET = 2  # 全量复检次数上限


def _run(cmd, timeout=900):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(BASE))
        return p.returncode, (p.stdout or "")[-2500:], (p.stderr or "")[-1200:], round(time.time() - t0, 1)
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after " + str(timeout) + "s", round(time.time() - t0, 1)
    except Exception as exc:
        return 125, "", type(exc).__name__ + ": " + str(exc)[:200], round(time.time() - t0, 1)


def gate_path(day, stage="infra"):
    return OUT / ("preflight_" + day + "_" + stage + ".json")


def read_gate(day, stage="infra"):
    p = gate_path(day, stage)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def failed_of(report):
    """致命项(ok=False 且 critical!=False)—— 自愈只对这些负责。"""
    if not report:
        return ["(门禁报告缺失)"]
    return [str(r.get("name")) for r in (report.get("results") or [])
            if r.get("ok") is False and r.get("critical") is not False]


def warn_of(report):
    """非致命提示项(ok=False 但 critical=False)—— 只报告, 不处置不推送。"""
    if not report:
        return []
    return [str(r.get("name")) for r in (report.get("results") or [])
            if r.get("ok") is False and r.get("critical") is False]


def run_preflight(day, stage="infra"):
    args = [PY, "-X", "utf8", str(SCRIPTS / "preflight.py"), "--date", day]
    if stage == "post_plan":
        args.append("--post-plan")
    return _run(args, timeout=600)


def repair_dashboard():
    if not VIBE.exists():
        return 3, "", "missing " + str(VIBE), 0.0
    return _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(VIBE)], timeout=300)


def repair_process():
    ps = ("$p=Get-CimInstance Win32_Process | Where-Object {$_.Name -like 'python*' -and $_.CommandLine -match 'tick_monitor|scan_and_confirm'};"
          "if($p){$p|ForEach-Object{Stop-Process -Id $_.ProcessId -Force; Write-Output ('killed '+$_.ProcessId)}} else {Write-Output 'none'}")
    return _run(["powershell", "-NoProfile", "-Command", ps], timeout=120)


def repair_tasks():
    return _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 str(SCRIPTS / "register_p0_schedule.ps1")], timeout=300)


def repair_tdx():
    return _run([PY, "-X", "utf8", str(SCRIPTS / "tdx_recovery_probe.py")], timeout=420)


def repair_data(failed):
    out = []
    need_r5p = any(x in failed for x in ("情绪表", "研究数据"))
    need_r6p = any(x in failed for x in ("候选表", "研究数据"))
    if need_r5p:
        out.append(("r5p_sentiment_build",) + _run([PY, "-X", "utf8", str(SCRIPTS / "r5p_sentiment_build.py"), "--workers", "6"], timeout=1800))
    if need_r6p:
        out.append(("r6p_candidates_build",) + _run([PY, "-X", "utf8", str(SCRIPTS / "r6p_candidates_build.py"), "--workers", "6"], timeout=1800))
    return out


def repair_plan(day):
    out = []
    out.append(("calendar_refresh",) + _run([PY, "-X", "utf8", str(SCRIPTS / "trading_calendar.py"), "refresh"], timeout=180))
    out.append(("plan_daily",) + _run([PY, "-X", "utf8", str(SCRIPTS / "plan_daily.py"), "--date", day], timeout=900))
    return out


def hhmm(now):
    return now.strftime("%H:%M")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--deadline", default="08:48")
    ap.add_argument("--extended-deadline", default="09:08")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--allow-any-hour", action="store_true", help="手动补跑时跳过时点窗口校验")
    a = ap.parse_args()
    now = datetime.now()
    day = a.date or now.strftime("%Y-%m-%d")
    rep = {"date": day, "started_at": now.strftime("%Y-%m-%d %H:%M:%S"), "dry_run": bool(a.dry_run),
           "deadline": a.deadline, "extended_deadline": a.extended_deadline, "attempts": [], "skipped": []}

    gate = read_gate(day)
    if gate is None:
        rc, so, se, dt = (0, "", "dry-run: would bootstrap preflight", 0.0) if a.dry_run else run_preflight(day)
        rep["attempts"].append({"id": "bootstrap", "kind": "首跑门禁", "rc": rc, "duration_s": dt, "stderr": se})
        gate = read_gate(day)
    before = failed_of(gate)
    rep["gate_before"] = {"status": (gate or {}).get("status"), "failed": before,
                          "warn": warn_of(gate), "time": (gate or {}).get("time")}

    used = {}
    unresolved = list(before)
    if before and before != ["(门禁报告缺失)"]:
        for _round in range(RERUN_BUDGET):
            todo = []
            for step in PLAYBOOK:
                hit = [c for c in step["checks"] if c in unresolved]
                if not hit:
                    continue
                if used.get(step["id"], 0) >= step["budget"]:
                    continue
                limit = a.deadline if step["deadline"] == "hard" else a.extended_deadline
                if not a.allow_any_hour and hhmm(datetime.now()) > limit:
                    rep["skipped"].append({"id": step["id"], "reason": "past deadline " + limit, "checks": hit})
                    continue
                todo.append((step, hit))
            if not todo:
                break
            acted = False
            for step, hit in todo:
                used[step["id"]] = used.get(step["id"], 0) + 1
                entry = {"id": step["id"], "kind": step["kind"], "checks": hit,
                         "at": datetime.now().strftime("%H:%M:%S")}
                if a.dry_run:
                    entry["rc"] = 0
                    entry["detail"] = "dry-run: would execute " + step["kind"]
                    rep["attempts"].append(entry)
                    continue
                acted = True
                sub = []
                if step["id"] == "process":
                    rc, so, se, dt = repair_process()
                    sub.append({"action": "kill lingering tick/scan", "rc": rc, "out": so[-300:], "err": se[-300:], "s": dt})
                elif step["id"] == "dashboard":
                    rc, so, se, dt = repair_dashboard()
                    sub.append({"action": "ensure-dashboard-services.ps1", "rc": rc, "out": so[-300:], "err": se[-300:], "s": dt})
                elif step["id"] == "tasks":
                    rc, so, se, dt = repair_tasks()
                    sub.append({"action": "register_p0_schedule.ps1", "rc": rc, "out": so[-300:], "err": se[-300:], "s": dt})
                elif step["id"] == "tdx":
                    rc, so, se, dt = repair_tdx()
                    sub.append({"action": "tdx_recovery_probe.py", "rc": rc, "out": so[-300:], "err": se[-300:], "s": dt})
                elif step["id"] == "data":
                    for name, rc2, so2, se2, dt2 in repair_data(hit):
                        sub.append({"action": name, "rc": rc2, "out": so2[-300:], "err": se2[-300:], "s": dt2})
                    rc = 0
                elif step["id"] == "plan":
                    for name, rc2, so2, se2, dt2 in repair_plan(day):
                        sub.append({"action": name, "rc": rc2, "out": so2[-300:], "err": se2[-300:], "s": dt2})
                    rc = 0
                entry["actions"] = sub
                entry["rc"] = rc
                rep["attempts"].append(entry)
            if not acted:
                break
            # 真实复检(绝不手写门禁)
            rc, so, se, dt = run_preflight(day)
            rep["attempts"].append({"id": "reverify", "kind": "复检 infra", "rc": rc,
                                    "duration_s": dt, "stderr": se[-300:]})
            unresolved = failed_of(read_gate(day))
            if not unresolved:
                break

    # 计划类失败需同时复检 post_plan 门禁
    if not a.dry_run and any(s.get("id") == "plan" for s in rep["attempts"]):
        rc, so, se, dt = run_preflight(day, "post_plan")
        rep["attempts"].append({"id": "reverify-post-plan", "kind": "复检 post_plan", "rc": rc,
                                "duration_s": dt, "stderr": se[-300:]})

    after = failed_of(read_gate(day))
    rep["gate_after"] = {"status": (read_gate(day) or {}).get("status"), "failed": after,
                         "warn": warn_of(read_gate(day)), "time": (read_gate(day) or {}).get("time")}
    rep["repaired"] = [c for c in before if c not in after]
    rep["unresolved"] = [c for c in after if c not in NOT_REPAIRABLE]
    rep["not_repairable"] = [c for c in after if c in NOT_REPAIRABLE]
    rep["finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not rep["gate_before"]["failed"]:
        rep["verdict"] = "无需自愈(08:35 门禁全过)"
    elif not rep["unresolved"] and not rep["not_repairable"]:
        rep["verdict"] = "自愈成功, 门禁已恢复"
    else:
        rep["verdict"] = "仍有未过项: " + ", ".join(rep["unresolved"] + rep["not_repairable"])

    SELFHEAL_DIR.mkdir(parents=True, exist_ok=True)
    fp = SELFHEAL_DIR / (day + ".json")
    hist = []
    if fp.exists():
        try:
            old = json.loads(fp.read_text(encoding="utf-8-sig"))
            hist = old if isinstance(old, list) else [old]
        except Exception:
            hist = []
    hist.append(rep)
    fp.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")

    L = ["【EvoAlpha 盘前自愈 · " + day + "】",
         "· 08:35 门禁: " + str(rep["gate_before"]["status"]) + "（未过 " + str(len(rep["gate_before"]["failed"])) + " 项"
         + ("：" + ",".join(rep["gate_before"]["failed"]) if rep["gate_before"]["failed"] else "") + "）"]
    for s in rep["attempts"]:
        if s["id"] in ("reverify", "reverify-post-plan"):
            continue
        acts = s.get("actions") or []
        if not acts:
            continue
        L.append("· 处置: " + s["kind"] + " → " + ("OK" if s.get("rc") == 0 else "rc=" + str(s.get("rc")))
                 + "（" + ",".join(x["action"] + " " + str(int(x["s"])) + "s" for x in acts) + "）")
    L.append("· 复检: " + str(rep["gate_after"]["status"]) + "（未过 " + str(len(rep["gate_after"]["failed"])) + " 项）")
    if rep["gate_after"].get("warn"):
        L.append("· 非致命提示: " + ",".join(rep["gate_after"]["warn"]) + "（资源类不自动处置）")
    L.append("· 结论: " + rep["verdict"])
    if rep["unresolved"] or rep["not_repairable"]:
        L.append("  未自愈项属禁止自动处置范围或超出能力，需人工/助手介入；买入类保持 fail-closed，持仓仍受 tick 保护。")
    msg = chr(10).join(L)
    print(msg)
    if (not a.no_push) and (not a.dry_run) and rep["gate_before"]["failed"] and rep["gate_before"]["failed"] != ["(门禁报告缺失)"]:
        kind = "failure" if (rep["unresolved"] or rep["not_repairable"]) else "alert"
        _run([PY, "-X", "utf8", NOTIFY, "--kind", kind, "--date", day,
              "--event-key", "selfheal:" + day, "--message", msg], timeout=180)
        print("push: " + kind)
    return 0


if __name__ == "__main__":
    sys.exit(main())
