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
         pool_file=('write', SYMS), pattern_dir=None, pool_doc=None):
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
                json.dumps(pool_doc if pool_doc is not None else _pool_doc(pool_file[1]),
                           ensure_ascii=False), encoding='utf-8')
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


class SectorHeatFilterTests(unittest.TestCase):
    """⭐ 板块热度过滤（2026-09-14 用户裁定「现在做」）—— 单靠形态收不到 4-15 只，收窄必须发生在板块层。"""

    def test_cold_sector_dropped_from_queue(self):
        """L2 不在当日热度前 N ⇒ 不进确认队列（选手：只选热点板块，冷门概念不玩）。"""
        # 300468/300469 同属热板块 A（涨幅高）; 300470 属冷板块 B（涨幅低）
        with mock.patch.object(scan, 'HOT_L2_TOP', 1), \
                mock.patch.object(scan, 'industry_of',
                                  side_effect=lambda s, d: 'A' if s in ('300468', '300469') else 'B'):
            rc, doc, _, _ = _run(syms=SYMS,
                                 chg_by_sym={'300468': 5.0, '300469': 4.0, '300470': 1.0},
                                 pool_file=('write', SYMS), pattern_gate=True)
        self.assertEqual(rc, 0)
        q = {x['sym'] for x in doc['confirm_queue']}
        self.assertEqual(q, {'300468', '300469'}, '冷板块标的未被剔除')
        self.assertEqual(doc['pattern_meta']['funnel']['n_dropped_cold_l2'], 1)
        for x in doc['confirm_queue']:
            self.assertEqual(x['l2'], 'A')
            self.assertEqual(x['l2_rank'], 1)

    def test_hot_sector_list_recorded(self):
        with mock.patch.object(scan, 'HOT_L2_TOP', 2), \
                mock.patch.object(scan, 'industry_of', side_effect=lambda s, d: 'A'):
            rc, doc, _, _ = _run(syms=SYMS, pool_file=('write', SYMS), pattern_gate=True)
        self.assertEqual(rc, 0)
        self.assertEqual(doc['pattern_meta']['hot_l2_top'], 2)
        self.assertTrue(doc['pattern_meta']['hot_l2'])


class QueueSortOrderTests(unittest.TestCase):
    """⭐ 排序改造：信号新鲜度 + 战法共振 优先（**只作权重，不作门槛**）。"""

    def _pool_doc2(self, rows):
        return {'day': DAY, 'asof': ASOF, 'lookback': 4, 'patterns': ['huigui'],
                'stats': {'n_pool': len(rows)},
                'pool': [{'sym': s, 'pattern': 'huigui', 'pattern_cn': '上升回档',
                          'patterns': pats, 'sig_date': ASOF,
                          'bars_since_sig': age, 'close_asof': PC}
                         for s, age, pats in rows],
                'name_map': {}}

    def test_fresher_and_more_resonant_ranks_first_despite_lower_chg(self):
        """新鲜度优先于涨幅：T-0 低涨幅 应排在 T-3 高涨幅之前（旧排序键会反过来）。"""
        rows = [('300468', 3, ['huigui']),      # T-3，涨幅给他最高
                ('300469', 0, ['huigui'])]      # T-0
        with mock.patch.object(scan, 'HOT_L2_TOP', 5), \
                mock.patch.object(scan, 'industry_of', side_effect=lambda s, d: 'A'), \
                mock.patch.object(scan, '_load_day_plan',
                                  return_value={'picks': []}):
            rc, doc, _, _ = _run(syms=('300468', '300469'),
                                 chg_by_sym={'300468': 9.0, '300469': 1.0},
                                 pool_file=('write', None), pattern_gate=True,
                                 pool_doc=self._pool_doc2(rows))
        self.assertEqual(rc, 0)
        q = [x['sym'] for x in doc['confirm_queue']]
        self.assertEqual(q[0], '300469', '新鲜度未优先（排序键可能又退回按涨幅）')

    def test_resonance_breaks_tie(self):
        """同新鲜度下，多战法共振者优先。"""
        rows = [('300468', 1, ['huigui']), ('300469', 1, ['huigui', 'xianren'])]
        with mock.patch.object(scan, 'HOT_L2_TOP', 5), \
                mock.patch.object(scan, 'industry_of', side_effect=lambda s, d: 'A'), \
                mock.patch.object(scan, '_load_day_plan', return_value={'picks': []}):
            rc, doc, _, _ = _run(syms=('300468', '300469'),
                                 chg_by_sym={'300468': 8.0, '300469': 1.0},
                                 pool_file=('write', None), pattern_gate=True,
                                 pool_doc=self._pool_doc2(rows))
        self.assertEqual(rc, 0)
        q = [x['sym'] for x in doc['confirm_queue']]
        self.assertEqual(q[0], '300469', '共振数未参与排序')

    def test_sort_source_guard(self):
        """源码防回退：排序键必须含 in_plan + bars_since_sig + 共振，且用 AST 判据。

        ⚠️ `lst.sort(key=...)` 的 key 是**关键字参数**（在 `Call.keywords`，不在 `Call.args`）——
        第一次写这条判据时按 `args[0]` 取，取不到而误报。
        """
        import ast
        src = (ROOT / 'scripts' / 'scan_and_confirm.py').read_text(encoding='utf-8')
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == 'sort'):
                continue
            for kw in node.keywords:
                if kw.arg != 'key':
                    continue
                seg = ast.get_source_segment(src, kw.value) or ''
                if 'bars_since_sig' in seg and 'in_plan' in seg and 'len(' in seg:
                    found = True
        self.assertTrue(found, 'confirm_queue 排序键被改回按涨幅（缺 bars_since_sig / 共振）')


