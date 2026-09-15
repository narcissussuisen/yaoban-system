"""P0.2 扫描器新鲜度回归测试（蓝图 B0-P0-2）——重放 300468 回溯成交场景。

场景 A（违规重放）: df 含 10:05 的 e4 命中 bar, 轮次 now=13:12:08 → 不得触发买入
场景 B（新鲜路径）: df 的 13:10 bar 命中, now=13:11:30 → 触发且 fill 携带完整 provenance
场景 C: 当日计划缺失 → fail-closed rc=7
场景 D: 计划内标的 → plan_match.in_plan=True + pick_id
"""
from __future__ import annotations
import copy, json, pathlib, sys, tempfile, unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'portfolio'))
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT.parent / 'py_libs'))
import scan_and_confirm as scan

DAY = '2026-09-01'


class _FakeDT(datetime):
    _fixed = datetime(2026, 9, 1, 13, 12, 8)

    @classmethod
    def now(cls, tz=None):
        return cls._fixed


class _FakeAPI:
    def __init__(self, bars):
        self._bars = bars

    def connect(self, *a, **k):
        return True

    def disconnect(self):
        return None

    def get_security_bars(self, *a, **k):
        return self._bars


def _bars(signal_at=None, exec_open=10.31):
    """构造当日分钟 bar: 全部 close=10.0, 仅 signal_at 那根 close=10.3（3%≥2% 触发 e4）。"""
    rows = []
    for hm in ('09:35', '09:40', '09:50', '10:00', '10:05', '10:30', '11:00',
               '13:00', '13:05', '13:10', '13:11'):
        close = 10.3 if hm == signal_at else 10.0
        rows.append({'datetime': f'{DAY} {hm}', 'open': 10.0, 'high': close + 0.05,
                     'low': 10.0, 'close': close, 'vol': 100000, 'amount': close * 100000})
    # exec bar 的 open 单独控制（触发 bar 的下一根）
    if signal_at is not None:
        for i, r in enumerate(rows):
            if r['datetime'].endswith(signal_at) and i + 1 < len(rows):
                rows[i + 1]['open'] = exec_open
    return rows


def _base_state():
    return {'policy': {'account_mode': 'autonomous_paper', 'require_human_decision': False,
                       'max_positions': 2, 'max_single_weight': 0.30,
                       'max_gross_exposure': 0.90, 'max_new_buys_per_day': 1},
            'account': {'cash': 100000.0, 'positions': {}, 'fills': [], 'equity_curve': []},
            'start_cash': 100000.0, 'risk_state': {}, 'signal_requests': {},
            'human_decisions': {}, 'autonomous_decisions': {}}


def _daily_df():
    import pandas as pd
    return pd.DataFrame({'close': [10.0] * 12})


