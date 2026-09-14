"""粒度同源 + 新鲜度上界 + 轮内不静默丢弃 —— 三个回归锚（2026-09-14 新增）。

背景（用户裁定：「生产以及回测都需要同标准」「triggered[:1] 这个错误太严重了，需要处理」
「我并没有限制 LLM 调用次数」）：

**锚① 粒度同源**：`get_security_bars(category, …)` 的 **category=0 是 5 分钟**，1 分钟是 7/8。
2026-09-11 `tick_monitor` 已修过（`KLINE_1MIN=8`）但**未推广** ⇒ `scan_and_confirm` /
`monitor_intraday` / `close_pipeline` / `daily_src` / `pull_intraday` 仍在取 5 分钟，
而回测 `QFQStore.get_minute` freq 硬编码 `1m` ⇒ **生产 5min vs 回测 1min 不同源**。
本锚断言交易路径一律用 `KLINE_1MIN`，且腾讯备胎显式 `m1`。

**锚② 新鲜度上界**：原 `if _dt < fresh_floor: continue` **只有下界** ⇒ 标签晚于当前时刻的
bar 永远算「新鲜」。实测午餐时段数据源把最后一根 bar 标成 13:00 ⇒ 11:30 那轮写出 `ts=13:00`。
本锚断言「未来标签 bar 不得触发」，且正常新鲜 bar 仍能触发（防止修过头）。

**锚③ 轮内不静默丢弃**：原 `for tg in triggered[:1]:` ⇒ 除第 1 条外既不执行也不留痕。
本锚断言「一轮内所有候选都产生结局」(`n_no_outcome == 0`)。
"""
from __future__ import annotations
import copy, json, pathlib, sys, tempfile, unittest
from datetime import datetime
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'portfolio'))
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT.parent / 'py_libs'))
import scan_and_confirm as scan  # noqa: E402
from core.intraday import KLINE_1MIN, TX_PERIOD_1MIN  # noqa: E402

DAY = '2026-09-01'
PC = 10.0
_PASS = {'allowed': True, 'choice': 'B_medium', 'status': 'ok',
         'veto_reason': 'd6_not_reject', 'reason': '', 'score': 0.5,
         'digest': None, 'digest_errors': []}


class _FakeDT(datetime):
    _fixed = datetime(2026, 9, 1, 13, 11, 30)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


def _bars_one(sym, times=('09:35', '09:40', '09:50', '10:00', '10:05', '10:30', '11:00',
                          '13:00', '13:05', '13:10', '13:11'),
              signal_at=None, chg=0.025):
    sig = PC * (1.0 + chg)
    rows = []
    for hm in times:
        close = sig if hm == signal_at else PC
        rows.append({'datetime': f'{DAY} {hm}', 'open': close, 'high': close, 'low': PC,
                     'close': close, 'vol': 100000, 'amount': close * 100000})
    return rows


def _base_state():
    return {'policy': {'account_mode': 'autonomous_paper', 'require_human_decision': False,
                       'max_positions': 4, 'max_single_weight': 0.30,
                       'max_gross_exposure': 0.90, 'max_new_buys_per_day': 1},
            'account': {'cash': 100000.0, 'positions': {}, 'fills': [], 'equity_curve': []},
            'start_cash': 100000.0, 'risk_state': {}, 'signal_requests': {},
            'human_decisions': {}, 'autonomous_decisions': {}}


def _daily_df():
    import pandas as pd
    return pd.DataFrame({'close': [PC] * 12})


