"""收盘后平滑轮换 tick watchdog（INC-2026-09-11-01 收口项①）。

背景: 今日线上 watcher(进程随 09:30 计划任务启动)运行的是**修复前**代码, 其 restarts 累计到 7
(> max_restarts 5), 且 `stale` 分支会直接 kill daemon + 进入 restart_exhausted 隔离而不再重启。
修复后的 `_tick_watch.py` 在 15:10 自退后由次日 09:30 计划任务自然接管即可 —— 本脚本只是让
修复**当天收盘即生效**, 并顺带把当日虚假重启累计的计数归零。

安全边界:
  - 只在 15:42 之后动作(watcher 的 WATCH_EXIT_AT=15:40 已自退, daemon 15:30 已自退;
    2026-09-12 R0.8 前是 15:12/15:10/15:05, 随盘后固定价格窗口一并后移),
    避免盘中停掉持仓守护。
  - 幂等: 若 watcher 已自退则只清锁, 不重复拉起。
  - 不碰 daemon 逻辑与配置, 只重启守护壳。
用法: python scripts/_rotate_tick_watch_after_close.py [--now]
      (--now 跳过时间等待, 仅供联调; 默认等到 15:42)
"""
from __future__ import annotations
import argparse
import pathlib
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / 'outputs'
INTRADAY = OUT / 'intraday'
PY = sys.executable
TZ = ZoneInfo('Asia/Shanghai')
WAIT_UNTIL = (15, 42)          # watcher 15:40 自退后 2 分钟（R0.8 前为 (15,12)）
WATCH_PAT = '*_tick_watch.py*'
DAEMON_PAT = '*tick_monitor*--daemon*'


def log(msg):
    line = f"[rotate {datetime.now(TZ):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with (OUT / 'tick_watch_rotate_20260911.log').open('a', encoding='utf-8') as f:
        f.write(line + '\n')


def cim_pids(pattern):
    """列出匹配 pattern 的 python 进程 pid。受限环境下查询会失败 -> 返回 None 表示不可判定。"""
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command',
                            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                            f"Where-Object {{$_.CommandLine -like '{pattern}'}} | "
                            'Select-Object -ExpandProperty ProcessId'],
                           capture_output=True, text=True, timeout=25)
        if r.returncode != 0:
            return None
        return [int(x) for x in (r.stdout or '').split()]
    except Exception:
        return None


def wait_until_close():
    while True:
        n = datetime.now(TZ)
        if (n.hour, n.minute) >= WAIT_UNTIL:
            return
        time.sleep(20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--now', action='store_true', help='跳过时间等待(联调)')
    a = ap.parse_args()

    if not a.now:
        log(f'等待收盘窗口 {WAIT_UNTIL[0]:02d}:{WAIT_UNTIL[1]:02d} ...')
        wait_until_close()

    # 1) 确认旧 watcher 已自退; 仍在则停掉(收盘后已无守护意义, 且它是修复前代码)
    pids = cim_pids(WATCH_PAT)
    if pids is None:
        log('WARN: 进程探测不可用(CIM 受限), 跳过 watcher 存活判定, 仅清锁 + 拉起')
    elif pids:
        log(f'旧 watcher 仍在: {pids} -> 收盘后停掉')
        for p in pids:
            subprocess.run(['taskkill', '/PID', str(p), '/F'], capture_output=True)
        time.sleep(2)
    else:
        log('旧 watcher 已自退(符合 15:40 设计)')

    # 2) 清残留锁: 被 kill 的 watcher 残留锁会让新 watcher 白等 beat 120s(_restart_tick_daemon 已记载)
    lock = INTRADAY / '_tick_watch.lock'
    if lock.exists():
        try:
            lock.unlink()
            log('已清 _tick_watch.lock 残留锁')
        except OSError as e:
            log(f'WARN: 清锁失败 {e}')

    # 3) 拉起修复版 watcher(等价 run_trading_task.ps1 -Mode tick 的最终一步)
    so = open(OUT / '_tick_watch_20260911.stdout.log', 'a', encoding='utf-8')
    se = open(OUT / '_tick_watch_20260911.stderr.log', 'a', encoding='utf-8')
    p = subprocess.Popen([PY, '-X', 'utf8', str(BASE / 'scripts' / '_tick_watch.py')],
                         cwd=str(BASE), stdout=so, stderr=se, creationflags=0x00000008)
    log(f'已拉起修复版 watcher pid={p.pid}')

    # 4) 验证: beat 出现且 restarts=0, daemon 锁被刷新
    beat = INTRADAY / '_tick_watch.beat'
    ok_beat = ok_daemon = False
    for _ in range(40):
        time.sleep(3)
        try:
            txt = beat.read_text(encoding='ascii')
            if 'restarts=0' in txt:
                ok_beat = True
                log(f'beat 正常: {txt.strip()}')
                break
        except OSError:
            pass
    dlock = INTRADAY / '_tick_daemon.lock'
    if dlock.exists() and time.time() - dlock.stat().st_mtime < 60:
        ok_daemon = True
        log(f'daemon 锁已刷新: pid={dlock.read_text(encoding="ascii").strip()}')
    log(f'轮换结果: beat_restarts0={ok_beat} daemon_alive={ok_daemon}')
    return 0 if (ok_beat and ok_daemon) else 1


if __name__ == '__main__':
    sys.exit(main())