class ScanFreshnessTests(unittest.TestCase):
    # R4.1：scan 现在会调 `intraday_veto.judge`（真调 LLM）。
    #   ⚠️ 测试**必须**把它打桩，否则：① 测试结果依赖外部 LLM 输出（flaky）；
    #   ② judge 会把判定写进**生产**目录 `outputs/decision_chain/intraday/`
    #   （实测已产生 `intraday_2026-09-01.jsonl`）。默认桩＝放行（含 `load_prev_df`）。
    _PASS = {'allowed': True, 'choice': 'B_medium', 'status': 'ok',
             'veto_reason': 'd6_not_reject', 'reason': '', 'score': 0.5,
             'digest': None, 'digest_errors': []}

    def _run(self, bars, fixed_now, argv_extra=(), plan=None, base_state=None, veto=None):
        state = base_state or _base_state()
        transact_calls = []

        def fake_transact(mutator, retries=3):
            st = copy.deepcopy(state)
            result = mutator(st)
            transact_calls.append((st, result))
            return st, result

        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td)
            with _patch_dt(fixed_now), \
                    mock.patch.object(scan, '_load_day_plan', return_value=plan), \
                    mock.patch.object(scan, 'load', return_value=state), \
                    mock.patch.object(scan, 'load_universe', return_value=['300468']), \
                    mock.patch.object(scan, 'fetch_batch',
                                      return_value={'300468': {'chg': 2.0, 'turn': 10.0,
                                                               'amt': 1e12, 'name': '测试股份'}}), \
                    mock.patch.object(scan, 'TdxHq_API', return_value=_FakeAPI(bars)), \
                    mock.patch.object(scan, 'prev_close', return_value=10.0), \
                    mock.patch('core.daily_src.load_daily', return_value=_daily_df()), \
                    mock.patch.object(scan, 'OUT', out), \
                    mock.patch.object(scan.intraday_veto, 'judge',
                                      side_effect=(veto if isinstance(veto, BaseException) else None),
                                      return_value=(None if isinstance(veto, BaseException)
                                                    else dict(veto or self._PASS))), \
                    mock.patch.object(scan.intraday_veto, 'load_prev_df', return_value=None), \
                    mock.patch.object(scan, 'transact', side_effect=fake_transact), \
                    mock.patch.object(sys, 'argv', ['scan', '--force', '--e4-support',
                                                    '--min-amt', '0', *argv_extra]):
                rc = scan.main()
            confirms = [json.loads(f.read_text(encoding='utf-8'))
                        for f in out.glob('confirm_*.json')]
        return rc, transact_calls, confirms

    def test_stale_signal_replay_rejected(self):
        # 300468 场景重放: 10:05 信号, 13:12:08 轮次 → 无触发无成交
        rc, calls, _ = self._run(_bars(signal_at='10:05'), datetime(2026, 9, 1, 13, 12, 8),
                                 argv_extra=('--execute',), plan={'picks': []})
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 0, '过期信号不得进入执行段')

    def test_fresh_signal_executes_with_provenance(self):
        # 新鲜信号: 13:10 触发, now=13:11:30 → 成交且 fill 携带完整 provenance
        rc, calls, confirms = self._run(_bars(signal_at='13:10'), datetime(2026, 9, 1, 13, 11, 30),
                                        argv_extra=('--execute',), plan={'picks': []})
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        st, result = calls[0]
        self.assertEqual(result['mode'], 'filled')
        fill = st['account']['fills'][-1]
        # P0.2 时序字段
        self.assertEqual(fill['signal_ts'], f'{DAY} 13:10')
        self.assertEqual(fill['decision_ts'], f'{DAY} 13:11:30')
        self.assertTrue(fill['ts'] >= fill['signal_ts'])
        # P0.4 provenance 字段
        self.assertTrue(fill['decision_id'].startswith('dec-auto-'))
        self.assertTrue(fill['candidates_ref'])
        self.assertFalse(fill['plan_match']['in_plan'])
        self.assertEqual(fill['plan_match']['off_plan_reason']['code'], 'intraday_scan_capture')
        self.assertTrue(fill['plan_ref'].startswith(f'offplan-{DAY}'))
        # 决策登记可追溯
        did = fill['decision_id']
        self.assertEqual(st['autonomous_decisions'][did]['sym'], '300468')
        self.assertEqual(st['autonomous_decisions'][did]['signal_ts'], f'{DAY} 13:10')
        # confirm 快照携带 candidates_ref
        doc = confirms[0]
        self.assertEqual(doc['candidates_ref'], fill['candidates_ref'])
        self.assertIn('candidates_snapshot', doc)

    def test_missing_plan_fails_closed(self):
        rc, calls, _ = self._run(_bars(signal_at='13:10'), datetime(2026, 9, 1, 13, 11, 30),
                                 argv_extra=('--execute',), plan=None)
        self.assertEqual(rc, 7)
        self.assertEqual(len(calls), 0)

    def test_in_plan_pick_gets_pick_id(self):
        rc, calls, _ = self._run(_bars(signal_at='13:10'), datetime(2026, 9, 1, 13, 11, 30),
                                 argv_extra=('--execute',),
                                 plan={'picks': [{'sym': '300468', 'name': '测试股份'}]})
        self.assertEqual(rc, 0)
        st, result = calls[0]
        self.assertEqual(result['mode'], 'filled')
        fill = st['account']['fills'][-1]
        self.assertTrue(fill['plan_match']['in_plan'])
        self.assertEqual(fill['plan_match']['pick_id'], f'plan-{DAY}#0:300468')
        self.assertEqual(fill['plan_ref'], f'plan-{DAY}#0:300468')

    def test_d6_veto_blocks_buy_and_registration(self):
        """R4.1 扫描侧集成：D6 判 `C_reject` → **不得有任何成交或决策登记**。"""
        veto = dict(self._PASS)
        veto.update(allowed=False, choice='C_reject', veto_reason='d6_c_reject',
                    score=0.2, reason='分时弱势')
        rc, calls, _ = self._run(_bars(signal_at='13:10'), datetime(2026, 9, 1, 13, 11, 30),
                                 argv_extra=('--execute',), plan={'picks': []}, veto=veto)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 0, 'D6 否决后仍进入执行段 → 会买到被否决的票')

    def test_veto_judge_exception_does_not_block_buy(self):
        """R4.1 硬约束：`judge` 自身抛异常 → scan 必须继续（降级放行，绝不阻断）。"""
        rc, calls, _ = self._run(_bars(signal_at='13:10'), datetime(2026, 9, 1, 13, 11, 30),
                                 argv_extra=('--execute',), plan={'picks': []},
                                 veto=RuntimeError('boom'))
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1, 'judge 异常不应阻断买入')
        self.assertEqual(calls[0][1]['mode'], 'filled')

    def test_weak_market_no_longer_hard_blocks(self):
        """⭐ 回归锚（2026-09-13 行为修正）：弱市**不得再硬停买点**。

        依据 9/11 实盘行为（用户裁定）：选手当日**仓位 38.6%、持仓 3 只**、做的是**调仓换股**
        ⇒ **弱市 ≠ 空仓；弱市 = 换股 + 降仓**。原文 `if _temp_gate: continue` 会让弱市全天零成交。

        改法：弱市只**记录标记**（`weak_market` 进 candidates_snapshot），仓位仍由 ledger policy
        （`max_single_weight`）约束；「弱市降档」因**切分阈值未标定**而登记为待标定项，不擅自引入。

        ⚠️ 本测试用源码扫描防回退（同 `preflight.py runner_ok` 的 token 扫描先例）——
        因为该行为直接决定「当天有没有成交」，属于高影响、易被无意改回的改动。
        """
        src = (ROOT / 'scripts' / 'scan_and_confirm.py').read_text(encoding='utf-8')
        # ⚠️ 断言的是「不存在『if _temp_gate: → continue』这一硬阻断组合」，
        #    而非「不出现 _temp_gate」—— 弱市标记本身仍须打印并写进 candidates_snapshot。
        import re
        self.assertIsNone(
            re.search(r'if\s+_temp_gate\s*:\s*\n\s*continue', src),
            '弱市硬阻断被改回来了：弱市不应阻断买点（见 9/11 实盘证据与用户裁定）')
        self.assertIn('_temp_gate = bool(', src)
        self.assertIn("'weak_market': _temp_gate", src)


def _patch_dt(fixed):
    _FakeDT._fixed = fixed
    return mock.patch.object(scan, 'datetime', _FakeDT)


if __name__ == '__main__':
    unittest.main(verbosity=2)
