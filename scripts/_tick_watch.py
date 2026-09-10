"""tick daemon 看门狗 v2（2026-09-04 凌晨 · 9/3 复盘 P0 修复）。

v1 缺陷（9/3 实录，docs/P0_CHANGELOG.md）:
  1. 收盘窗口误判: daemon 自退时刻 >15:05, watcher 却守到 >15:10 —— 15:05-15:10 把
     正常自退当 daemon missing, 5 连重启烧光额度(15:06-15:08 watch_limit 假告警)。
  2. 耗尽无升级: 重启额度耗尽只 append_event + 退出 —— 无飞书告警/无隔离状态文件,
     盘中风控裸奔要等 16:30 盘后链验收才被看见。
  3. 启动竞态: daemon 首写 pos_live 最坏 ~25s(TDX 连接+首轮拉取), v1 首查 20s 可能
     先于首写 → 误判 stale 假重启。
  4. 午休盲区: daemon 午休(11:30-13:00)按设计不写 pos_live, v1 的 mtime 判据会把
     整个午休当挂死反复重启(v1 仅下午时段在守所以未暴露; 09:30 全天守护必炸)。

v2 修复:
  - 收盘窗口: 分钟数 >= 903(15:03) 后 daemon missing/stale 一律不再重启(daemon 15:05
    自退, 15:03 起留 2 分钟缓冲), 视为正常收盘退出。
  - 启动宽限 BOOT_GRACE(150s): 每次 spawn 后宽限期内不判 stale(等首写), 超过宽限
    仍无首写才判挂死。
  - 午休感知: 11:30-13:00 冻结 stale 判据; 跨午休的 age 扣除午休段(effective_age)。
  - 耗尽升级: watch_limit → tick_guard_state.json(state=restart_exhausted) + 飞书
    告警(每日至多一条) + degraded 观测模式(不再烧额度), 进程退出码 6。
  - 进程存活 ≠ 数据更新: 唯一健康判据是 pos_live.json mtime(数据在更新), 进程列表
    仅用于 missing 检测与 kill。
  - 状态机: armed(启动) → ok/restart_exhausted(耗尽, 保持到收盘) → closed_ok(正常收盘)。

自测: python scripts/_tick_watch.py --selftest
      (stub daemon + 快时钟常量 + 临时目录, 不发飞书, 约 3 分钟)
用法: python scripts/_tick_watch.py
      (YaobanTickDaemon 09:30 计划任务经 launch.ps1 -Mode tick 前台运行本进程;
       _restart_tick_daemon.py --spawn 手动链路兼容, 文件锁单实例)
"""
from __future__ import annotations
import argparse, json, os, pathlib, subprocess, sys, threading, time
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = pathlib.Path(__file__).resolve().parent
PY = sys.executable
TICK_SCRIPT = str(SCRIPTS / 'tick_monitor.py')
OUT = BASE / 'outputs' / 'intraday'
POS = OUT / 'pos_live.json'
TZ = ZoneInfo('Asia/Shanghai')

LUNCH_FREEZE_S, LUNCH_FREEZE_E = 690, 780   # 11:30-13:00 冻结 stale 判据(daemon 午休停写)
LUNCH_WRITE_CUT = 692                       # 上午最后合法写入分钟(11:32 前), 跨午休 age 扣 90min
CLOSE_NO_RESTART = 15 * 60 + 3              # 15:03 起 daemon missing/stale 不再重启
WATCH_EXIT_AT = 15 * 60 + 10                # 15:10 看门狗兜底退出


def now():
    return datetime.now(TZ)


def ts_now():
    return now().strftime('%H:%M:%S')


def day_str():
    return now().strftime('%Y-%m-%d')


def mins_now():
    t = now()
    return t.hour * 60 + t.minute


