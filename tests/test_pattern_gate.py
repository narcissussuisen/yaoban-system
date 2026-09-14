"""形态门（ROADMAP §1.2 第三层）+ 异动池/确认队列分离 + in-plan 优先 —— 回归锚。

背景（2026-09-14 根因定位）：
  `scan_and_confirm.py` 原以 `pool.sort(key=lambda x: -x['chg'])` 后取 `pool[:8]` 当候选池
  ⇒ 「候选池」= 全市场**涨幅前 8**，与选手的「上升回档战法池」**构造互斥**（追高 vs 低吸），
  两边候选池交集恒为空（实测：EvoAlpha top8 = +9.29%~+7.10%，选手实买 +2.61%）。
  用户裁定：① 补形态筛选层 ② 日计划成为准入/优先级 ③ 涨幅榜降级为"异动池"。

本文件锚定：
  ① `load_pattern_pool` 的**口径硬校验**（缺失/日期不符/asof>=day 一律不可用 —— 防前视）；
  ② 启用 `--pattern-gate` 后，确认队列 = 战法池 ∩ 今日活跃（**不是**涨幅榜）；
  ③ 战法池缺失 ⇒ **fail-closed rc=8**，禁止静默退回"涨幅榜当池子"；
  ④ in-plan 优先（同额度竞争时计划内排前）；
  ⑤ 未启用形态门时**显式告警**，且留痕字段仍在（movers/confirm_queue/pattern_meta）。
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

DAY = '2026-09-01'
ASOF = '2026-08-31'
PC = 10.0
_PASS = {'allowed': True, 'choice': 'B_medium', 'status': 'ok',
         'veto_reason': 'd6_not_reject', 'reason': '', 'score': 0.5,
         'digest': None, 'digest_errors': []}

SYMS = ('300468', '300469', '300470')


class _FakeDT(datetime):
    _fixed = datetime(2026, 9, 1, 13, 11, 30)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


def _bars_one():
    """09:35 起；13:10 那根 close=PC*1.025（落在 e4 的 [2%,3%] 内）→ 能触发。"""
    sig = PC * 1.025
    rows = []
    for hm in ('09:35', '09:40', '09:50', '10:00', '10:05', '10:30', '11:00',
               '13:00', '13:05', '13:10', '13:11'):
        close = sig if hm == '13:10' else PC
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


def _pool_doc(pool_syms, asof=ASOF, day=DAY):
    return {'day': day, 'asof': asof, 'lookback': 4, 'patterns': ['huigui'],
            'stats': {'n_pool': len(pool_syms)},
            'pool': [{'sym': s, 'pattern': 'huigui', 'pattern_cn': '上升回档',
                      'patterns': ['huigui'], 'sig_date': asof,
                      'bars_since_sig': 1, 'close_asof': PC} for s in pool_syms],
            'name_map': {s: f'测试{s[-3:]}' for s in pool_syms}}


def _run(*, syms=SYMS, chg_by_sym=None, plan_picks=(), pattern_gate=True,
         pool_file=('write', SYMS), pattern_dir=None):
    """跑一轮 scan。pool_file: ('write', syms) 写一份有效战法池 / ('skip', None) 不写。"""
    state = _base_state()
    calls = []

    def fake_transact(mutator, retries=3):
        st = copy.deepcopy(state)
        res = mutator(st)
        state.clear(); state.update(st)
        calls.append((st, res))
        return st, res

    chg_by_sym = chg_by_sym or {s: 2.0 for s in syms}

    class _API:
        def connect(self, *a, **k):
            return True

        def disconnect(self):
            return None

        def get_security_bars(self, *a, **k):
            return _bars_one()

    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td)
        pdir = pathlib.Path(pattern_dir) if pattern_dir else (out / 'patterns')
        pdir.mkdir(parents=True, exist_ok=True)
        if pool_file[0] == 'write':
            (pdir / f'{DAY}_pattern_pool.json').write_text(
                json.dumps(_pool_doc(pool_file[1]), ensure_ascii=False), encoding='utf-8')
        plan = {'picks': [{'sym': s, 'name': f'计划{s[-3:]}'} for s in plan_picks]}
        argv = ['scan', '--force', '--e4-support', '--min-amt', '0', '--execute']
        if pattern_gate:
            argv.append('--pattern-gate')
        with mock.patch.object(scan, 'datetime', _FakeDT), \
                mock.patch.object(scan, '_load_day_plan', return_value=plan), \
                mock.patch.object(scan, 'load', return_value=state), \
                mock.patch.object(scan, 'load_universe', return_value=list(syms)), \
                mock.patch.object(scan, 'fetch_batch',
                                  return_value={s: {'chg': chg_by_sym[s], 'turn': 10.0,
                                                    'amt': 1e12, 'name': f'测试{s[-3:]}'}
                                                for s in syms}), \
                mock.patch.object(scan, 'TdxHq_API', return_value=_API()), \
                mock.patch.object(scan, 'prev_close', return_value=PC), \
                mock.patch('core.daily_src.load_daily', return_value=_daily_df()), \
                mock.patch.object(scan, 'OUT', out), \
                mock.patch.object(scan, 'PATTERN_DIR', pdir), \
                mock.patch.object(scan.intraday_veto, 'judge', return_value=dict(_PASS)), \
                mock.patch.object(scan.intraday_veto, 'load_prev_df', return_value=None), \
                mock.patch.object(scan, 'transact', side_effect=fake_transact), \
                mock.patch.object(sys, 'argv', argv):
            rc = scan.main()
        docs = [json.loads(f.read_text(encoding='utf-8')) for f in out.glob('confirm_*.json')]
    return rc, (docs[0] if docs else None), calls, state


class LoadPatternPoolTests(unittest.TestCase):
    """① 口径硬校验（防前视）。"""

    def _write(self, td, doc):
        p = pathlib.Path(td) / f'{DAY}_pattern_pool.json'
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
        return pathlib.Path(td)

    def test_valid_pool_loads(self):
        with tempfile.TemporaryDirectory() as td:
            d = self._write(td, _pool_doc(SYMS))
            with mock.patch.object(scan, 'PATTERN_DIR', d):
                self.assertIsNotNone(scan.load_pattern_pool(DAY))

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(scan, 'PATTERN_DIR', pathlib.Path(td)):
                self.assertIsNone(scan.load_pattern_pool(DAY))

    def test_asof_equal_or_later_than_day_rejected(self):
        """asof >= day ⇒ 前视，必须拒绝。"""
        for bad_asof in (DAY, '2026-09-02'):
            with tempfile.TemporaryDirectory() as td:
                d = self._write(td, _pool_doc(SYMS, asof=bad_asof))
                with mock.patch.object(scan, 'PATTERN_DIR', d):
                    self.assertIsNone(scan.load_pattern_pool(DAY),
                                      f'asof={bad_asof} 未被拒绝 ⇒ 会引入前视')

    def test_day_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            d = self._write(td, _pool_doc(SYMS, day='2026-08-28'))
            with mock.patch.object(scan, 'PATTERN_DIR', d):
                self.assertIsNone(scan.load_pattern_pool(DAY))


class ConfirmQueueTests(unittest.TestCase):
    """② 确认队列 = 战法池 ∩ 活跃（不是涨幅榜）；③ 缺失 fail-closed；⑤ 未启用时显式告警留痕。"""

    def test_queue_is_intersection_not_movers(self):
        """战法池只含两只需，第三只（同为异动）不得进入确认队列。"""
        rc, doc, _, _ = _run(syms=SYMS, pool_file=('write', SYMS[:2]), pattern_gate=True)
        self.assertEqual(rc, 0)
        q = {x['sym'] for x in doc['confirm_queue']}
        self.assertEqual(q, set(SYMS[:2]), '确认队列未按战法池收窄 ⇒ 又变成涨幅榜了')
        self.assertEqual(len(doc['movers']), 3, '异动池应保留全部活跃标的')
        self.assertEqual(doc['pattern_meta']['asof'], ASOF)

    def test_empty_pattern_pool_yields_empty_queue(self):
        """战法池与今日活跃无交集 ⇒ 队列为空且 rc=0（"今天没有符合模式的标的"是正常结果）。"""
        rc, doc, calls, _ = _run(syms=SYMS, pool_file=('write', ['600000']), pattern_gate=True)
        self.assertEqual(rc, 0)
        self.assertEqual(doc['confirm_queue'], [])
        self.assertEqual(len(calls), 0)
        self.assertEqual(len(doc['movers']), 3)

    def test_missing_pool_fails_closed(self):
        """战法池缺失 ⇒ rc=8 且零成交，**不得静默退回涨幅榜**。"""
        rc, doc, calls, _ = _run(syms=SYMS, pool_file=('skip', None), pattern_gate=True)
        self.assertEqual(rc, 8, '战法池缺失时未 fail-closed')
        self.assertEqual(len(calls), 0)
        self.assertIsNone(doc, 'fail-closed 时不应落 confirm 快照')

    def test_gate_off_keeps_legacy_but_records_sections(self):
        rc, doc, _, _ = _run(syms=SYMS, pool_file=('skip', None), pattern_gate=False)
        self.assertEqual(rc, 0)
        self.assertFalse(doc['pattern_meta'].get('enabled', True))
        self.assertEqual(len(doc['confirm_queue']), 3)
        self.assertEqual(len(doc['movers']), 3)
        self.assertIn('movers', doc['candidates_snapshot'])
        self.assertIn('confirm_queue', doc['candidates_snapshot'])
        self.assertIn('pattern_meta', doc['candidates_snapshot'])


class InPlanPriorityTests(unittest.TestCase):
    """④ in-plan 优先：与 off-plan 争同一额度时计划内排前。"""

    def test_in_plan_sorted_first_even_with_lower_chg(self):
        # 涨幅：300468=+5.0（计划外，最高） / 300469=+2.0（计划内）
        rc, doc, _, _ = _run(syms=SYMS[:2], chg_by_sym={'300468': 5.0, '300469': 2.0},
                             plan_picks=['300469'], pool_file=('write', SYMS[:2]),
                             pattern_gate=True)
        self.assertEqual(rc, 0)
        q = doc['confirm_queue']
        self.assertEqual(q[0]['sym'], '300469', 'in-plan 未优先')
        self.assertTrue(q[0]['in_plan'])
        self.assertFalse(q[1]['in_plan'])

    def test_in_plan_flag_recorded_in_snapshot(self):
        rc, doc, _, _ = _run(syms=SYMS[:2], plan_picks=['300469'],
                             pool_file=('write', SYMS[:2]), pattern_gate=True)
        self.assertEqual(rc, 0)
        snap_q = doc['candidates_snapshot']['confirm_queue']
        self.assertEqual({x['sym']: x['in_plan'] for x in snap_q},
                         {'300468': False, '300469': True})


class PatternPoolCoreTests(unittest.TestCase):
    """`core.pattern_pool` 基本行为（不依赖全市场数据）。"""

    def test_asof_required(self):
        from core.pattern_pool import build_pattern_pool
        with self.assertRaises(ValueError):
            build_pattern_pool({}, asof='')

    def test_short_history_skipped_not_crashed(self):
        import pandas as pd
        from core.pattern_pool import build_pattern_pool
        dmap = {'600000': pd.DataFrame({'date': ['2026-08-28', '2026-08-31'],
                                        'open': [1.0, 1.0], 'high': [1.0, 1.0],
                                        'low': [1.0, 1.0], 'close': [1.0, 1.0],
                                        'volume': [1.0, 1.0]})}
        pool, stats = build_pattern_pool(dmap, asof=ASOF, lookback=4)
        self.assertEqual(pool, [])
        self.assertEqual(stats['n_skipped_short'], 1)

    def test_no_lookahead_beyond_asof(self):
        """asof 之后的 bar 不得参与（否则即为前视）。"""
        import pandas as pd
        from core.pattern_pool import build_pattern_pool
        idx = pd.date_range('2026-01-01', periods=120, freq='D').strftime('%Y-%m-%d').tolist()
        n = len(idx)
        df = pd.DataFrame({'date': idx, 'open': [1.0] * n, 'high': [1.0] * n,
                           'low': [1.0] * n, 'close': [1.0] * n, 'volume': [1.0] * n})
        pool, stats = build_pattern_pool({'600000': df}, asof=ASOF, lookback=4)
        for r in pool:      # 若未来被误用为信号日，这里会暴露
            self.assertLessEqual(r['sig_date'], ASOF)


if __name__ == '__main__':
    unittest.main(verbosity=2)
