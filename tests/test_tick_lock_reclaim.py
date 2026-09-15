"""tick 单实例锁回收回归测试（INC-2026-09-11-01 修复保护）

背景（9/11 实录）: tick_monitor 只在启动时抢一次 daemon 单实例锁, 而 watchdog kill 旧 daemon
后锁文件会留在盘上; 旧 `_acquire_daemon_lock` 只认 mtime(>120s 才抢占), 于是"kill → 立刻
spawn"必然被死锁文件拒绝 —— restart #4/#5/#6 三连 spawn 全部秒退, 只剩"已有活 daemon 持有
单实例锁", 白烧 3 次重启额度且期间持仓守护真空。

覆盖:
  - 死 pid 残留锁立即回收(不再白等 120s)
  - 活进程持锁必须拒绝(单实例语义不变, 不得抢活锁造成双写 pos_live)
  - 锁内 pid 不可解析且 mtime 新鲜 → 保守拒绝
  - 锁内 pid 不可解析但 mtime 陈旧 → 兜底回收(mtime 判据保留)
  - watchdog spawn 前清理残留锁: 死 pid 清、活 pid 绝不误删
"""
from __future__ import annotations
import importlib.util
import os
import pathlib
import shutil
import sys
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'portfolio'))
sys.path.insert(0, str(ROOT / 'src'))
# 注: 不注入 ROOT.parent/'py_libs' —— 本机 py_libs 目录内容被 ACL 拒绝读取(连 pwsh 都读不到),
# 注入后会抢占标准库/环境包路径并抛 PermissionError(如 py_libs/six.py)。pandas/pytdx/pyarrow
# 均可由 workbuddy 环境自身提供, 无需该目录。

# 用 workbuddy 解释器直接跑本文件时 scripts/ 不在 path, 故显式按文件路径加载两个被测模块;
# 生产 watchdog 以脚本方式运行(_tick_watch.py 在 __main__ 下), 此处 import 不会触碰生产目录。
import tick_monitor as tm  # noqa: E402


def _load_watch():
    spec = importlib.util.spec_from_file_location('_tick_watch_under_test',
                                                  ROOT / 'scripts' / '_tick_watch.py')
    mod = importlib.util.module_from_spec(spec)
    sys.modules['_tick_watch_under_test'] = mod
    spec.loader.exec_module(mod)
    return mod


tw = _load_watch()

# 沙箱禁止写 %TEMP%(且 tempfile.mkdtemp 的目录权限会被拒), 用工作区内一次性目录。
SCRATCH = ROOT / 'outputs' / '_test_tick_lock_tmp'
DEAD_PID = 4000000  # 远超 Windows pid 上限, 恒不存在


class _Scratch(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        SCRATCH.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)


class DaemonLockTests(_Scratch):
    def test_acquire_when_no_lock(self):
        self.assertTrue(tm._acquire_daemon_lock(SCRATCH))

    def test_dead_holder_lock_reclaimed_immediately(self):
        """核心修复: 持锁者已死 → 立即回收, 不得白等 120s。"""
        self.assertFalse(tm._pid_alive(DEAD_PID), '前置: 该 pid 应判定为不存活')
        lock = SCRATCH / '_tick_daemon.lock'
        lock.write_text(str(DEAD_PID), encoding='ascii')  # mtime 全新, 只有 pid 判据能救
        t0 = time.time()
        got = tm._acquire_daemon_lock(SCRATCH)
        self.assertTrue(got, '死 pid 残留锁应被回收并取得')
        self.assertLess(time.time() - t0, 2.0, '回收应立即完成, 不应等到 120s mtime 阈值')

    def test_live_holder_lock_refused(self):
        """单实例语义不变: 本进程存活时不得抢锁(否则双 daemon 交替写 pos_live)。"""
        self.assertTrue(tm._pid_alive(os.getpid()), '前置: 本进程应判定为存活')
        lock = SCRATCH / '_tick_daemon.lock'
        lock.write_text(str(os.getpid()), encoding='ascii')
        self.assertFalse(tm._acquire_daemon_lock(SCRATCH))
        self.assertEqual(tm._holder_pid(lock), os.getpid())

    def test_unparsable_fresh_lock_refused(self):
        """pid 不可判定 + mtime 新鲜 → 保守拒绝(宁可退出也不能抢活锁)。"""
        lock = SCRATCH / '_tick_daemon.lock'
        lock.write_text('not-a-pid', encoding='ascii')
        self.assertIsNone(tm._holder_pid(lock))
        self.assertFalse(tm._acquire_daemon_lock(SCRATCH))

    def test_unparsable_stale_lock_reclaimed(self):
        """mtime 判据保留为兜底: 不可判定但确实陈旧 → 回收。"""
        lock = SCRATCH / '_tick_daemon.lock'
        lock.write_text('not-a-pid', encoding='ascii')
        old = time.time() - 300
        os.utime(lock, (old, old))
        self.assertTrue(tm._acquire_daemon_lock(SCRATCH))


class WatchdogSpawnLockCleanupTests(_Scratch):
    """spawn_daemon 前的残留锁清理: 只清"锁内 pid 已不存在"的锁。"""

    def _cfg(self, alive_pids):
        return {'out': SCRATCH, 'pid_pattern': '*unittest-fake*',
                'pid_lister': lambda _pat: list(alive_pids),
                'daemon_argv': [sys.executable, '-c', 'pass']}

    def test_dead_holder_lock_cleared_then_spawn(self):
        lock = SCRATCH / '_tick_daemon.lock'
        lock.write_text('555555', encoding='ascii')  # 不在存活表 → 已被 kill
        fake = type('FakeProc', (), {'pid': 999999})()
        with mock.patch.object(tw.subprocess, 'Popen', lambda *a, **k: fake):
            tw.spawn_daemon(self._cfg([111]))
        self.assertFalse(lock.exists(), '死 pid 残留锁应被清理, 否则新 daemon 必然秒退')

    def test_live_holder_lock_preserved(self):
        lock = SCRATCH / '_tick_daemon.lock'
        lock.write_text('777777', encoding='ascii')  # 仍是活 daemon
        fake = type('FakeProc', (), {'pid': 999999})()
        with mock.patch.object(tw.subprocess, 'Popen', lambda *a, **k: fake):
            tw.spawn_daemon(self._cfg([777777]))
        self.assertTrue(lock.exists(), '活 daemon 的锁绝不能被误删(会造成双写 pos_live)')
        self.assertEqual(lock.read_text(encoding='ascii'), '777777')

    def test_spawn_without_lock_ok(self):
        fake = type('FakeProc', (), {'pid': 999999})()
        with mock.patch.object(tw.subprocess, 'Popen', lambda *a, **k: fake):
            self.assertEqual(tw.spawn_daemon(self._cfg([])), 999999)


class PidListerInjectionTests(unittest.TestCase):
    """find_pids 的可注入后端(2026-09-11): selftest/单测不再依赖沙箱里被拒的 CIM 查询。"""

    def test_injected_lister_used(self):
        seen = []

        def lister(pat):
            seen.append(pat)
            return [1234, os.getpid()]  # 自身 pid 必须被排除

        self.assertEqual(tw.find_pids('*x*', lister), [1234])
        self.assertEqual(seen, ['*x*'])

    def test_lister_failure_returns_empty(self):
        def boom(_pat):
            raise RuntimeError('probe backend down')

        self.assertEqual(tw.find_pids('*x*', boom), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