def append_event(ev, out):
    with (out / 'risk_events.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(ev, ensure_ascii=False) + '\n'); f.flush(); os.fsync(f.fileno())


def write_state(out, state, detail):
    p = out / 'tick_guard_state.json'
    tmp = p.with_name(p.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps({'date': day_str(), 'time': ts_now(), 'state': state,
                               'detail': detail}, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, p)


def feishu_alert(day, detail, enabled=True):
    """耗尽升级告警(每日一条, event_key 去重); 失败不阻塞守护——risk_events 已留痕。"""
    if not enabled:
        return
    try:
        sys.path.insert(0, str(SCRIPTS))
        from feishu_notify import send_text
        send_text(f"[tick守护] 重启额度耗尽 {day}: {detail}\n"
                  f"盘中风控已隔离(scan 对 tick stale 已 fail-closed 拒新仓), "
                  f"持仓守护停摆, 请人工介入(_restart_tick_daemon.py)。",
                  event_key=f"tick_guard_exhausted:{day}", kind="alert")
    except Exception:
        pass


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


def kill_pids(pids):
    for pid in pids:
        subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)


def spawn_daemon(cfg):
    d8 = day_str().replace('-', '')
    so = open(cfg['out'] / f'_daemon_{d8}.stdout.log', 'a', encoding='utf-8')
    se = open(cfg['out'] / f'_daemon_{d8}.stderr.log', 'a', encoding='utf-8')
    p = subprocess.Popen(cfg['daemon_argv'], cwd=str(BASE), stdout=so, stderr=se,
                         creationflags=0x00000008)
    return p.pid


def try_lock(out):
    """单实例互斥: O_EXCL 原子抢占; 持有者死亡(beat 停更>120s)由后来者接管。"""
    lock = out / '_tick_watch.lock'
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
        return True
    except FileExistsError:
        try:
            beat_age = time.time() - (out / '_tick_watch.beat').stat().st_mtime
        except FileNotFoundError:
            beat_age = 1e9
        # 2026-09-10 修复(双守护竞态): 新 watcher 首查可能先于旧 watcher 首个 beat 写出,
        # beat 缺失但锁文件新鲜(120s 内)时同样视为持有者存活——否则 scheduler 重启链会
        # 抢锁双写 pos_live(9/10 实录: 双 watcher+双 daemon, pos_live 交替 1/2 持仓)。
        lock_age = time.time() - lock.stat().st_mtime
        if beat_age > 120 and lock_age > 120:
            try:
                lock.unlink()
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode()); os.close(fd)
                return True
            except OSError:
                pass
        return False


def effective_age(pos: pathlib.Path, m_now: int, now_epoch=None):
    """pos_live 数据年龄, 跨午休扣除停写段; 文件缺失返回 None(视为无限陈旧, 由宽限保护)。

    now_epoch 可注入以便离线单测(凌晨跑 selftest 时当日白天时间戳在未来)。
    """
    now_epoch = time.time() if now_epoch is None else now_epoch
    try:
        mt = pos.stat().st_mtime
    except FileNotFoundError:
        return None
    age = now_epoch - mt
    w = datetime.fromtimestamp(mt, TZ)
    mw = w.hour * 60 + w.minute
    if mw < LUNCH_WRITE_CUT and m_now >= LUNCH_FREEZE_E:
        age -= (LUNCH_FREEZE_E - LUNCH_FREEZE_S) * 60  # 上午收盘后 daemon 按设计停写 90min
    return max(age, 0.0)


def _ev(trigger, action, detail):
    return {'date': day_str(), 'time': ts_now(), 'sym': '-', 'trigger': trigger,
            'action': action, 'detail': detail}