class StrongMainlineTests(unittest.TestCase):
    """⭐ 强主线豁免层（T-1，选手口径 M1/M2/M3）—— 当日热度榜会漏掉「题材型主线」。"""

    def test_missing_artifact_is_soft_fail(self):
        """`outputs/` 下没有早于 day 的 sector_strength 产物 ⇒ 返回空集 + available=False（不抛、不静默）。"""
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(scan, 'PATTERN_DIR', pathlib.Path(td) / 'patterns'):
                s, meta = scan.load_strong_mainline(DAY)
        self.assertEqual(s, set())
        self.assertFalse(meta['available'])

    def test_not_yet_built_date_ignored(self):
        """只接受 **严格早于 day** 的产物（防前视）。

        ⚠️ 注意口径是「严格早于」：产物日期 == day 时**必须拒绝**（用当日产物＝前视）。
        """
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / 'patterns').mkdir()
            (root / 'sector_strength_2026-08-28.json').write_text(
                json.dumps({'sectors': [{'l2': 'X', 'm2': {'pass_': True}}]}), encoding='utf-8')
            with mock.patch.object(scan, 'PATTERN_DIR', root / 'patterns'):
                s, meta = scan.load_strong_mainline(DAY)          # DAY = 2026-09-01
                self.assertTrue(meta['available'], '早于 day 的产物应被接受')
                self.assertEqual(s, {'X'})
                self.assertEqual(meta['date'], '2026-08-28')
                # 产物日期 == day ⇒ 拒绝（否则即为前视）
                s2, meta2 = scan.load_strong_mainline('2026-08-28')
                self.assertFalse(meta2['available'], '产物日期不早于 day 时应拒绝（防前视）')

    def test_strong_mainline_only_enters_queue(self):
        """当日冷板块、但属 T-1 强主线 ⇒ 仍在候选（豁免剔除），且标记 strong_mainline_only。"""
        with mock.patch.object(scan, 'HOT_L2_TOP', 1), \
                mock.patch.object(scan, 'industry_of',
                                  side_effect=lambda s, d: 'A' if s in ('300468', '300469') else 'COLD'), \
                mock.patch.object(scan, 'load_strong_mainline',
                                  return_value=({'COLD'}, {'available': True, 'n_strong': 1})):
            rc, doc, _, _ = _run(syms=SYMS,
                                 chg_by_sym={'300468': 5.0, '300469': 4.0, '300470': 1.0},
                                 pool_file=('write', SYMS), pattern_gate=True)
        self.assertEqual(rc, 0)
        syms = [x['sym'] for x in doc['confirm_queue']]
        self.assertIn('300470', syms, '强主线豁免未生效')
        self.assertEqual(doc['pattern_meta']['funnel']['n_strong_mainline_only'], 1)
        # 且排序上排在热度内之后
        self.assertEqual(syms[-1], '300470', '强主线豁免的票应排在当日热度内之后')


if __name__ == '__main__':
    unittest.main(verbosity=2)