def _run(symbols, bars_by_sym, fixed_now, cap=1, veto=None):
    """跑一轮 scan，返回 (rc, confirm_doc, state, transact_calls)。"""
    state = _base_state()
    state['policy']['max_new_buys_per_day'] = cap
    calls = []

    def fake_transact(mutator, retries=3):
        st = copy.deepcopy(state)
        res = mutator(st)                     # 抛 ValueError 时下面不执行 ⇒ 状态不变（同真实 transact）
        state.clear(); state.update(st)       # 成交才落库 ⇒ 后续候选能看到额度已消耗
        calls.append((st, res))
        return st, res

    class _API:
        def connect(self, *a, **k):
            return True

        def disconnect(self):
            return None

        def get_security_bars(self, cat, mkt, sym, start, n):
            self.last_cat = cat
            return bars_by_sym.get(sym, [])

    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td)
        api = _API()
        with mock.patch.object(scan, 'datetime', _FakeDT), \
                mock.patch.object(scan, '_load_day_plan', return_value={'picks': []}), \
                mock.patch.object(scan, 'load', return_value=state), \
                mock.patch.object(scan, 'load_universe', return_value=list(symbols)), \
                mock.patch.object(scan, 'fetch_batch',
                                  return_value={s: {'chg': 2.0, 'turn': 10.0, 'amt': 1e12,
                                                    'name': f'测试{s[-3:]}'} for s in symbols}), \
                mock.patch.object(scan, 'TdxHq_API', return_value=api), \
                mock.patch.object(scan, 'prev_close', return_value=PC), \
                mock.patch('core.daily_src.load_daily', return_value=_daily_df()), \
                mock.patch.object(scan, 'OUT', out), \
                mock.patch.object(scan.intraday_veto, 'judge',
                                  side_effect=(veto if isinstance(veto, BaseException) else None),
                                  return_value=(None if isinstance(veto, BaseException)
                                                else dict(veto or _PASS))), \
                mock.patch.object(scan.intraday_veto, 'load_prev_df', return_value=None), \
                mock.patch.object(scan, 'transact', side_effect=fake_transact), \
                mock.patch.object(sys, 'argv', ['scan', '--force', '--e4-support',
                                                '--min-amt', '0', '--execute']):
            rc = scan.main()
        docs = [json.loads(f.read_text(encoding='utf-8')) for f in out.glob('confirm_*.json')]
    return rc, (docs[0] if docs else None), state, calls, api


class GranularityTests(unittest.TestCase):
    """锚① 交易路径必须取 1 分钟类别。"""

    def test_scan_pull_minutes_requests_1min(self):
        seen = []

        class A:
            def get_security_bars(self, *a, **k):
                seen.append(a)
                return [{'datetime': f'{DAY} 09:3{i}', 'open': 1.0, 'high': 1.0, 'low': 1.0,
                         'close': 1.0, 'vol': 1.0, 'amount': 1.0} for i in range(6)]

        scan.pull_minutes(A(), '300468', DAY)
        self.assertTrue(seen, '未调用 get_security_bars')
        self.assertEqual(seen[0][0], KLINE_1MIN,
                         'scan.pull_minutes 未取 1 分钟（category=0 是 5 分钟）')

    def test_scan_fallback_uses_m1(self):
        with mock.patch.object(scan, 'tencent_min_df', return_value=None) as m:
            scan.pull_minutes(None, '300468', DAY)
        m.assert_called_once_with('300468', DAY, 'm1')

    def test_scan_fallback_on_empty_tdx_also_m1(self):
        class A:
            def get_security_bars(self, *a, **k):
                return []

        with mock.patch.object(scan, 'tencent_min_df', return_value=None) as m:
            scan.pull_minutes(A(), '300468', DAY)
        m.assert_called_once_with('300468', DAY, 'm1')

    def test_offline_pull_minutes_reports_1min(self):
        """端到端：默认（不传 api）必须请求 1 分钟周期。"""
        with mock.patch.object(scan, 'tencent_min_df', return_value=None) as m:
            scan.pull_minutes(None, '301071', DAY)
        self.assertEqual(m.call_args[0][2], 'm1')