def run(cfg):
    out = cfg['out']
    out.mkdir(parents=True, exist_ok=True)
    if not try_lock(out):
        print('[watch] lock held by live watcher, exiting', flush=True)
        return 0
    write_state(out, 'armed', 'watchdog started')
    restarts = 0
    degraded = False
    recovered_logged = False
    boot = time.time()
    cur = spawn_daemon(cfg)
    append_event(_ev('watch_start', 'info', f'tick watchdog v2 up, daemon pid={cur}'), out)
    print(f'[watch] up, daemon pid={cur}', flush=True)
    try:
        while True:
            time.sleep(cfg['check_every'])
            m = mins_now()
            if m > cfg['exit_after']:
                append_event(_ev('watch_exit', 'info', 'past close, watchdog exit'), out)
                print('[watch] past close, exit', flush=True)
                break
            (out / '_tick_watch.beat').write_text(
                f"{ts_now()} restarts={restarts} degraded={int(degraded)}", encoding='ascii')
            in_lunch = cfg['lunch_freeze_s'] <= m < cfg['lunch_freeze_e']
            age = effective_age(cfg['pos'], m)
            stale = (age is None) or (age > cfg['stale_sec'])  # 文件缺失=无限陈旧(受宽限/午休保护)
            pids = find_pids(cfg['pid_pattern'])
            if degraded and not recovered_logged and pids and age is not None and age <= cfg['stale_sec']:
                recovered_logged = True
                append_event(_ev('watch_recovered', 'info',
                                 'daemon revived under degraded mode (manual?), observing without restart budget'), out)
            if not pids:  # 进程没了
                if m >= cfg['close_no_restart']:
                    append_event(_ev('watch_exit', 'info',
                                     'daemon self-exited near close, no restart (v2 close window)'), out)
                    print('[watch] daemon self-exited near close, exit', flush=True)
                    break
                if degraded:
                    continue  # 已隔离: 只观测不再重启
                cur = spawn_daemon(cfg); restarts += 1; boot = time.time()
                append_event(_ev('watch_restart', 'info',
                                 f'daemon missing, restart #{restarts} pid={cur}'), out)
                print(f'[watch] daemon missing, restart #{restarts}', flush=True)
                continue
            # 进程在 —— 唯一健康判据是数据在更新(pos_live mtime), 进程存在不算数
            if stale and not in_lunch:
                if m >= cfg['close_no_restart']:
                    kill_pids(pids)
                    append_event(_ev('watch_exit', 'info',
                                     f'pos_live stale {int(age) if age is not None else -1}s near close, killed, no restart (v2 close window)'), out)
                    print('[watch] stale daemon killed near close, exit', flush=True)
                    break
                if degraded:
                    continue
                if time.time() - boot < cfg['boot_grace']:
                    continue  # 启动宽限: 等 daemon 首写(TDX 连接+首轮最坏 ~40s)
                if restarts >= cfg['max_restarts']:
                    kill_pids(pids)
                    _ages = f"{int(age)}s" if age is not None else 'no-snapshot'
                    detail = f"{cfg['max_restarts']} restarts exhausted, pos_live stale {_ages}"
                    append_event(_ev('watch_limit', 'halt', detail), out)
                    write_state(out, 'restart_exhausted', detail)
                    feishu_alert(day_str(), detail, cfg['feishu'])
                    print('[watch] restarts exhausted -> degraded observe mode', flush=True)
                    degraded = True
                    continue
                kill_pids(pids)
                cur = spawn_daemon(cfg); restarts += 1; boot = time.time()
                _ager = f"{int(age)}s" if age is not None else 'no-snapshot'
                append_event(_ev('watch_restart', 'info',
                                 f'pos_live stale {_ager}, restart #{restarts} pid={cur}'), out)
                print(f'[watch] pos_live stale {_ager}, restart #{restarts}', flush=True)
    finally:
        try:
            (out / '_tick_watch.lock').unlink()
        except OSError:
            pass
    if not degraded:
        write_state(out, 'closed_ok', 'watchdog closed clean')
    return 6 if degraded else 0


def prod_cfg():
    return {'out': OUT, 'pos': POS,
            'daemon_argv': [PY, '-X', 'utf8', TICK_SCRIPT, '--daemon', '--interval', '5', '--execute-risk'],
            'pid_pattern': '*tick_monitor*--daemon*',
            'check_every': 20, 'stale_sec': 90, 'boot_grace': 150, 'max_restarts': 5,
            'close_no_restart': CLOSE_NO_RESTART, 'exit_after': WATCH_EXIT_AT,
            'lunch_freeze_s': LUNCH_FREEZE_S, 'lunch_freeze_e': LUNCH_FREEZE_E,
            'feishu': True}


# ---------------- selftest: stub daemon + 快时钟, 双场景冒烟(不发飞书) ----------------
STUB_DAEMON = r'''import json, pathlib, sys, time
pos = pathlib.Path(sys.argv[1]); ctl = pathlib.Path(sys.argv[2])
i = 0
while True:
    try:
        c = ctl.read_text(encoding='ascii').strip() if ctl.exists() else 'run'
    except OSError:
        c = 'run'
    if c == 'exit':
        sys.exit(0)
    if c == 'stall':
        time.sleep(3600); continue
    pos.write_text(json.dumps({'time': time.strftime('%H:%M:%S'), 'i': i}), encoding='ascii')
    i += 1
    time.sleep(2)
'''


