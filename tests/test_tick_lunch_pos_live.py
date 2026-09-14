# -*- coding: utf-8 -*-
"""午休 pos_live 结转 —— 防回退契约（AST 判据）。

背景（2026-09-14 实测根因）：
  `tick_monitor.py` 主循环在 `not in_exec_window(hm2)` 时 `time.sleep(60);continue`
  ⇒ **午休 11:30-13:00 不写 `pos_live.json`**；而 `scan_and_confirm.companion_health()`
  对 `pos_live.time` 有 **120s** 硬判据 ⇒ **每日 13:00 首轮 scan 恒定 rc=6
  「伴随监控失效，禁止新仓」**（实录：13:00:03 rc=6、13:01:03 起 rc=0）。
  后果：每个交易日 13:00 那一轮**必然拿不到候选队列样本**。

修法：daemon 在窗口外（上午收盘之后）也**结转写一份 pos_live**（positions 原样带过，
只刷新 `time`，并打 `out_of_session: true`），让「监控是否活着」的判据重新成立。

本文件钉三条：
  ① **AST 防回退**：daemon 主循环体内，时段守卫的 `continue` 之前必须调用
     `write_idle_pos_live` —— 否则午休又会不写 pos_live（rc=6 复发）；
  ② **AST 防回退**：`pos_live.json` 的写入必须仍在主循环体内（9/14 首日「绝食」故障
     是同一类"缩进挪出循环"缺陷，那条已由 test_smoke 钉住，这里补循环内结转的判据）；
  ③ **行为**：结转只刷新 time、保留当日 positions，且**不得**把上一交易日的持仓
     搬到今天（`date` 不符 ⇒ 退化为空持仓，不得伪造）。
"""
from __future__ import annotations
import ast
import json
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'portfolio'))
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT.parent / 'py_libs'))
import tick_monitor as tm  # noqa: E402

SRC = (ROOT / 'scripts' / 'tick_monitor.py').read_text(encoding='utf-8')


def _daemon_while():
    """返回 main() 里那个 `while a.rounds==0 or rounds<a.rounds:` 节点。"""
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.While):
            seg = ast.get_source_segment(SRC, node.test) or ''
            if 'a.rounds' in seg:
                return node
    raise AssertionError('未找到 daemon 主循环（`while a.rounds==0 or rounds<a.rounds:`）')


class AstGuardTests(unittest.TestCase):
    """① ② 防回退：窗口外必须结转写 pos_live，且写在 `continue` 之前。"""

    def test_idle_branch_writes_pos_live_before_continue(self):
        loop = _daemon_while()
        guard = None
        for node in loop.body:
            if isinstance(node, ast.If):
                seg = ast.get_source_segment(SRC, node.test) or ''
                if 'in_exec_window' in seg:
                    guard = node
        self.assertIsNotNone(guard, '主循环内未找到 `not in_exec_window(hm2)` 时段守卫')

        # 守卫体内：`write_idle_pos_live(...)` 必须出现在任何 `continue` **之前**。
        # ⚠️ 判据按**语句序**比较（不是"存在即通过"）：结转写若被挪到 continue 之后
        #    就永远执行不到，而"存在"式断言照样绿 —— 那正是要防的回退形态。
        call_idx, cont_idx = None, None
        for i, stmt in enumerate(guard.body):
            for sub in ast.walk(stmt):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id == 'write_idle_pos_live'):
                    call_idx = i if call_idx is None else min(call_idx, i)
            if isinstance(stmt, ast.Continue):
                cont_idx = i if cont_idx is None else min(cont_idx, i)
        self.assertIsNotNone(call_idx,
                             '时段守卫体内没有调用 write_idle_pos_live ⇒ '
                             '午休不写 pos_live，13:00 首轮 scan 会再次 rc=6')
        self.assertIsNotNone(cont_idx, '时段守卫体内应有 `continue`（窗口外跳过本轮取数）')
        self.assertLess(call_idx, cont_idx,
                        'write_idle_pos_live 被排在 `continue` 之后 ⇒ 永远执行不到（等于没修）')

    def test_pos_live_write_still_inside_loop(self):
        """`atomic_json(OUT/'pos_live.json', ...)` 必须仍在主循环体内（防缩进回落）。"""
        loop = _daemon_while()
        hit = False
        for node in ast.walk(loop):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'atomic_json'):
                seg = ast.get_source_segment(SRC, node) or ''
                if 'pos_live.json' in seg:
                    hit = True
        self.assertTrue(hit, "pos_live 写入被挪出主循环体（空仓时永不刷新 ⇒ 全天禁新仓）")

    def test_idle_writer_bound_after_morning_close_only(self):
        """结转只在**上午收盘之后**触发：开盘前 09:00-09:29 不得用空持仓覆盖隔夜视图。"""
        loop = _daemon_while()
        seg = ast.get_source_segment(SRC, loop) or ''
        self.assertIn('SESSION_AM[1]', seg,
                      '窗口外结转写必须有「上午收盘之后」的时段前提（防开盘前伪造空仓）')


