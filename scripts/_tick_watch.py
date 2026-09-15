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
  - ⚠️ 2026-09-12 R0.8：**该窗口整体后移**。daemon 自退从 15:05 改为 15:30
    （深交所 2026-07-06 起盘后固定价格交易扩围至全部 A 股 + ETF，15:05-15:30 可成交、
    成交价 = 当日收盘价；原实现这 30 分钟零守护）。故 CLOSE_NO_RESTART 15:03→15:33、
    WATCH_EXIT_AT 15:10→15:40，详见下方常量注释。tick 模式不带任何 Gate，
    因此**不涉及** run_trading_task.ps1 的 post_plan 锚点（用户裁定：不放宽它）。
  - 启动宽限 BOOT_GRACE(150s): 每次 spawn 后宽限期内不判 stale(等首写), 超过宽限
    仍无首写才判挂死。
  - 午休感知: 11:30-13:00 冻结 stale 判据; 跨午休的 age 扣除午休段(effective_age)。
  - 耗尽升级: watch_limit → tick_guard_state.json(state=restart_exhausted) + 飞书
    告警(每日至多一条) + degraded 观测模式(不再烧额度), 进程退出码 6。
  - 进程存活 ≠ 数据更新: 唯一健康判据是 pos_live.json mtime(数据在更新), 进程列表
    仅用于 missing 检测与 kill。
  - 状态机: armed(启动) → ok/restart_exhausted(耗尽, 保持到收盘) → closed_ok(正常收盘)。

v3 修复(2026-09-11 盘中事故, INC-2026-09-11-01):
  - **唤醒信号解耦**: daemon 每轮无条件写 `_tick_daemon.beat`(早于所有 continue 守卫),
    watcher 以"心跳新鲜"为存活判据; `pos_live` 陈旧降级为**数据信号**。旧实现把二者
    混为一谈, 于是"数据路径卡住"被判成"守护停摆"。
  - **degraded 可重估**: 双信号恢复新鲜即自动清除隔离并回补预算。旧实现 degraded 一置位
    到收盘都不再评估 → 9/11 10:37 之后 174 次 scan 全天 fail-closed。
  - **隔离态仍保命**: 进程真死时即使已隔离也必须重启(停新仓是交易侧的事, 不该牺牲持仓保护)。
  - **滑动窗口预算**: restarts 由"当日累计"改为 `restart_window_sec`(900s) 内 ≤ max_restarts。
  - **双 watcher 留痕**: try_lock 落败方写 `duplicate_watcher` 事件(9/11 09:30:04 两个 watcher)。
  - **状态双信号字段**: tick_guard_state.json 增 daemon_alive/tick_fresh/daemon_beat_age_s/
    tick_age_s/window_sec, 旧字段保留。
  - 状态机: armed → ok/restarting/recovered/empty_stale/restart_exhausted → closed_ok。

自测: python scripts/_tick_watch.py --selftest
      (stub daemon + 快时钟常量 + 临时目录 + 可注入进程探测后端, 不发飞书;
       A 正常收盘 / B 耗尽隔离+恢复 / C 预算回补 / D 残留锁清理 /
       E 心跳活但数据卡住 / F 隔离态进程真死仍重启, 约 8 分钟)
      2026-09-11: 进程探测改为 cfg['pid_lister'] 可注入 —— 原先直连 Get-CimInstance, 沙箱下
      被拒访问导致 stub 恒不可见, A/B 断言必然 FAIL(与改动无关), 等于本地无回归保护。