def _events(out):
    p = out / 'risk_events.jsonl'
    if not p.exists():
        return []
    evs = []
    for line in p.read_text(encoding='utf-8').splitlines():
        try:
            evs.append(json.loads(line))
        except Exception:
            pass
    return evs


def _wait_minutes(target_mins, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if mins_now() >= target_mins:
            return True
        time.sleep(1)
    return False


def selftest():
    tdir = BASE / 'outputs' / f'_selftest_tick_watch_{now().strftime("%Y%m%d_%H%M%S")}'
    tdir.mkdir(parents=True, exist_ok=True)
    stub = tdir / 'stub_daemon.py'
    stub.write_text(STUB_DAEMON, encoding='utf-8')
    ok_all = True

    def _check(name, cond, detail=''):
        nonlocal ok_all
        print(f"[selftest] {'PASS' if cond else 'FAIL'} {name}" + (f" ({detail})" if detail else ''), flush=True)
        ok_all = ok_all and bool(cond)

    # ---- effective_age 单元断言(午休扣除; now_epoch 注入, 与真实墙钟无关) ----
    posU = tdir / 'pos_unit.json'
    posU.write_text('{}', encoding='ascii')

    def _set_mtime(h, m_):
        t = datetime(now().year, now().month, now().day, h, m_, 30, tzinfo=TZ).timestamp()
        os.utime(posU, (t, t))

    def _epoch(h, m_):
        return datetime(now().year, now().month, now().day, h, m_, 0, tzinfo=TZ).timestamp()

    _set_mtime(11, 29)
    a1 = effective_age(posU, m_now=782, now_epoch=_epoch(13, 2))   # 上午末写+午后判 → 扣90min
    _set_mtime(11, 0)
    a2 = effective_age(posU, m_now=782, now_epoch=_epoch(13, 2))   # 上午早段已死 → 扣后仍巨大
    _set_mtime(13, 5)
    a3 = effective_age(posU, m_now=786, now_epoch=_epoch(13, 6))   # 午后正常写入 → 不扣
    _check('effective_age lunch-cut', a1 is not None and a1 < 180 and a2 is not None and a2 > 1800 and a3 is not None and a3 < 180,
           f'a1={a1 and int(a1)}s a2={a2 and int(a2)}s a3={a3 and int(a3)}s')
    posU.unlink()

    # ---- 场景A: 正常路径(首写→持续更新→收盘自退→clean exit 0) ----
    posA, ctlA = tdir / 'posA.json', tdir / 'ctlA'
    ctlA.write_text('run', encoding='ascii')
    closeA, exitA = mins_now() + 1, mins_now() + 4
    cfgA = {'out': tdir / 'A', 'pos': posA,
            'daemon_argv': [PY, '-X', 'utf8', str(stub), str(posA), str(ctlA)],
            'pid_pattern': f'*{str(posA)}*',
            'check_every': 2, 'stale_sec': 6, 'boot_grace': 8, 'max_restarts': 5,
            'close_no_restart': closeA, 'exit_after': exitA,
            'lunch_freeze_s': LUNCH_FREEZE_S, 'lunch_freeze_e': LUNCH_FREEZE_E,
            'feishu': False}
    resA = {}
    thA = threading.Thread(target=lambda: resA.__setitem__('code', run(cfgA)), daemon=True)
    tA = time.time(); thA.start()
    _wait_minutes(closeA, timeout=240)
    ctlA.write_text('exit', encoding='ascii')
    thA.join(timeout=240)
    evA = _events(cfgA['out'])
    stA = json.loads((cfgA['out'] / 'tick_guard_state.json').read_text(encoding='utf-8')) \
        if (cfgA['out'] / 'tick_guard_state.json').exists() else {}
    _check('A exit code 0', resA.get('code') == 0, f"code={resA.get('code')}")
    _check('A zero restarts', not any(e['trigger'] == 'watch_restart' for e in evA))
    _check('A clean close-window exit',
           any(e['trigger'] == 'watch_exit' and 'near close' in e['detail'] for e in evA))
    _check('A state closed_ok', stA.get('state') == 'closed_ok', f"state={stA.get('state')}")

    # ---- 场景B: 宽限→挂死重启→额度耗尽(隔离+状态)→degraded 复活观测→exit 6 ----
    posB, ctlB = tdir / 'posB.json', tdir / 'ctlB'
    ctlB.write_text('stall', encoding='ascii')
    closeB, exitB = mins_now() + 1, mins_now() + 5
    cfgB = {'out': tdir / 'B', 'pos': posB,
            'daemon_argv': [PY, '-X', 'utf8', str(stub), str(posB), str(ctlB)],
            'pid_pattern': f'*{str(posB)}*',
            'check_every': 2, 'stale_sec': 5, 'boot_grace': 8, 'max_restarts': 2,
            'close_no_restart': closeB, 'exit_after': exitB,
            'lunch_freeze_s': LUNCH_FREEZE_S, 'lunch_freeze_e': LUNCH_FREEZE_E,
            'feishu': False}
    resB = {}
    first_restart_at = {}

    def _watch_b():
        resB['code'] = run(cfgB)

    thB = threading.Thread(target=_watch_b, daemon=True)
    tB = time.time(); thB.start()
    limit_seen_t = None
    deadline = time.time() + 150
    while time.time() < deadline and thB.is_alive():
        for e in _events(cfgB['out']):
            if e['trigger'] == 'watch_restart' and 'restart #1' in e['detail'] and 'r1' not in first_restart_at:
                first_restart_at['r1'] = time.time()
            if e['trigger'] == 'watch_limit' and limit_seen_t is None:
                limit_seen_t = time.time()
        if limit_seen_t is not None:
            break
        time.sleep(1)
    # 人工复活: ctl=run + 手动拉起裸 stub(模拟人工 daemon, watcher degraded 观测)
    ctlB.write_text('run', encoding='ascii')
    rev = subprocess.Popen([PY, '-X', 'utf8', str(stub), str(posB), str(ctlB)],
                           cwd=str(BASE), creationflags=0x00000008)
    _wait_minutes(closeB, timeout=240)
    ctlB.write_text('exit', encoding='ascii')  # 收盘窗口内自退 → v2 应不再重启
    thB.join(timeout=300)
    evB = _events(cfgB['out'])
    stB = json.loads((cfgB['out'] / 'tick_guard_state.json').read_text(encoding='utf-8')) \
        if (cfgB['out'] / 'tick_guard_state.json').exists() else {}
    restarts_b = [e for e in evB if e['trigger'] == 'watch_restart']
    _check('B exit code 6 (degraded)', resB.get('code') == 6, f"code={resB.get('code')}")
    _check('B boot-grace held (no restart before grace)',
           'r1' in first_restart_at and first_restart_at['r1'] - tB >= cfgB['boot_grace'] - 4,
           f"first_restart_after={first_restart_at.get('r1', 0) and round(first_restart_at['r1'] - tB, 1)}s grace={cfgB['boot_grace']}s")
    _check('B exactly max_restarts restarts', len(restarts_b) == cfgB['max_restarts'],
           f"restarts={len(restarts_b)}")
    _check('B watch_limit + halt', any(e['trigger'] == 'watch_limit' and e['action'] == 'halt' for e in evB))
    _check('B state restart_exhausted', stB.get('state') == 'restart_exhausted', f"state={stB.get('state')}")
    _check('B degraded recovery observed', any(e['trigger'] == 'watch_recovered' for e in evB))
    _check('B no restart after limit', not any(e['trigger'] == 'watch_restart' for e in evB
                                               if limit_seen_t is not None and e['time'] >= datetime.fromtimestamp(limit_seen_t, TZ).strftime('%H:%M:%S')))
    # 清理残余 stub 进程
    for pat in (f'*{str(posA)}*', f'*{str(posB)}*'):
        kill_pids(find_pids(pat))
    try:
        rev.kill()
    except Exception:
        pass
    print(f"[selftest] {'ALL PASS' if ok_all else 'FAILED'} (dir={tdir})", flush=True)
    return 0 if ok_all else 1


def main():
    ap = argparse.ArgumentParser(description='tick daemon watchdog v2')
    ap.add_argument('--selftest', action='store_true', help='stub daemon 双场景自测(不发飞书)')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    return run(prod_cfg())


if __name__ == '__main__':
    sys.exit(main())
