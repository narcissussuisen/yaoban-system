"""tick daemon 看门狗（2026-09-03）。

背景: 当日 daemon 三连挂(11:16 WinError5 / 13:4x 两轮 pos_live 停写、机理未明
的无限阻塞), 唯一卖出执行器盘中死亡 = 持仓裸奔。本 watcher 常驻监控 + 自动重启。

职责:
  1. 启动 daemon(detached, 继承沙箱外环境 —— 由 schtasks->spawner 链拉起本进程)
  2. 每 20s 检查 pos_live.json mtime: 停写 >90s 判定挂死(容忍 interval 退避最大 30s
     + 一轮 TDX 拉取), 杀掉重启
  3. 重启上限 5 次, 超限 append_event 告警并退出(fail-loud, scan 的 tick stale
     检测会继续向用户告警)
  4. 15:10 自动退出(收盘守护结束)
  5. 每 60s 写心跳文件 _tick_watch.beat

用法: python scripts/_tick_watch.py   (经 _restart_tick_daemon.py --spawn 链路拉起)
"""
import json, os, pathlib, subprocess, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
SCRIPT = str(BASE / 'scripts' / 'tick_monitor.py')
OUT = BASE / 'outputs' / 'intraday'
POS = OUT / 'pos_live.json'
BEAT = OUT / '_tick_watch.beat'
MAX_RESTARTS = 5
STALE_SEC = 90
CHECK_EVERY = 20


def hm_now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%H:%M')


def ts_now():
    return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')


def append_event(ev):
    with (OUT / 'risk_events.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(ev, ensure_ascii=False) + '\n'); f.flush(); os.fsync(f.fileno())


def spawn_daemon():
    so = open(BASE / 'outputs' / f'_tick_daemon_{datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")}.stdout.log', 'a', encoding='utf-8')
    se = open(BASE / 'outputs' / f'_tick_daemon_{datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")}.stderr.log', 'a', encoding='utf-8')
    p = subprocess.Popen([PY, '-X', 'utf8', SCRIPT, '--daemon', '--interval', '5', '--execute-risk'],
                         cwd=str(BASE), stdout=so, stderr=se, creationflags=0x00000008)
    return p.pid


def find_pids(pattern):
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                              f"Where-Object {{$_.CommandLine -like '{pattern}'}} | "
                              'Select-Object -ExpandProperty ProcessId'],
                             capture_output=True, text=True, timeout=25)
        return [int(x) for x in (out.stdout or '').split() if int(x) != os.getpid()]
    except Exception:
        return []


def find_daemon_pids():
    return find_pids('*tick_monitor*--daemon*')


def kill_daemons(pids):
    for pid in pids:
        subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)


# --- 单实例互斥: 文件锁(O_EXCL 原子创建, 唯一赢家) ---
# 9/3 教训: schtasks 启动有延迟, 连续两次 /Run 会让前后两波 watcher 几乎同时到达,
# "检测到别人就退出"的互斥导致双方全退没人守。改为抢占锁: 谁创建锁文件谁活。
# 死锁恢复: 持有者死亡锁文件残留 -> 后来者用 beat 文件年龄(>120s)判断接管。
LOCK = OUT / '_tick_watch.lock'
_got_lock = False
try:
    _fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(_fd, str(os.getpid()).encode()); os.close(_fd)
    _got_lock = True
except FileExistsError:
    try:
        _beat_age = time.time() - (OUT / '_tick_watch.beat').stat().st_mtime
    except FileNotFoundError:
        _beat_age = 1e9
    if _beat_age > 120:  # 持有者已死(beat 停更), 接管
        try:
            LOCK.unlink()
            _fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(_fd, str(os.getpid()).encode()); os.close(_fd)
            _got_lock = True
        except OSError:
            pass
if not _got_lock:
    print('[watch] lock held by live watcher, exiting', flush=True)
    sys.exit(0)

restarts = 0
cur = spawn_daemon()
append_event({'date': datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d'),
              'time': ts_now(), 'sym': '-', 'trigger': 'watch_start',
              'action': 'info', 'detail': f'tick watchdog up, daemon pid={cur}'})
print(f'[watch] up, daemon pid={cur}', flush=True)

try:
    while True:
        time.sleep(CHECK_EVERY)
        h = hm_now()
        if h > '15:10':
            append_event({'date': datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d'),
                          'time': ts_now(), 'sym': '-', 'trigger': 'watch_exit',
                          'action': 'info', 'detail': 'past close, watchdog exit'})
            print('[watch] past close, exit', flush=True)
            break
        BEAT.write_text(f'{ts_now()} restarts={restarts}', encoding='ascii')
        try:
            age = time.time() - POS.stat().st_mtime
        except FileNotFoundError:
            age = 1e9
        pids = find_daemon_pids()
        if not pids:  # daemon 进程都没了
            if restarts >= MAX_RESTARTS:
                append_event({'date': datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d'),
                              'time': ts_now(), 'sym': '-', 'trigger': 'watch_limit',
                              'action': 'halt', 'detail': f'{MAX_RESTARTS} restarts exhausted, daemon missing'})
                break
            kill_daemons(pids)
            cur = spawn_daemon(); restarts += 1
            append_event({'date': datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d'),
                          'time': ts_now(), 'sym': '-', 'trigger': 'watch_restart',
                          'action': 'info', 'detail': f'daemon missing, restart #{restarts} pid={cur}'})
            continue
        if age > STALE_SEC:  # 进程在但停写 = 挂死
            if restarts >= MAX_RESTARTS:
                append_event({'date': datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d'),
                              'time': ts_now(), 'sym': '-', 'trigger': 'watch_limit',
                              'action': 'halt', 'detail': f'{MAX_RESTARTS} restarts exhausted, pos_live stale {int(age)}s'})
                kill_daemons(pids)
                break
            kill_daemons(pids)
            cur = spawn_daemon(); restarts += 1
            append_event({'date': datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d'),
                          'time': ts_now(), 'sym': '-', 'trigger': 'watch_restart',
                          'action': 'info', 'detail': f'pos_live stale {int(age)}s, restart #{restarts} pid={cur}'})
            continue
finally:
    try:
        LOCK.unlink()
    except OSError:
        pass