class IdleWriteBehaviorTests(unittest.TestCase):
    """③ 行为语义：结转刷 time / 留 positions / 不跨日伪造。"""

    def _run(self, td, prev, day='2026-09-14', hm=(11, 45)):
        out = pathlib.Path(td)
        if prev is not None:
            (out / 'pos_live.json').write_text(json.dumps(prev, ensure_ascii=False),
                                               encoding='utf-8')
        n = datetime(2026, 9, 14, hm[0], hm[1], 0)
        with mock.patch.object(tm, 'OUT', out):
            tm.write_idle_pos_live(day, n)
        return json.loads((out / 'pos_live.json').read_text(encoding='utf-8'))

    def test_refreshes_time_and_keeps_today_positions(self):
        with tempfile.TemporaryDirectory() as td:
            prev = {'date': '2026-09-14', 'time': '11:30:55',
                    'positions': [{'sym': '300468', 'px': 10.0, 'chg': 1.0,
                                   't1_locked': False}]}
            got = self._run(td, prev)
            self.assertEqual(got['time'], '11:45:00', 'time 未刷新 ⇒ rc=6 依旧会复发')
            self.assertEqual([p['sym'] for p in got['positions']], ['300468'],
                             '结转不得丢掉当日持仓（会误导看板/复盘）')
            self.assertTrue(got.get('out_of_session'), '结转快照必须显式标记 out_of_session')

    def test_stale_other_day_positions_not_carried(self):
        """上一交易日的 pos_live 不得被当成今日持仓结转（防跨日伪造）。"""
        with tempfile.TemporaryDirectory() as td:
            got = self._run(td, {'date': '2026-09-11', 'time': '14:59:55',
                                 'positions': [{'sym': '600000', 'px': 8.0}]})
            self.assertEqual(got['date'], '2026-09-14')
            self.assertEqual(got['positions'], [], '跨日持仓被误结转')

    def test_missing_file_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as td:
            got = self._run(td, None)
            self.assertEqual(got, {'date': '2026-09-14', 'time': '11:45:00',
                                   'positions': [], 'out_of_session': True})

    def test_lunch_write_makes_companion_health_pass(self):
        """端到端语义：午休结转后，13:00 首轮 companion_health 必须是 (True,'ok')。"""
        import scan_and_confirm as scan
        with tempfile.TemporaryDirectory() as td:
            base = pathlib.Path(td)
            logs = base / 'outputs' / 'task_logs' / '2026-09-14'
            logs.mkdir(parents=True)
            out = base / 'outputs' / 'intraday'
            out.mkdir(parents=True)
            ts = datetime(2026, 9, 14, 12, 59, 0)
            stamp = ts.strftime('%Y%m%d_%H%M%S_000')
            (logs / f'{stamp}_monitor.json').write_text(json.dumps(
                {'date': '2026-09-14', 'exit_code': 0,
                 'finished_at': ts.strftime('%Y-%m-%d %H:%M:%S.000')}), encoding='utf-8')
            # 上午收盘最后一笔（daemon 在 11:30 的窗口内写）
            (out / 'pos_live.json').write_text(json.dumps(
                {'date': '2026-09-14', 'time': '11:30:55', 'positions': []}),
                encoding='utf-8')
            now = datetime(2026, 9, 14, 13, 0, 3)
            with mock.patch.object(scan, 'BASE', base), mock.patch.object(scan, 'OUT', out):
                before, _d = scan.companion_health('2026-09-14', now)
                self.assertFalse(before, '前置：未结转时本用例应复现 rc=6 形态')
            # 午休期间 daemon 结转（11:31 / 12:00 / 12:59 各刷一次）
            for hm in ((11, 31), (12, 0), (12, 59)):
                with mock.patch.object(tm, 'OUT', out):
                    tm.write_idle_pos_live('2026-09-14',
                                           datetime(2026, 9, 14, hm[0], hm[1], 3))
            with mock.patch.object(scan, 'BASE', base), mock.patch.object(scan, 'OUT', out):
                after = scan.companion_health('2026-09-14', now)
        self.assertEqual(after, (True, 'ok'),
                         '午休结转后 13:00 首轮仍被判失效 ⇒ 队列样本仍拿不到')


