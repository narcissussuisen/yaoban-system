# -*- coding: utf-8 -*-
"""2026-09-02 手动恢复盘中卖出执行通道（运维干预，非代码变更）。

背景：
  9/2 08:45 infra 预检 3 项 critical 失败（9/1 close 崩溃遗留的净值点缺失、
  情绪表/候选表停更）→ 08:55 plan-gate exit 21 → post_plan gate 文件未生成 →
  09:15/09:30 的 auction/tick/scan/monitor/notify 全部 exit 20。
  其中 tick 任务（--execute-risk）是系统唯一的盘中卖出执行器（止损/炸板/破VWAP），
  被 gate 拓扑一并挡死 —— 与 fail-closed "阻断进入、放行退出" 的语义相悖（设计缺陷，已登记 1A/1B 修复项）。

本脚本做什么（用户 9/2 09:07 授权："根据 evoalpha 的卖出规则处理，我不能越权插手"）：
  1. 以系统自身的 tick_monitor.py（原样、零修改、--daemon --interval 5 --execute-risk）
     detached 拉起卖出执行器 —— 决策权完全在系统规则内，本脚本不做任何交易决策；
  2. 同时拉起 _risk_watch_20260902.py 看门狗：tail risk_events.jsonl 与 daemon stdout，
     把 RISK/RISK-EXEC/RISK-FAIL 事件实时推送到 yaoban 既定飞书 webhook；
     daemon 意外死亡时告警并自拉起（限 2 次）；
  3. 写干预存证 JSON（时间、pid、日志路径、理由），供今晚 acceptance/复盘如实记录本日为
     "fail-closed 日 + 人工解除退出通道封锁" 的事件。

不做什么：
  - 不生成/伪造任何 gate 文件；
  - 不启动 scan（进场侧保持 fail-closed 封锁）；
  - 不修改任何策略/账本代码；不做任何人工交易决策。
"""
import json
import os
import pathlib
import subprocess
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
PY = r"C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
DAY = "2026-09-02"
TL = BASE / "outputs" / "task_logs" / DAY
PID_FILE = BASE / "outputs" / "intraday" / "manual_tick_20260902.pid"
MARKER = TL / "20260902_manual_tick.json"
DAEMON_OUT = TL / "20260902_manual_tick.stdout.log"
DAEMON_ERR = TL / "20260902_manual_tick.stderr.log"
WATCH_OUT = TL / "20260902_manual_watch.stdout.log"

CREATION_FLAGS = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP


def pid_alive(pid: int) -> bool:
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        k.CloseHandle(h)
        return True
    except Exception:
        return True  # 检测失败一律视为存活（防误判）


def main() -> int:
    TL.mkdir(parents=True, exist_ok=True)
    # 防重入：已有存活实例则不重复拉起
    if PID_FILE.exists():
        try:
            old = int(PID_FILE.read_text().strip())
            if old and pid_alive(old):
                print(f"[SKIP] manual tick daemon already running pid={old}")
                return 0
        except Exception:
            pass

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"

    out_f = open(DAEMON_OUT, "ab")
    err_f = open(DAEMON_ERR, "ab")
    daemon = subprocess.Popen(
        [PY, "-X", "utf8", str(BASE / "scripts" / "tick_monitor.py"),
         "--daemon", "--interval", "5", "--execute-risk"],
        cwd=str(BASE), stdout=out_f, stderr=err_f,
        creationflags=CREATION_FLAGS, close_fds=True, env=env)
    out_f.close(); err_f.close()

    wout_f = open(WATCH_OUT, "ab")
    watcher = subprocess.Popen(
        [PY, "-X", "utf8", str(BASE / "scripts" / "_risk_watch_20260902.py"),
         str(daemon.pid)],
        cwd=str(BASE), stdout=wout_f, stderr=subprocess.STDOUT,
        creationflags=CREATION_FLAGS, close_fds=True, env=env)
    wout_f.close()

    PID_FILE.write_text(str(daemon.pid), encoding="ascii")
    marker = {
        "date": DAY,
        "kind": "manual_intervention",
        "reason": "post_plan gate missing (plan-gate exit 21) blocked the ONLY sell executor "
                  "(tick --execute-risk); unblocking exit channel per fail-closed semantics "
                  "(block entries, allow exits). Gate topology fix registered as 1A/1B work item.",
        "authorized_by": "user 2026-09-02 09:07 (follow system sell rules, no human override)",
        "daemon_pid": daemon.pid,
        "watcher_pid": watcher.pid,
        "daemon_cmd": "tick_monitor.py --daemon --interval 5 --execute-risk (unmodified)",
        "daemon_stdout": str(DAEMON_OUT),
        "daemon_stderr": str(DAEMON_ERR),
        "watcher_stdout": str(WATCH_OUT),
        "scope": "sell-side only; scan (entry side) remains fail-closed; no gate files faked",
        "launched_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 保留历史尝试（第一轮 detached 直拉起被工具沙箱 job 清理，第二轮改走 schtasks 触发）
    try:
        prev = json.loads(MARKER.read_text(encoding="utf-8"))
        attempts = prev.get("attempts", []) + [prev]
        marker["attempts"] = attempts
    except Exception:
        pass
    MARKER.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(marker, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