用法: python scripts/_tick_watch.py
      (EvoAlphaTickDaemon 09:30 计划任务经 launch.ps1 -Mode tick 前台运行本进程;
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
BEAT = OUT / '_tick_daemon.beat'   # 2026-09-11 P0: daemon 心跳（存活信号, 与 POS 数据信号分离）
TZ = ZoneInfo('Asia/Shanghai')

LUNCH_FREEZE_S, LUNCH_FREEZE_E = 690, 780   # 11:30-13:00 冻结 stale 判据(daemon 午休停写)
LUNCH_WRITE_CUT = 692                       # 上午最后合法写入分钟(11:32 前), 跨午休 age 扣 90min
CLOSE_NO_RESTART = 15 * 60 + 33              # 15:33 起 daemon missing/stale 不再重启
                                             # (2026-09-12 R0.8: 原 15:03。daemon 自退改为
                                             #  15:30 —— 盘后固定价格交易 15:05-15:30 需守护，
                                             #  15:33 = 窗口结束后留 3 分钟缓冲)
WATCH_EXIT_AT = 15 * 60 + 40                # 15:40 看门狗兜底退出
                                             # (2026-09-12 R0.8: 原 15:10，随窗口后移)


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


def write_state(out, state, detail, extra=None):
    """写守护状态。

    2026-09-11 P0: 新增双信号字段 —— 旧读者只看 state 字符串, 无法区分
    「进程真死」与「进程活着但数据路径卡住」(9/11 两者被混为一谈, 导致误报"守护停摆")。
    daemon_alive=心跳新鲜, tick_fresh=pos_live 新鲜, 二者独立。
    旧字段(date/time/state/detail)全部保留, 向后兼容。
    """
    p = out / 'tick_guard_state.json'
    payload = {'date': day_str(), 'time': ts_now(), 'state': state, 'detail': detail}
    if extra:
        # 注意: **不能**过滤 None —— 字段缺失会让读者把"未测量"误当成"健康"
        # (9/11 排障时最难的一步恰恰是"没有信号"与"信号正常"无法区分)。
        payload.update(extra)
    tmp = p.with_name(p.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, p)


def _over_budget(budget, cfg):
    """滑动窗口预算: 窗口内重启次数是否已达上限。

    2026-09-11 P0: 旧实现是"当日累计"计数器 —— 90 秒内被 3 次假重启推到 5 后永久保持,
    此后任何一次 stale 判定都直落隔离分支(9/11 实录: 07:41 之后再没被救)。
    改为滑动窗口后, 抖动不再等同于"当天完蛋", 只有窗口内持续失败才隔离。
    """
    now_e = time.time()
    window = cfg.get('restart_window_sec') or 900
    recent = [t for t in budget if now_e - t <= window]
    budget[:] = recent
    return len(recent) >= cfg['max_restarts']


def _tick_fresh_state(cfg, age, in_lunch):
    """数据信号: 午休冻结期不判陈旧(effective_age 已扣午休段)。"""
    return (age is not None) and (bool(in_lunch) or age <= cfg['stale_sec'])


def feishu_alert(day, detail, enabled=True, kind='exhausted'):
    """升级告警(按 kind 分别去重, 每日至多一条); 失败不阻塞守护——risk_events 已留痕。

    kind='missing'   : 进程真死且预算耗尽（此时"守护停摆"是真的, 应人工介入）
    kind='exhausted' : 数据路径持续卡住且预算耗尽（进程可能还活着, 文案不得谎称"停摆"）
    """
    if not enabled:
        return
    try:
        sys.path.insert(0, str(SCRIPTS))
        from feishu_notify import send_text
        if kind == 'missing':
            msg = (f"[tick守护] 守护进程已消失且重启预算耗尽 {day}: {detail}\n"
                   f"持仓保护停摆, 请人工介入(_restart_tick_daemon.py)。\n"
                   f"注: scan 对 tick stale 已 fail-closed 拒新仓。")
        else:
            msg = (f"[tick守护] 数据路径持续卡住且重启预算耗尽 {day}: {detail}\n"
                   f"持仓风控可能失去新鲜输入, 请检查 tick_monitor / TDX 可用性。\n"
                   f"注: 进程存活与数据新鲜已分别记录在 tick_guard_state.json。")
        send_text(msg, event_key=f"tick_guard_{kind}:{day}", kind="alert")
    except Exception:
        pass


def find_pids(pattern, lister=None):
    """列出命令行匹配 pattern 的 python 进程 pid(排除自身)。

    2026-09-11: 抽出 lister 后端以便注入 —— 默认走 Get-CimInstance, 但沙箱/受限权限下该
    查询会"拒绝访问"并静默返回空表, 于是 selftest 里 stub 进程恒不可见(A/B 场景 6 条断言
    必然 FAIL, 与改动无关)。把"探测后端"变成可注入参数后, selftest 与单测不再依赖真实进程
    可见性; 生产路径默认行为不变。
    """
    if lister is not None:
        try:
            return [int(x) for x in lister(pattern) if int(x) != os.getpid()]
        except Exception:
            return []
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command',
                              "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                              f"Where-Object {{$_.CommandLine -like '{pattern}'}} | "
                              'Select-Object -ExpandProperty ProcessId'],
                             capture_output=True, text=True, timeout=25)
        return [int(x) for x in (out.stdout or '').split() if int(x) != os.getpid()]
    except Exception:
        return []


def _cfg_lister(cfg):
    """cfg 内可注入的进程探测后端; 缺省回落到真实 CIM 查询(生产路径)。"""
    return cfg.get('pid_lister')


def _list_cfg_pids(cfg):
    return find_pids(cfg['pid_pattern'], _cfg_lister(cfg))


def kill_pids(pids):
    for pid in pids:
        subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)


def _clear_dead_daemon_lock(cfg, alive_pids):
    """清理锁内 pid 已不存在的残留锁(活锁绝不误删)。

    2026-09-11 修复: tick_monitor 只在启动时抢一次 daemon 单实例锁, 而 watcher kill 旧 daemon
    后锁文件会留在盘上; 旧 tick_monitor 仅在锁 mtime 陈旧 >120s 时才抢占, 于是"kill → 立刻
    spawn"必然被死锁文件拒绝(9/11 实录: restart #4/#5/#6 三连 spawn 全部秒退, 只剩
    "已有活 daemon 持有单实例锁", 白烧 3 次额度且期间持仓裸奔)。
    """
    lock = cfg['out'] / '_tick_daemon.lock'
    try:
        holder = int(lock.read_text(encoding='ascii').strip() or 0)
    except (OSError, ValueError):
        return
    if holder and holder not in alive_pids and holder != os.getpid():
        try:
            lock.unlink()
        except OSError:
            pass


def spawn_daemon(cfg):
    d8 = day_str().replace('-', '')
    _clear_dead_daemon_lock(cfg, _list_cfg_pids(cfg))
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


def beat_age(beat: pathlib.Path, now_epoch=None):
    """daemon 心跳年龄（秒）; 文件缺失返回 None。

    2026-09-11 P0: 心跳由 tick_monitor 每轮无条件写（早于所有 continue 守卫），
    因此"心跳新鲜"直接等价于"循环还在转"，与 pos_live 是否落盘彻底解耦。
    注意：**不扣午休** —— daemon 午休期间仍在 60s 空闲循环里写心跳，
    心跳停更在任何时段都是真死亡信号（这正是不复用 effective_age 的原因）。
    """
    now_epoch = time.time() if now_epoch is None else now_epoch
    try:
        mt = beat.stat().st_mtime
    except OSError:
        return None
    return max(now_epoch - mt, 0.0)


def _ev(trigger, action, detail):
    return {'date': day_str(), 'time': ts_now(), 'sym': '-', 'trigger': trigger,
            'action': action, 'detail': detail}


def _state_extras(cfg, daemon_alive, tick_fresh, b_age, age, degraded, restarts, budget):
    """双信号状态字段（2026-09-11 P0 新增；旧字段保留，向后兼容）。

    刻意**不做 bool() 归一化**: None 表示"本轮未测量"(如 armed/closed_ok 阶段),
    与 False("测到了, 是坏的")是不同语义 —— 把未测量报成 False 会让读者误判守护故障。
    """
    return {'daemon_alive': daemon_alive, 'tick_fresh': tick_fresh,
            'daemon_beat_age_s': int(b_age) if b_age is not None else None,
            'tick_age_s': int(age) if age is not None else None,
            'degraded': bool(degraded), 'restarts_window': len(budget),
            'window_sec': cfg.get('restart_window_sec') or cfg.get('stale_sec')}