class GranularitySourceGuardTests(unittest.TestCase):
    """源码级防回退：交易路径不得再出现 `get_security_bars(0, …)`。

    ⚠️ 判据一律走 **AST**，不做文本扫描 —— 本文件/被测文件的注释里会引用旧写法
    （如「原先写 `get_security_bars(0, …)`」），文本判据会被自己的注释骗到。
    这条纪律本仓已在 `.ps1` 吞行检查上吃过教训（「判吞行须用 AST 文本包含判据」）。
    """

    TRADING_PATH = ('scripts/scan_and_confirm.py', 'scripts/monitor_intraday.py',
                    'scripts/close_pipeline.py', 'src/core/daily_src.py',
                    'scripts/pull_intraday.py')

    @staticmethod
    def _calls_with_literal_zero(src_text):
        """返回 [(lineno, 源码片段)]：`get_security_bars(0, …)` 且首参是字面量 0。"""
        import ast
        hits = []
        for node in ast.walk(ast.parse(src_text)):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == 'get_security_bars'):
                continue
            if not node.args:
                continue
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and a0.value == 0:
                hits.append((node.lineno, ast.get_source_segment(src_text, node) or ''))
        return hits

    def test_no_5min_literal_in_trading_path(self):
        bad = []
        for rel in self.TRADING_PATH:
            src = (ROOT / rel).read_text(encoding='utf-8')
            for lineno, seg in self._calls_with_literal_zero(src):
                bad.append(f'{rel}:{lineno}  {seg[:110]}')
        self.assertEqual(bad, [],
                         '交易路径仍在取 5 分钟类别（category=0）：\n' + '\n'.join(bad))

    def test_all_use_shared_constant(self):
        for rel in self.TRADING_PATH:
            src = (ROOT / rel).read_text(encoding='utf-8')
            self.assertIn('KLINE_1MIN', src, f'{rel} 未引用单一事实源 KLINE_1MIN')

    def test_single_source_of_truth_defined_once(self):
        src = (ROOT / 'src' / 'core' / 'intraday.py').read_text(encoding='utf-8')
        self.assertIn('KLINE_1MIN = 8', src)
        self.assertIn('TX_PERIOD_1MIN = "m1"', src)

    def test_tick_monitor_constant_unchanged(self):
        """tick_monitor 2026-09-11 已正确（KLINE_1MIN=8），不得被回退、也不得改成 0。"""
        src = (ROOT / 'scripts' / 'tick_monitor.py').read_text(encoding='utf-8')
        self.assertIn('KLINE_1MIN=8', src)
        self.assertEqual(self._calls_with_literal_zero(src), [])


class FreshnessCeilingTests(unittest.TestCase):
    """锚② 新鲜度必须有上界：未来标签 bar 不得触发；正常新鲜 bar 仍须触发。"""

    def test_future_labeled_bar_does_not_trigger(self):
        """午餐复现实录：now=11:30:30，bar 标签为 13:00（数据源越界标注）→ 不得触发。"""
        _FakeDT._fixed = datetime(2026, 9, 1, 11, 30, 30)
        try:
            rc, doc, _, calls, _ = _run(['300468'], {'300468': _bars_one('300468', signal_at='13:00')},
                                        datetime(2026, 9, 1, 11, 30, 30))
            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 0,
                             '未来标签 bar（13:00 @ 11:30）被当成新鲜信号并成交了 —— 上界失效')
        finally:
            _FakeDT._fixed = datetime(2026, 9, 1, 13, 11, 30)

    def test_normal_fresh_bar_still_triggers(self):
        """防修过头：正常新鲜窗口内的 bar 必须照常触发。"""
        rc, doc, _, calls, _ = _run(['300468'], {'300468': _bars_one('300468', signal_at='13:10')},
                                    datetime(2026, 9, 1, 13, 11, 30))
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1, '新鲜 bar 被上界误杀')

    def test_ceiling_constant_present(self):
        src = (ROOT / 'scripts' / 'scan_and_confirm.py').read_text(encoding='utf-8')
        self.assertIn('FUTURE_BAR_TOL_SECONDS', src)
        self.assertIn('_dt < fresh_floor or _dt > fresh_ceil', src,
                      'e4_support 的新鲜度上界被移除')
        self.assertIn('fresh_floor <= _cdt <= fresh_ceil', src,
                      '非 e4 分支（B/D/P 引擎）的新鲜度上界被移除')


