"""手动重启 tick daemon 的标准启动器。

2026-09-03 固化：9/2 与 9/3 连续两日需要手动重启卖出执行器。

启动链路（三层）:
  Bash 沙箱内: python _restart_tick_daemon.py [reason]
    -> schtasks ONCE + /Run 拉起本脚本的 --spawn 模式（沙箱外/Job Object 之外）
       -> --spawn 分支 Popen DETACHED 真正的 tick daemon（继承沙箱外环境）

三层原因（2026-09-03 实测）:
  ① Bash 工具沙箱在命令返回后清理全部后代进程 —— 直接 Popen detached 必死（假阳性：
    4 秒存活验证通过后，启动器退出 Job 关闭才连带杀子进程）;
  ② schtasks 直接拉 python.exe 跑 tick_monitor 卡死在 import 前（恒 3,496KB，
    疑无 std 句柄 + System32 cwd 环境）;
  ③ 今晨 09:30 计划任务 daemon（powershell 包装 + 设 cwd）正常跑 1h46min，
    证明 schtasks 链路可行 —— 本启动器 = 那条链路的可复用复刻。

用法: python scripts/_restart_tick_daemon.py [原因说明]
存证: outputs/task_logs/<day>/<YYYYMMDD>_manual_tick.json（沿用 9/2 存证格式）
日志: outputs/_tick_daemon_<YYYYMMDD>.stdout/.stderr.log（daemon 输出与 traceback）
"""
import json, pathlib, subprocess, sys, time
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = pathlib.Path(__file__).resolve().parent.parent
PY = sys.executable
SCRIPT = str(BASE / 'scripts' / 'tick_monitor.py')
TASK = 'yaoban_tick_manual'
now = datetime.now(ZoneInfo('Asia/Shanghai'))
day = now.strftime('%Y-%m-%d')
d8 = day.replace('-', '')
reason = (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--')
          else 'manual restart')


def _write_evidence(method, pid):
    logdir = BASE / 'outputs' / 'task_logs' / day
    logdir.mkdir(parents=True, exist_ok=True)
    json.dump({'date': day, 'time': datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S'),
               'action': 'manual_restart_tick_daemon', 'pid': pid, 'method': method,
               'argv': 'tick_monitor.py --daemon --interval 5 --execute-risk',
               'reason': reason},
              open(logdir / f'{d8}_manual_tick.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)


# --- --spawn 模式: 已由 schtasks 在沙箱外调起, Popen 常驻看门狗 ---
# 看门狗负责拉起 daemon + pos_live 停写检测 + 自动重启(2026-09-03 daemon 三连挂后加的闭环)
if '--spawn' in sys.argv:
    so = open(BASE / 'outputs' / f'_tick_watch_{d8}.stdout.log', 'a', encoding='utf-8')
    se = open(BASE / 'outputs' / f'_tick_watch_{d8}.stderr.log', 'a', encoding='utf-8')
    p = subprocess.Popen([PY, '-X', 'utf8', str(BASE / 'scripts' / '_tick_watch.py')],
                         cwd=str(BASE), stdout=so, stderr=se, creationflags=0x00000008)
    _write_evidence('schtasks_spawner_watchdog', p.pid)
    print(f'tick watchdog spawned pid={p.pid}')
    time.sleep(8)  # 让 detached 子进程完成初始化后再退出（同时留下日志句柄给子进程）
    sys.exit(0)

# --- 主模式: 注册 schtasks 任务并触发（沙箱内的调用入口）---
# 先清理旧 watcher 与可能卡死/挂死的旧 daemon
for pat in ('*tick_monitor*--daemon*', '*_tick_watch.py*'):
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                              f"Where-Object {{$_.CommandLine -like '{pat}'}} | "
                              'Select-Object -ExpandProperty ProcessId'],
                             capture_output=True, text=True, timeout=20)
        for opid in [int(x) for x in (out.stdout or '').split()]:
            subprocess.run(['taskkill', '/PID', str(opid), '/F'], capture_output=True)
    except Exception:
        pass

# P0 加固(2026-09-04): 被杀 watcher 的残留锁会让新 watcher 白等 beat 120s 超时;
# 仅当确认已无存活 watcher 时清锁(若 kill 失败则保留锁, 由 beat 接管逻辑兜底防双守)。
time.sleep(1)
try:
    _w = subprocess.run(['powershell', '-NoProfile', '-Command',
                         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                         "Where-Object {$_.CommandLine -like '*_tick_watch.py*'} | "
                         'Select-Object -ExpandProperty ProcessId'],
                        capture_output=True, text=True, timeout=20)
    _alive = [int(x) for x in (_w.stdout or '').split()]
except Exception:
    _alive = [1]  # 查询失败: 保守认为还有存活者, 不动锁
if not _alive:
    (BASE / 'outputs' / 'intraday' / '_tick_watch.lock').unlink(missing_ok=True)

tr = f'"{PY}" -X utf8 "{pathlib.Path(__file__).resolve()}" --spawn'
subprocess.run(['schtasks', '/Create', '/TN', TASK, '/SC', 'ONCE', '/ST', '23:59', '/F', '/TR', tr],
               check=True, capture_output=True)
subprocess.run(['schtasks', '/Run', '/TN', TASK], check=True, capture_output=True)
time.sleep(10)
# 反查新 daemon pid（schtasks 不回传 pid）
pid = None
try:
    out = subprocess.run(['powershell', '-NoProfile', '-Command',
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                          "Where-Object {$_.CommandLine -like '*tick_monitor*--daemon*'} | "
                          'Select-Object -ExpandProperty ProcessId'],
                         capture_output=True, text=True, timeout=20)
    for line in (out.stdout or '').split():
        pid = int(line)
        break
except Exception:
    pass
_write_evidence('schtasks_spawner', pid)
print(f'tick watchdog chain started, daemon pid={pid}')