def _daemon_status(cfg, pids):
    """(daemon_alive, beat_age) —— 双信号中的**存活信号**。

    主判据是心跳新鲜（任何 continue 守卫都拦不住心跳，因此它诚实地等价于"循环在转"）。
    仅当心跳文件**从未出现过**（旧 daemon / 未升级环境）才回退到进程探测；
    一旦心跳存在，绝不用 pids 去"或"它 —— 否则 pids 查询失败返回空表时会把活进程判死。
    """
    beat = cfg.get('beat') or BEAT
    b_age = beat_age(beat)
    limit = cfg.get('daemon_stale_sec', 60)
    if beat.exists():
        return (b_age is not None and b_age <= limit), b_age
    return bool(pids), b_age


def _has_positions():
    """当前是否持仓（空仓时守护收益为 0，数据陈旧只告警不烧重启预算）。失败按"有持仓"保守处理。"""
    try:
        sys.path.insert(0, str(BASE / 'portfolio'))
        from ledger import load
        return bool((load().get('account') or {}).get('positions'))
    except Exception:
        return True


def run(cfg):
    out = cfg['out']
    out.mkdir(parents=True, exist_ok=True)
    if not try_lock(out):
        # 2026-09-11: 落一条事件便于复盘识别"同秒双 watcher"(9/11 09:30:04 实录两个 watcher
        # 并存, 各自 spawn+计数, 把重启预算更快推向阈值)。原实现只 print 不留痕。
        try:
            append_event(_ev('duplicate_watcher', 'info',
                             f'watcher lock held, this instance exits (pid={os.getpid()})'), out)
        except Exception:
            pass
        print('[watch] lock held by live watcher, exiting', flush=True)
        return 0
    restarts = 0
    budget = []          # 滑动窗口内的重启时刻（2026-09-11: 替代"当日累计"计数器）
    degraded = False
    boot = time.time()
    last_detail = ''
    cur = spawn_daemon(cfg)
    write_state(out, 'armed', 'watchdog started',
                _state_extras(cfg, True, None, None, None, False, restarts, budget))
    append_event(_ev('watch_start', 'info', f'tick watchdog v3 up, daemon pid={cur}'), out)
    print(f'[watch] up, daemon pid={cur}', flush=True)

    def save(state, detail, daemon_alive, tick_fresh, b_age, age):
        nonlocal last_detail
        last_detail = detail
        write_state(out, state, detail,
                    _state_extras(cfg, daemon_alive, tick_fresh, b_age, age, degraded, restarts, budget))

    try:
        while True:
            time.sleep(cfg['check_every'])
            m = mins_now()
            if m > cfg['exit_after']:
                append_event(_ev('watch_exit', 'info', 'past close, watchdog exit'), out)
                print('[watch] past close, exit', flush=True)
                break
            (out / '_tick_watch.beat').write_text(
                f"{ts_now()} restarts={len(budget)} degraded={int(degraded)}", encoding='ascii')
            in_lunch = cfg['lunch_freeze_s'] <= m < cfg['lunch_freeze_e']
            age = effective_age(cfg['pos'], m)                   # 数据信号(可跨午休扣除)
            pids = _list_cfg_pids(cfg)
            daemon_alive, b_age = _daemon_status(cfg, pids)
            # tick_fresh: 午休冻结期不判陈旧(effective_age 已扣午休段)
            tick_fresh = _tick_fresh_state(cfg, age, in_lunch)
            # 预算回补: 数据新鲜且窗口内无重启需求才清零。保留 pids 条件 —— 进程探测自身失效时
            # 预算耗尽正是"探测失效"的安全阀, 不能在返回空表时回补(否则无限 respawn 且永不升级告警)。
            if pids and tick_fresh and budget:
                budget = []
                restarts = 0

            # ---- 恢复: 隔离态必须可重估（9/11 隔离一置位就再没被清过 → 174 次 scan 冻结）----
            if degraded and daemon_alive and tick_fresh:
                degraded = False
                budget = []
                boot = time.time()
                append_event(_ev('watch_recovered', 'info',
                                 'degraded cleared: daemon alive and tick data fresh again'), out)
                save('recovered', 'daemon + tick both fresh, restart budget restored',
                     True, True, b_age, age)
                print('[watch] degraded cleared -> recovered', flush=True)

            # ⚠️⚠️ 2026-09-14 修复(首日「绝食」故障同族, 与 tick_monitor 的 pos_live 缩进是一对):
            #   原判据 `b_age is None` 只在**心跳文件从未存在**时成立; 而 daemon 一旦死掉,
            #   心跳文件仍然在(只是不再刷新) ⇒ b_age 是"越来越大的数"而非 None
            #   ⇒ 本分支永不命中 ⇒ 落到下面的数据分支 ⇒ 空仓时被 `empty_ok` 判成
            #   「只告警、不烧预算」⇒ **空仓 + daemon 真死 = 永不重启** ⇒ pos_live 永远陈旧
            #   ⇒ scan 的 companion_health 恒 rc=6「禁止新仓」⇒ 空仓→不重启→禁买→永远空仓。
            #   今日实录: 手动 kill 掉 daemon 后 90s 内看门狗一次都没拉起, 走的正是这条路。
            #   修法: 「心跳停了 > daemon_stale_sec」等同**进程真死** —— 有持仓时本就该这么判,
            #   空仓时同样必须重启(监控链活着才允许开新仓); 预算限速与隔离语义**不变**。
            if not pids and (b_age is None or b_age > cfg.get('daemon_stale_sec', 60)):
                # 进程没了(且无心跳) —— 进程真死
                if m >= cfg['close_no_restart']:
                    append_event(_ev('watch_exit', 'info',
                                     'daemon self-exited near close, no restart (v2 close window)'), out)
                    print('[watch] daemon self-exited near close, exit', flush=True)
                    break
                if _over_budget(budget, cfg):
                    if not degraded:
                        degraded = True
                        append_event(_ev('watch_limit', 'halt',
                                         f'daemon missing and restart budget exhausted '
                                         f'({len(budget)}/{cfg["max_restarts"]} in {cfg["restart_window_sec"]}s)'), out)
                        save('restart_exhausted', 'daemon missing, restart budget exhausted',
                             False, False, b_age, age)
                        feishu_alert(day_str(),
                                     f'守护进程已消失且重启预算耗尽（{len(budget)}/{cfg["max_restarts"]}）'
                                     f'—— 持仓保护停摆，请人工介入(_restart_tick_daemon.py)。',
                                     cfg['feishu'], kind='missing')
                    continue
                # 2026-09-11 P1: 隔离态下"进程真死"仍必须重启 —— 停新仓是交易侧的事,
                # 不该以牺牲持仓保护为代价(旧实现 degraded → continue 造成整段守护真空)。
                kill_pids(pids)
                cur = spawn_daemon(cfg)
                budget.append(time.time())
                restarts = len(budget)
                boot = time.time()
                append_event(_ev('watch_restart', 'info',
                                 f'daemon missing, restart #{restarts} pid={cur}'), out)
                print(f'[watch] daemon missing, restart #{restarts}', flush=True)
                save('restarting', f'daemon missing, restart #{restarts}', False, False, b_age, age)
                continue

            # 进程在(或心跳新鲜)
            if not tick_fresh and not in_lunch:
                if m >= cfg['close_no_restart']:
                    kill_pids(pids)
                    append_event(_ev('watch_exit', 'info',
                                     f'pos_live stale {int(age) if age is not None else -1}s near close, killed, no restart (v2 close window)'), out)
                    print('[watch] stale daemon killed near close, exit', flush=True)
                    break
                if time.time() - boot < cfg['boot_grace']:
                    continue  # 启动宽限: 等 daemon 首写(TDX 连接+首轮最坏 ~40s)
                stall = int(age) if age is not None else -1
                has_pos = (cfg.get('has_positions_fn') or _has_positions)()
                if not has_pos and cfg.get('empty_ok', True):
                    # 空仓: 守护收益为 0, 只告警不烧预算(采纳 INC-2026-09-11-01 §7.5.2)
                    if not degraded:
                        append_event(_ev('tick_empty_stale', 'warn',
                                         f'空仓且 pos_live 陈旧 {stall}s —— 只告警, 不消耗重启预算'), out)
                        degraded = True
                        save('empty_stale', f'空仓且 pos_live 陈旧 {stall}s（不重启）',
                             daemon_alive, False, b_age, age)
                    continue
                if _over_budget(budget, cfg):
                    if not degraded:
                        degraded = True
                        detail = (f'pos_live stale {stall}s 且重启预算耗尽 '
                                  f'({len(budget)}/{cfg["max_restarts"]} in {cfg["restart_window_sec"]}s)')
                        append_event(_ev('watch_limit', 'halt', detail), out)
                        save('restart_exhausted', detail, daemon_alive, False, b_age, age)
                        feishu_alert(day_str(), detail, cfg['feishu'], kind='exhausted')
                        print('[watch] restarts exhausted -> degraded observe mode', flush=True)
                    continue
                # 数据路径卡住(守护进程可能活着, 但止损坏在同一个被跳过的分支里) → 必须重跑
                kill_pids(pids)
                cur = spawn_daemon(cfg)
                budget.append(time.time())
                restarts = len(budget)
                boot = time.time()
                append_event(_ev('watch_restart', 'info',
                                 f'pos_live stale {stall}s, restart #{restarts} pid={cur}'), out)
                print(f'[watch] pos_live stale {stall}s, restart #{restarts}', flush=True)
                save('restarting', f'pos_live stale {stall}s, restart #{restarts}',
                     daemon_alive, False, b_age, age)
                continue
            # 双信号健康
            if not degraded:
                save('ok', 'daemon alive and tick data fresh', True, True, b_age, age)
    finally:
        try:
            (out / '_tick_watch.lock').unlink()
        except OSError:
            pass
    # 当日结论: 收盘时仍处隔离态才写非 closed_ok（保持"当天结论"可读）
    if not degraded:
        save('closed_ok', 'watchdog closed clean', True, None, None, None)
    else:
        save('restart_exhausted', last_detail or 'closed while degraded', None, None, None, None)
    return 6 if degraded else 0


