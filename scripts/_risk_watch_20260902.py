# -*- coding: utf-8 -*-
"""2026-09-02 盘中风险事件看门狗（配合 manual_tick_unblock_20260902.py，运维工具）。

职责：
  1. tail outputs/intraday/risk_events.jsonl —— 新增 RISK 事件实时推飞书；
  2. tail daemon stdout 日志 —— 只推 [RISK-EXEC]/[RISK-FAIL]/[RISK-BLOCK]/TDX重连失败 等关键行；
  3. 看护 daemon 进程存活（ctypes OpenProcess 零子进程方案）：15:00 前意外死亡则告警并
     自拉起（最多 2 次）；超限后仅告警；
  4. 15:12 自行退出，收尾时更新干预存证 JSON 并推送当日总结。

不推历史事件：启动时以当前文件末尾为基线。
"""
import ctypes
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

BASE = pathlib.Path(__file__).resolve().parent.parent
DAY = "2026-09-02"
OUT = BASE / "outputs" / "intraday"
TL = BASE / "outputs" / "task_logs" / DAY
RISK_FILE = OUT / "risk_events.jsonl"
DAEMON_LOG = TL / "20260902_manual_tick.stdout.log"
MARKER = TL / "20260902_manual_tick.json"
PID_FILE = OUT / "manual_tick_20260902.pid"
PY = r"C:\Users\YZP\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
WEBHOOK_FILE = pathlib.Path(r"C:\Users\YZP\WorkBuddy\yaoban_tasks\feishu_webhook.txt")
CREATION_FLAGS = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
MAX_RESTARTS = 2

# 信号免疫（Windows 长跑保命）
for _s in ("SIGINT", "SIGBREAK", "SIGTERM"):
    try:
        signal_mod = __import__("signal")
        signal_mod.signal(getattr(signal_mod, _s), signal_mod.SIG_IGN)
    except (AttributeError, ValueError, OSError):
        pass


def webhook() -> str:
    return WEBHOOK_FILE.read_text(encoding="utf-8").strip()


def push(text: str) -> bool:
    try:
        data = json.dumps({"msg_type": "text", "content": {"text": text}}).encode("utf-8")
        req = urllib.request.Request(webhook(), data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        print(f"[WATCH] push failed: {e}", flush=True)
        return False


def pid_alive(pid: int) -> bool:
    try:
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        k.CloseHandle(h)
        return True
    except Exception:
        return True


def baseline(path: pathlib.Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_new(path: pathlib.Path, offset: int):
    """返回 (new_text, new_offset)。文件被截断(变短)时重置基线。"""
    try:
        size = path.stat().st_size
    except OSError:
        return "", offset
    if size < offset:
        offset = 0
    if size == offset:
        return "", offset
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read(size - offset).decode("utf-8", errors="replace")
    return chunk, size


def fmt_risk_event(line: str) -> str:
    try:
        ev = json.loads(line)
        br = ev.get("blocked_reason") or ""
        return (f"[RISK] {ev.get('sym')} {ev.get('trigger')} px={ev.get('px')} "
                f"qty={ev.get('qty')} action={ev.get('action')} {br} @{ev.get('time')}")
    except Exception:
        return "[RISK] " + line.strip()


def spawn_daemon() -> int:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    out_f = open(DAEMON_LOG, "ab")
    err_f = open(TL / "20260902_manual_tick.stderr.log", "ab")
    p = subprocess.Popen(
        [PY, "-X", "utf8", str(BASE / "scripts" / "tick_monitor.py"),
         "--daemon", "--interval", "5", "--execute-risk"],
        cwd=str(BASE), stdout=out_f, stderr=err_f,
        creationflags=CREATION_FLAGS, close_fds=True, env=env)
    out_f.close(); err_f.close()
    PID_FILE.write_text(str(p.pid), encoding="ascii")
    return p.pid


def main() -> int:
    daemon_pid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    print(f"[WATCH] start daemon_pid={daemon_pid} at {datetime.now()}", flush=True)
    off_risk = baseline(RISK_FILE)
    off_log = baseline(DAEMON_LOG)
    pushed = 0
    restarts = 0
    dead_alerted = False
    while True:
        now = datetime.now()
        hm = now.strftime("%H:%M")
        if hm > "15:12":
            break
        # 1) 新风险事件
        chunk, off_risk = read_new(RISK_FILE, off_risk)
        for line in (l for l in chunk.splitlines() if l.strip()):
            push(fmt_risk_event(line))
            pushed += 1
        # 2) daemon stdout 关键行
        chunk, off_log = read_new(DAEMON_LOG, off_log)
        for line in chunk.splitlines():
            ls = line.strip()
            if ls.startswith(("[RISK-EXEC]", "[RISK-FAIL]", "[RISK-BLOCK]", "TDX重连失败")):
                push("[manual-tick] " + ls)
                pushed += 1
        # 3) daemon 存活看护
        if daemon_pid and not pid_alive(daemon_pid):
            if hm < "15:00" and restarts < MAX_RESTARTS:
                restarts += 1
                push(f"[manual-tick][WARN] 卖出执行器进程死亡(pid={daemon_pid})，"
                     f"第 {restarts}/{MAX_RESTARTS} 次自拉起 @{now.strftime('%H:%M:%S')}")
                daemon_pid = spawn_daemon()
                off_log = baseline(DAEMON_LOG)
                push(f"[manual-tick] 已重启 pid={daemon_pid}")
            elif not dead_alerted:
                dead_alerted = True
                push(f"[manual-tick][ALERT] 卖出执行器死亡且不再自拉起(pid={daemon_pid})，"
                     f"持仓失去盘中守护，请人工关注 @ {now.strftime('%H:%M:%S')}")
        time.sleep(5)
    # 收尾：更新存证 + 总结
    alive = pid_alive(daemon_pid)
    try:
        m = json.loads(MARKER.read_text(encoding="utf-8"))
        m["watcher_finished_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        m["watcher_pushed_events"] = pushed
        m["daemon_restarts"] = restarts
        m["daemon_alive_at_finish"] = alive
        MARKER.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[WATCH] marker update failed: {e}", flush=True)
    push(f"[manual-tick] 9/2 收盘收尾：daemon存活={alive} 重启={restarts}次 "
         f"推送事件={pushed}条；干预存证见 task_logs/{DAY}/20260902_manual_tick.json")
    print(f"[WATCH] finish pushed={pushed} restarts={restarts} alive={alive}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