class NoSilentDropTests(unittest.TestCase):
    """锚③ 一轮内所有候选都必须有结局（不得再静默丢弃）。"""

    SYMS = ('300468', '300469', '300470')

    def test_all_candidates_get_outcome(self):
        bars = {s: _bars_one(s, signal_at='13:10') for s in self.SYMS}
        rc, doc, state, calls, _ = _run(self.SYMS, bars, datetime(2026, 9, 1, 13, 11, 30), cap=1)
        self.assertEqual(rc, 0)
        self.assertEqual(doc['n_triggered'], 3, '三条候选应全部进入 triggered')
        self.assertEqual(doc['n_no_outcome'], 0,
                         '有候选没有结局 ⇒ 执行段又发生了静默丢弃（原 triggered[:1] 的病灶）')
        for tg in doc['triggered']:
            self.assertTrue(tg.get('outcome') or tg.get('execution_rejected'),
                            f"{tg['sym']} 既无 outcome 也无 execution_rejected")
        # 额度=1 ⇒ 恰好 1 笔成交，其余 2 条被 policy 明确拒绝（而非静默丢弃）
        self.assertEqual(len(doc['fill_ids']), 1)
        self.assertEqual(len(doc['rejected']), 2)
        for r in doc['rejected']:
            self.assertEqual(r['stage'], 'policy')
            self.assertIn('上限', r['reason'])

    def test_trigger_rank_recorded(self):
        bars = {s: _bars_one(s, signal_at='13:10') for s in self.SYMS}
        _, doc, _, _, _ = _run(self.SYMS, bars, datetime(2026, 9, 1, 13, 11, 30), cap=1)
        ranks = sorted(tg.get('trigger_rank') for tg in doc['triggered'])
        self.assertEqual(ranks, [1, 2, 3], '轮内顺序未记录')

    def test_no_slice_truncation_in_source(self):
        """⚠️ 用 AST 判据：`for … in triggered[:N]` 这种切片截断不得出现。

        文本判据会被注释骗到（本文件与被测文件的注释里都引用了旧写法）。
        """
        import ast
        src = (ROOT / 'scripts' / 'scan_and_confirm.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        sliced, plain = [], []
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            it = node.iter
            _is_triggered = (isinstance(it, ast.Name) and it.id == 'triggered')
            # 也接受 `enumerate(triggered)`（当前实现形式 —— 需要轮内序号 trigger_rank）
            if (not _is_triggered and isinstance(it, ast.Call)
                    and isinstance(it.func, ast.Name) and it.func.id == 'enumerate'
                    and it.args and isinstance(it.args[0], ast.Name)
                    and it.args[0].id == 'triggered'):
                _is_triggered = True
            if _is_triggered:
                plain.append(node.lineno)
            if (isinstance(it, ast.Subscript) and isinstance(it.value, ast.Name)
                    and it.value.id == 'triggered'):
                sliced.append((node.lineno, ast.get_source_segment(src, it) or ''))
        self.assertEqual(sliced, [],
                         'triggered 又被切片截断了（静默丢弃候选）：\n' +
                         '\n'.join(f'line {n}: {s}' for n, s in sliced))
        self.assertTrue(plain, '未找到「直接遍历 triggered」的语句 —— 执行段结构被改动')

    def test_d6_veto_is_recorded_in_snapshot(self):
        """D6 否决也要在 confirm 快照留痕（原先只 print）。"""
        veto = dict(_PASS); veto.update(allowed=False, choice='C_reject',
                                        veto_reason='d6_c_reject', score=0.2, reason='分时弱势')
        bars = {s: _bars_one(s, signal_at='13:10') for s in self.SYMS}
        rc, doc, _, calls, _ = _run(self.SYMS, bars, datetime(2026, 9, 1, 13, 11, 30), veto=veto)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 0, 'D6 全否决后不得成交')
        self.assertEqual(doc['n_no_outcome'], 0)
        for tg in doc['triggered']:
            self.assertFalse(tg['d6_veto']['allowed'])
            self.assertIn('D6 否决', tg['execution_rejected'])
        self.assertEqual(len(doc['rejected']), 3)
        self.assertTrue(all(r['stage'] == 'd6' for r in doc['rejected']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