def prod_cfg():
    return {'out': OUT, 'pos': POS, 'beat': BEAT,
            'daemon_argv': [PY, '-X', 'utf8', TICK_SCRIPT, '--daemon', '--interval', '5', '--execute-risk'],
            'pid_pattern': '*tick_monitor*--daemon*',
            'check_every': 20, 'stale_sec': 90, 'boot_grace': 150, 'max_restarts': 5,
            # 2026-09-11 P0 新增: 存活判据与预算窗口
            'daemon_stale_sec': 60,      # 心跳超过此龄 = 进程真死(daemon 每 5s 一跳, 60s 余量充足)
            'write_stall_kill_after': 120,  # 进程活但数据停写超过此龄 = 数据路径卡住 → 重跑 daemon
            'restart_window_sec': 900,   # 重启预算改为滑动窗口: 900s 内最多 max_restarts 次
            'empty_ok': True,            # 空仓时 pos_live 陈旧只告警不烧预算
            'close_no_restart': CLOSE_NO_RESTART, 'exit_after': WATCH_EXIT_AT,
            'lunch_freeze_s': LUNCH_FREEZE_S, 'lunch_freeze_e': LUNCH_FREEZE_E,
            'feishu': True}


# ---------------- selftest: stub daemon + 快时钟, 多场景冒烟(不发飞书) ----------------
# 2026-09-11: stub 首件事是把自己的 pid 写进 pidfile —— selftest 的进程探测后端
# (cfg['pid_lister']) 据此伪造"存活表", 于是断言不再依赖沙箱里不可用的 Get-CimInstance。
STUB_DAEMON = r'''import json, os, pathlib, sys, time
pos = pathlib.Path(sys.argv[1]); ctl = pathlib.Path(sys.argv[2])
pidf = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else None
beat = pathlib.Path(sys.argv[4]) if len(sys.argv) > 4 else None
if pidf is not None:
    pidf.write_text(str(os.getpid()), encoding='ascii')
i = 0
while True:
    try:
        c = ctl.read_text(encoding='ascii').strip() if ctl.exists() else 'run'
    except OSError:
        c = 'run'
    if c == 'exit':
        if pidf is not None:
            try:
                pidf.unlink()
            except OSError:
                pass
        sys.exit(0)
    # 心跳: 与 ctl 无关, 每轮无条件写 —— 用于验证"进程活着但数据路径卡住"与"进程真死"的区分。
    # ctl=stall 时只写心跳不写 pos_live, 精确复刻 9/11 的失明形态。
    if beat is not None:
        try:
            beat.write_text(json.dumps({'datetime': time.strftime('%Y-%m-%d %H:%M:%S'),
                                        'pid': os.getpid()}), encoding='ascii')
        except OSError:
            pass
    if c == 'stall':
        time.sleep(3600); continue
    pos.write_text(json.dumps({'time': time.strftime('%H:%M:%S'), 'i': i}), encoding='ascii')
    i += 1
    time.sleep(2)
'''