class DaemonLoopLunchReplayTests(unittest.TestCase):
    """端到端：直接把**生产 daemon 主循环**按午休时刻跑一轮，看它是否落 pos_live。

    比"调一次 write_idle_pos_live"强的地方：它证明**主循环的控制流**真的走到了那次调用
    （9/14 首日「绝食」故障正是控制流问题：语句在、缩进错、永不执行）。
    """

    def test_one_round_at_lunch_writes_pos_live(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td)

            class _FakeDT(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime(2026, 9, 14, 11, 45, 0, tzinfo=tz)

            class _API:
                def connect(self, *a, **k):
                    return False

                def disconnect(self):
                    return None

            argv = ['tick_monitor.py', '--daemon', '--rounds', '1', '--interval', '5']
            # ⚠️ `BEAT` 是**模块级常量**（`OUT/'_tick_daemon.beat'`，import 时求值）——
            #    只 patch `OUT` 挡不住它，测试会写进**生产** `outputs/intraday/`，
            #    并留下一个假时间戳的心跳（实测踩过）。两个都必须 patch。
            beat = out / '_tick_daemon.beat'
            with mock.patch.object(tm, 'OUT', out), \
                    mock.patch.object(tm, 'BEAT', beat), \
                    mock.patch.object(tm, 'datetime', _FakeDT), \
                    mock.patch.object(tm, '_acquire_daemon_lock', return_value=True), \
                    mock.patch.object(tm, 'TdxHq_API', return_value=_API()), \
                    mock.patch.object(tm.time, 'sleep', return_value=None), \
                    mock.patch.object(sys, 'argv', argv):
                rc = tm.main()
            self.assertEqual(rc, 0)
            fp = out / 'pos_live.json'
            self.assertTrue(fp.exists(),
                            '午休跑一轮后 pos_live 未落盘 ⇒ 13:00 首轮 scan 会 rc=6')
            got = json.loads(fp.read_text(encoding='utf-8'))
            self.assertEqual(got['date'], '2026-09-14')
            self.assertEqual(got['time'], '11:45:00')
            self.assertTrue(got.get('out_of_session'))
            self.assertEqual(got['positions'], [])
            self.assertTrue(beat.exists(), '心跳也应在窗口外照写')

    def test_beat_constant_is_module_level(self):
        """防呆：`BEAT` 必须是模块级常量 —— 若改成函数内派生，本文件的 patch 会失效，
        测试又会开始污染生产 outputs/。"""
        import inspect
        src = inspect.getsource(tm)
        self.assertIn("BEAT=OUT/'_tick_daemon.beat'", src.replace(' ', ''))
        self.assertTrue(pathlib.Path(tm.BEAT).name == '_tick_daemon.beat')


if __name__ == '__main__':
    unittest.main(verbosity=2)