def _pidfile_lister(pidf):
    """selftest 用进程探测后端: stub 存活则 pidfile 在, 反之视为已退出。"""
    def _lister(_pattern):
        try:
            return [int(pidf.read_text(encoding='ascii').strip())]
        except (OSError, ValueError):
            return []
    return _lister


def _always_positions():
    """selftest 用: 断言"有持仓", 使空仓免重启分支不干扰预算/隔离类断言。

    生产路径默认走真实账本(_has_positions), 该注入点只服务 selftest 的确定性。
    """
    return True


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


def _selftest_body(tdir, pidfiles):
    """场景实现体; stub 的 pidfile 路径登记进 pidfiles 供外层兜底清理。"""
    tdir.mkdir(parents=True, exist_ok=True)
    pidfiles[:] = [tdir / 'pidA.txt', tdir / 'pidB.txt', tdir / 'pidC.txt',
                   tdir / 'pidE.txt', tdir / 'pidF.txt']
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

    # ---- beat_age 单元断言: 独立性(不扣午休)与缺失语义 ----
    beatU = tdir / 'beat_unit'
    _check('beat_age 缺失返回 None', beat_age(beatU) is None)
    beatU.write_text('{}', encoding='ascii')
    os.utime(beatU, (_epoch(13, 5), _epoch(13, 5)))
    _check('beat_age 正常计算', abs((beat_age(beatU, now_epoch=_epoch(13, 6)) or -1) - 60) < 2,
           f"age={beat_age(beatU, now_epoch=_epoch(13, 6))}")
    # 与 effective_age 的关键差异: 跨午休**不扣**90min(daemon 午休仍每轮写心跳)
    _set_ut = _epoch(11, 29)
    os.utime(beatU, (_set_ut, _set_ut))
    b_cross = beat_age(beatU, now_epoch=_epoch(13, 2))
    _check('beat_age 不扣午休(与 effective_age 语义不同)', b_cross is not None and b_cross > 1800,
           f'age={b_cross and int(b_cross)}s')
    beatU.unlink()

    # ---- 场景A: 正常路径(首写→持续更新→收盘自退→clean exit 0) ----
    posA, ctlA, pidfA, beatA = tdir / 'posA.json', tdir / 'ctlA', tdir / 'pidA.txt', tdir / 'beatA'
    ctlA.write_text('run', encoding='ascii')
    closeA, exitA = mins_now() + 1, mins_now() + 4
    cfgA = {'out': tdir / 'A', 'pos': posA, 'beat': beatA,
            'daemon_argv': [PY, '-X', 'utf8', str(stub), str(posA), str(ctlA), str(pidfA), str(beatA)],
            'pid_pattern': f'*{str(posA)}*', 'pid_lister': _pidfile_lister(pidfA),
            'has_positions_fn': _always_positions,
            'check_every': 2, 'stale_sec': 6, 'boot_grace': 8, 'max_restarts': 5,
            'daemon_stale_sec': 6, 'restart_window_sec': 900,
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

    # ---- 场景B: 宽限→挂死重启→预算耗尽(隔离+状态)→守护复活→degraded 被清除→exit 0 ----
    # 2026-09-11 P0 语义变更: 隔离态在"心跳与数据双双新鲜"后必须**自动清除**(旧实现
    # 一置位就再没被清过, 导致 9/11 10:37 之后 174 次 scan 全天 fail-closed)。
    # 因此本场景的终局由"带着 degraded 收盘(exit 6)"改为"恢复后正常收盘(exit 0)"。
    posB, ctlB, pidfB, beatB = tdir / 'posB.json', tdir / 'ctlB', tdir / 'pidB.txt', tdir / 'beatB'
    ctlB.write_text('stall', encoding='ascii')
    closeB, exitB = mins_now() + 1, mins_now() + 5
    cfgB = {'out': tdir / 'B', 'pos': posB, 'beat': beatB,
            'daemon_argv': [PY, '-X', 'utf8', str(stub), str(posB), str(ctlB), str(pidfB), str(beatB)],
            'pid_pattern': f'*{str(posB)}*', 'pid_lister': _pidfile_lister(pidfB),
            'has_positions_fn': _always_positions,
            'check_every': 2, 'stale_sec': 5, 'boot_grace': 8, 'max_restarts': 2,
            'daemon_stale_sec': 5, 'restart_window_sec': 900,
            'close_no_restart': closeB, 'exit_after': exitB,
            'lunch_freeze_s': LUNCH_FREEZE_S, 'lunch_freeze_e': LUNCH_FREEZE_E,
            'feishu': False}
    resB = {}
    first_restart_at = {}

    def _watch_b():
        resB['code'] = run(cfgB)

    # ⚠️ 夹具修正(2026-09-13): **先自起 stub, 再启动 watcher**。
    # 原实现让 watcher 自己 spawn, 而夹具的进程发现后端读 **pidfile**(stub 首写要几百 ms)
    # ⇒ 存在"进程已在、pidfile 未写"的窗口, 该窗口会命中 `not pids and b_age is None`
    # 的「进程真死」分支 —— 而该分支**刻意不设宽限**(:416-417: 持仓保护优先于限速)。
    # 后果: 下面的"宽限未被突破"断言变成**计时彩票**(实测 first_restart_after=3.2s / grace=8s
    # 时红、更晚时绿, 同一份代码两种结果)。先起 stub 后 watcher 才能真正走到
    # 「进程活 + 数据陈旧」分支 —— 那才是宽限该管的情形。
    _preB = subprocess.Popen([PY, '-X', 'utf8', str(stub), str(posB), str(ctlB), str(pidfB), str(beatB)],
                             cwd=str(BASE), creationflags=0x00000008)
    for _ in range(50):                     # 等 stub 写下 pidfile(≤5s)
        if pidfB.exists():
            break
        time.sleep(0.1)

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
    # 人工复活: ctl=run + 手动拉起裸 stub(模拟人工 daemon) —— 数据恢复新鲜后
    # watcher 必须清除 degraded 并恢复预算(P0-2)。
    ctlB.write_text('run', encoding='ascii')
    rev = subprocess.Popen([PY, '-X', 'utf8', str(stub), str(posB), str(ctlB), str(pidfB), str(beatB)],
                           cwd=str(BASE), creationflags=0x00000008)
    _wait_minutes(closeB, timeout=240)
    ctlB.write_text('exit', encoding='ascii')  # 收盘窗口内自退 → v2 应不再重启
    thB.join(timeout=300)
    evB = _events(cfgB['out'])
    stB = json.loads((cfgB['out'] / 'tick_guard_state.json').read_text(encoding='utf-8')) \
        if (cfgB['out'] / 'tick_guard_state.json').exists() else {}
    restarts_b = [e for e in evB if e['trigger'] == 'watch_restart']
    _check('B boot-grace held (no restart before grace)',
           'r1' in first_restart_at and first_restart_at['r1'] - tB >= cfgB['boot_grace'] - 4,
           f"first_restart_after={first_restart_at.get('r1', 0) and round(first_restart_at['r1'] - tB, 1)}s grace={cfgB['boot_grace']}s")
    _check('B exactly max_restarts restarts', len(restarts_b) == cfgB['max_restarts'],
           f"restarts={len(restarts_b)}")
    _check('B watch_limit + halt', any(e['trigger'] == 'watch_limit' and e['action'] == 'halt' for e in evB))
    _check('B degraded recovery observed', any(e['trigger'] == 'watch_recovered' for e in evB))
    _check('B state cleared to closed_ok (degraded 不残留)', stB.get('state') == 'closed_ok',
           f"state={stB.get('state')}")
    _check('B exit code 0 (隔离已恢复, 不再带 degraded 收盘)', resB.get('code') == 0,
           f"code={resB.get('code')}")
    _check('B no restart after limit', not any(e['trigger'] == 'watch_restart' for e in evB
                                               if limit_seen_t is not None and e['time'] >= datetime.fromtimestamp(limit_seen_t, TZ).strftime('%H:%M:%S')))

    # ---- 场景C: 重启预算在守护恢复健康后回补(2026-09-11 修复的回归保护) ----
    # 缺陷原状: restarts 是"当日累计"计数器, 一旦 >=max_restarts 就永久保持, 此后任何 stale
    # 判定都直接走 kill+restart_exhausted 隔离 —— "先抖动几次, 之后真故障反而不再被救"。
    # 修复: 确认守护健康(pids 非空 + age<=stale_sec)即回补预算。本场景必须让计数器先涨到 >0。
    posC, ctlC, pidfC, beatC = tdir / 'posC.json', tdir / 'ctlC', tdir / 'pidC.txt', tdir / 'beatC'
    ctlC.write_text('stall', encoding='ascii')   # 先制造一次 stale 重启
    closeC, exitC = mins_now() + 2, mins_now() + 5
    cfgC = {'out': tdir / 'C', 'pos': posC, 'beat': beatC,
            'daemon_argv': [PY, '-X', 'utf8', str(stub), str(posC), str(ctlC), str(pidfC), str(beatC)],
            'pid_pattern': f'*{str(posC)}*', 'pid_lister': _pidfile_lister(pidfC),
            'has_positions_fn': _always_positions,
            'check_every': 1, 'stale_sec': 3, 'boot_grace': 6, 'max_restarts': 5,
            'close_no_restart': closeC, 'exit_after': exitC,
            'lunch_freeze_s': LUNCH_FREEZE_S, 'lunch_freeze_e': LUNCH_FREEZE_E,
            'feishu': False}
    resC = {}
    thC = threading.Thread(target=lambda: resC.__setitem__('code', run(cfgC)), daemon=True)
    thC.start()
    first_restart_c = None
    deadline_c = time.time() + 90
    while time.time() < deadline_c:
        for e in _events(cfgC['out']):
            if e['trigger'] == 'watch_restart' and first_restart_c is None:
                first_restart_c = e
        if first_restart_c is not None:
            break
        time.sleep(0.5)
    ctlC.write_text('run', encoding='ascii')     # 新 stub 随即写 pos_live → 转入健康
    reset_seen = False
    deadline_c2 = time.time() + 60
    while time.time() < deadline_c2:
        try:
            if 'restarts=0' in (cfgC['out'] / '_tick_watch.beat').read_text(encoding='ascii'):
                reset_seen = True
                break
        except OSError:
            pass
        time.sleep(0.5)
    _wait_minutes(closeC, timeout=180)
    ctlC.write_text('exit', encoding='ascii')
    thC.join(timeout=180)
    evC = _events(cfgC['out'])
    _check('C 前置: 出现过一次重启',
           first_restart_c is not None and 'restart #1' in (first_restart_c or {}).get('detail', ''),
           f"detail={(first_restart_c or {}).get('detail')}")
    _check('C 健康后重启预算回补(beat restarts=0)', reset_seen)
    _check('C 未误判耗尽(无 watch_limit)', not any(e['trigger'] == 'watch_limit' for e in evC))

    # ---- 场景D: spawn 前清理残留锁(活锁绝不误删) ----
    # 用假 Popen + 注入探测后端, 不依赖任务管理器是否看得到子进程。
    outD = tdir / 'D'
    outD.mkdir(parents=True, exist_ok=True)
    lockD = outD / '_tick_daemon.lock'
    orig_popen = subprocess.Popen
    subprocess.Popen = lambda *a, **k: type('FakeProc', (), {'pid': 999999})()
    try:
        cfgD = {'out': outD, 'pid_pattern': '*selftest*', 'pid_lister': lambda pat: [111],
                'daemon_argv': [PY, '-X', 'utf8', str(stub)]}
        lockD.write_text('555555', encoding='ascii')          # 锁内 pid 不在存活表(已被 kill)
        spawn_daemon(cfgD)
        _check('D 死 pid 残留锁被清理', not lockD.exists())
        lockD.write_text('111', encoding='ascii')             # 锁内 pid 仍是活 daemon
        spawn_daemon(cfgD)
        _check('D 活 daemon 锁不被误删', lockD.exists() and lockD.read_text(encoding='ascii') == '111')
    finally:
        subprocess.Popen = orig_popen
        try:
            lockD.unlink()
        except OSError:
            pass

    # ---- 场景E: 心跳新鲜 + pos_live 不写 —— 今晨失明形态的**正向验证** ----
    # 复刻 9/11 10:35:42 之后的真实形态: daemon 活着(心跳在跳)但数据路径不再落盘。
    # 旧实现把这种形态判成"守护停摆"并误报人工介入; 新实现必须:
    #   ① 判定为"进程活/数据卡住"而非进程死; ② 走"重跑数据路径"恢复;
    #   ③ 恢复后清掉隔离; ④ 状态文件双信号字段齐备。
    posE, ctlE, pidfE, beatE = tdir / 'posE.json', tdir / 'ctlE', tdir / 'pidE.txt', tdir / 'beatE'
    ctlE.write_text('stall', encoding='ascii')   # 只写心跳, 不写 pos_live
    closeE, exitE = mins_now() + 2, mins_now() + 5
    cfgE = {'out': tdir / 'E', 'pos': posE, 'beat': beatE,
            'daemon_argv': [PY, '-X', 'utf8', str(stub), str(posE), str(ctlE), str(pidfE), str(beatE)],
            'pid_pattern': f'*{str(posE)}*', 'pid_lister': _pidfile_lister(pidfE),
            'has_positions_fn': _always_positions,
            'check_every': 2, 'stale_sec': 5, 'boot_grace': 0,
            # max_restarts=1 + 短窗口: 让 E 先走到 degraded(否则预算在耗尽前就被回补,
            # 恢复分支根本不会被触达, "恢复后清隔离"的断言就成了空转)。
            'max_restarts': 1, 'daemon_stale_sec': 5, 'restart_window_sec': 8,
            'close_no_restart': closeE, 'exit_after': exitE,
            'lunch_freeze_s': LUNCH_FREEZE_S, 'lunch_freeze_e': LUNCH_FREEZE_E,
            'feishu': False}
    resE = {}
    thE = threading.Thread(target=lambda: resE.__setitem__('code', run(cfgE)), daemon=True)
    thE.start()
    saw_alive_but_stale = False
    restarted_for_stall = False
    observed = {'daemon_alive': None, 'tick_fresh': None}
    # 阶段①: 观察"进程活但数据陈旧"被判出, 且数据路径被重跑
    deadline_e = time.time() + 60
    while time.time() < deadline_e:
        try:
            st_e = json.loads((cfgE['out'] / 'tick_guard_state.json').read_text(encoding='utf-8'))
        except Exception:
            st_e = None
        if st_e and st_e.get('daemon_alive') is True and st_e.get('tick_fresh') is False:
            saw_alive_but_stale = True
            # 记录**判定成立当时**的字段值, 而不是终局快照(终局可能已被后续写入覆盖)
            observed = {'daemon_alive': st_e.get('daemon_alive'), 'tick_fresh': st_e.get('tick_fresh')}
        if any(e['trigger'] == 'watch_restart' for e in _events(cfgE['out'])):
            restarted_for_stall = True
            break
        time.sleep(0.5)
    # 阶段②: 等隔离(degraded)出现 —— 预算 1 + 窗口 8s, 卡住阶段必然耗尽
    degraded_seen = False
    deadline_e2 = time.time() + 45
    while time.time() < deadline_e2:
        try:
            st_e = json.loads((cfgE['out'] / 'tick_guard_state.json').read_text(encoding='utf-8'))
            if st_e.get('degraded') is True:
                degraded_seen = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    # 阶段③: 恢复写盘 → 双信号转健康 → 隔离必须被清除
    ctlE.write_text('run', encoding='ascii')
    recovered = False
    deadline_e3 = time.time() + 60
    while time.time() < deadline_e3:
        if any(e['trigger'] == 'watch_recovered' for e in _events(cfgE['out'])):
            recovered = True
            break
        time.sleep(0.5)
    _wait_minutes(closeE, timeout=180)
    ctlE.write_text('exit', encoding='ascii')
    thE.join(timeout=180)
    evE = _events(cfgE['out'])
    stE = json.loads((cfgE['out'] / 'tick_guard_state.json').read_text(encoding='utf-8'))
    _check('E 区分"进程活/数据卡住"(daemon_alive=true 且 tick_fresh=false)',
           saw_alive_but_stale and observed['tick_fresh'] is False,
           f"observed tick_fresh={observed['tick_fresh']!r} alive={observed['daemon_alive']!r}")
    _check('E 数据卡住时确实重跑了数据路径', restarted_for_stall,
           f"restarts={sum(1 for e in evE if e['trigger'] == 'watch_restart')}")
    _check('E 数据持续卡住会进入隔离', degraded_seen)
    _check('E 恢复后隔离被清除', recovered and any(e['trigger'] == 'watch_recovered' for e in evE))
    _check('E 状态文件双信号字段齐备',
           all(k in stE for k in ('daemon_alive', 'tick_fresh', 'daemon_beat_age_s', 'tick_age_s', 'window_sec')),
           f"keys={sorted(stE.keys())}")

    # ---- 场景F: 隔离态下"进程真死"仍必须重启(持仓保护不得真空) ----
    # 旧实现 degraded → continue, 于是隔离后进程死掉也不再拉起 —— 守护真空到收盘。
    posF, ctlF, pidfF, beatF = tdir / 'posF.json', tdir / 'ctlF', tdir / 'pidF.txt', tdir / 'beatF'
    ctlF.write_text('stall', encoding='ascii')
    closeF, exitF = mins_now() + 2, mins_now() + 5
    cfgF = {'out': tdir / 'F', 'pos': posF, 'beat': beatF,
            'daemon_argv': [PY, '-X', 'utf8', str(stub), str(posF), str(ctlF), str(pidfF), str(beatF)],
            'pid_pattern': f'*{str(posF)}*', 'pid_lister': _pidfile_lister(pidfF),
            'has_positions_fn': _always_positions,
            'check_every': 2, 'stale_sec': 5, 'boot_grace': 6, 'max_restarts': 1,
            # 预算窗口特意设很短(8s), 以便在测试时限内观察到"窗口滑过 → 隔离态下真死被重启"。
            # 生产窗口是 900s: 该限速是刻意的(避免 flapping 无限重启), 但**绝不能永久放弃** ——
            # 这正是 F 要守住的语义(旧实现 degraded → continue, 收盘前永不重启)。
            'daemon_stale_sec': 4, 'restart_window_sec': 8,
            'close_no_restart': closeF, 'exit_after': exitF,
            'lunch_freeze_s': LUNCH_FREEZE_S, 'lunch_freeze_e': LUNCH_FREEZE_E,
            'feishu': False}
    resF = {}
    thF = threading.Thread(target=lambda: resF.__setitem__('code', run(cfgF)), daemon=True)
    thF.start()
    # 等隔离出现(预算 1, 应很快耗尽)
    deadline_f = time.time() + 90
    while time.time() < deadline_f:
        if any(e['trigger'] == 'watch_limit' for e in _events(cfgF['out'])):
            break
        time.sleep(0.5)
    isolated = any(e['trigger'] == 'watch_limit' for e in _events(cfgF['out']))
    restarts_before = sum(1 for e in _events(cfgF['out']) if e['trigger'] == 'watch_restart')
    # 杀掉进程并抹掉心跳文件 → 隔离态下的"进程真死"
    try:
        kill_pids([int(pidfF.read_text(encoding='ascii').strip())])
    except (OSError, ValueError):
        pass
    try:
        pidfF.unlink()
    except OSError:
        pass
    try:
        beatF.unlink()
    except OSError:
        pass
    restarted_in_isolation = False
    deadline_f2 = time.time() + 45   # 需覆盖 restart_window_sec(8s) 的滑出时间
    while time.time() < deadline_f2:
        n = sum(1 for e in _events(cfgF['out']) if e['trigger'] == 'watch_restart')
        if n > restarts_before:
            restarted_in_isolation = True
            break
        time.sleep(0.5)
    _wait_minutes(closeF, timeout=180)
    ctlF.write_text('exit', encoding='ascii')
    thF.join(timeout=180)
    _check('F 前置: 已进入隔离', isolated)
    _check('F 隔离态下进程真死仍被重启(持仓保护不真空)', restarted_in_isolation,
           f"restarts_before={restarts_before}")
    return ok_all


def selftest():
    """多场景冒烟。**无论成败都必须收掉 stub 进程** —— 早期版本只在末尾清理, 中途断言失败会
    留下"睡 3600s"的 stub 逐渐累积(实测积累到 18 个), 既占资源也干扰后续排查。

    已知残留边界: watcher 在场景中 kill 旧 stub 并 spawn 新 stub 时, 会存在**非本函数直接
    spawn** 的短命进程(启动后随即被 kill), pidfile 只记录最后一个。故一次通过后通常仍有
    少量 stub 在退出前存活一两秒; 生产路径不受影响(唯一 daemon 由 watcher 管理)。
    """
    tdir = BASE / 'outputs' / f'_selftest_tick_watch_{now().strftime("%Y%m%d_%H%M%S")}'
    pidfiles = []
    ok = False                      # ⚠️ 必须先置位: 下面 finally 里要读它, 若 try 内的异常是
                                    # SystemExit(BaseException, 不被 except Exception 捕获),
                                    # 原实现会让 finally 抛 UnboundLocalError 掩盖真实原因。
    try:
        ok = _selftest_body(tdir, pidfiles)
    except BaseException as exc:    # 夹具自身异常不得留下残余进程; SystemExit 也要报出来
        traceback.print_exc()
        print(f"[selftest] FAILED(异常) {type(exc).__name__}: {exc}", flush=True)
        ok = False
    finally:
        for pf in pidfiles:
            try:
                kill_pids([int(pathlib.Path(pf).read_text(encoding='ascii').strip())])
            except Exception:
                pass
        try:
            kill_pids(find_pids('*stub_daemon*'))   # 兜底: 按命令行再扫一遍
        except Exception:
            pass
        print(f"[selftest] {'ALL PASS' if ok else 'FAILED'} (dir={tdir})", flush=True)
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description='tick daemon watchdog v3（双信号: 心跳存活 + 数据新鲜）')
    ap.add_argument('--selftest', action='store_true',
                    help='stub daemon 场景自测: A 正常收盘 / B 耗尽隔离+恢复 / C 预算回补 / '
                         'D 残留锁清理 / E 心跳活但数据卡住 / F 隔离态进程真死仍重启（不发飞书）')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    return run(prod_cfg())


if __name__ == '__main__':
    sys.exit(main())
