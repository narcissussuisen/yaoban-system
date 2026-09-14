"""e4_support 买入涨幅**闭区间** [2%, 3%] 回归锚（2026-09-14 新增）。

背景（P0 根因）：`scan_and_confirm.py` 的 e4_support 触发条件原先写作
    `if (_px / pc - 1) >= 0.02 and ...`      # 只有下界、无上界
⇒ 上界实际退化为 `rough_screen` 的池窗 9.8%，导致 2026-09-14 首个自主交易日
  10:00 买在 **+9.3%**（001896，signal_px=12.91），违反选手 `parameters.toml`
  rule#8「买点-当日涨幅上限 ≤3%」（confidence=实锤，v12@01:07 / v19@01:13）。
  选手实盘反证：2026-09-11 依顿电子 +2.71%、2026-09-14 博敏电子 +2.61%。

本测试锚定两件事：
  ① **上界真的生效** —— +3.01% / +9.30% 不得成交；
  ② **边界是闭区间** —— 恰好 +2.00% 与恰好 +3.00% **必须成交**。
     ⚠️ ② 是浮点陷阱：`10.3 / 10.0 - 1` = 0.030000000000000027 > 0.03，
     朴素写法会把恰好 +3.00% 判成越界（本测试首次运行即抓出该缺陷）。
     故实现侧用 `PCT_EPS = 1e-9` 容差；本测试是它的守卫。
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
PC = 10.0            # prev_close
SYM = '300468'       # 创业板 → 涨停 ±20%，保证 +9.3% 不会被"已封板"提前否掉


class _FakeDT(datetime):
    _fixed = datetime(2026, 9, 1, 13, 11, 30)

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


def _bars(chg):
    """全部 bar close=PC，仅 13:10 那根 close=PC*(1+chg)；13:11 的 open 同样设成触发价。"""
    sig = PC * (1.0 + chg)
    rows = []
    for hm in ('09:35', '09:40', '09:50', '10:00', '10:05', '10:30', '11:00',
               '13:00', '13:05', '13:10', '13:11'):
        close = sig if hm == '13:10' else PC
        rows.append({'datetime': f'{DAY} {hm}', 'open': close, 'high': close, 'low': PC,
                     'close': close, 'vol': 100000, 'amount': close * 100000})
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
    return pd.DataFrame({'close': [PC] * 12})


_PASS = {'allowed': True, 'choice': 'B_medium', 'status': 'ok',
         'veto_reason': 'd6_not_reject', 'reason': '', 'score': 0.5,
         'digest': None, 'digest_errors': []}


class E4EntryBoundsTests(unittest.TestCase):
    def _fills(self, chg):
        """跑一轮 scan，返回成交笔数。"""
        state = _base_state()
        calls = []

        def fake_transact(mutator, retries=3):
            st = copy.deepcopy(state)
            res = mutator(st)
            calls.append((st, res))
            return st, res

        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td)
            with mock.patch.object(scan, 'datetime', _FakeDT), \
                    mock.patch.object(scan, '_load_day_plan', return_value={'picks': []}), \
                    mock.patch.object(scan, 'load', return_value=state), \
                    mock.patch.object(scan, 'load_universe', return_value=[SYM]), \
                    mock.patch.object(scan, 'fetch_batch',
                                      return_value={SYM: {'chg': 2.0, 'turn': 10.0,
                                                          'amt': 1e12, 'name': '测试股份'}}), \
                    mock.patch.object(scan, 'TdxHq_API', return_value=_FakeAPI(_bars(chg))), \
                    mock.patch.object(scan, 'prev_close', return_value=PC), \
                    mock.patch('core.daily_src.load_daily', return_value=_daily_df()), \
                    mock.patch.object(scan, 'OUT', out), \
                    mock.patch.object(scan.intraday_veto, 'judge', return_value=dict(_PASS)), \
                    mock.patch.object(scan.intraday_veto, 'load_prev_df', return_value=None), \
                    mock.patch.object(scan, 'transact', side_effect=fake_transact), \
                    mock.patch.object(sys, 'argv', ['scan', '--force', '--e4-support',
                                                    '--min-amt', '0', '--execute']):
                rc = scan.main()
            self.assertEqual(rc, 0)
        return len(calls)

    # ---- 闭区间：两端都必须成交 ----
    def test_exact_lower_bound_2pct_executes(self):
        self.assertEqual(self._fills(0.02), 1, '恰好 +2.00% 属闭区间下沿，应当成交')

    def test_exact_upper_bound_3pct_executes(self):
        self.assertEqual(self._fills(0.03), 1,
                         '恰好 +3.00% 属闭区间上沿（选手 rule#8「≤3%」），应当成交；'
                         '⚠️ 浮点 10.3/10-1=0.030000000000000027 会误判越界 → 须用容差')

    # ---- 越界：一律不得成交 ----
    def test_just_below_lower_bound_rejected(self):
        self.assertEqual(self._fills(0.0199), 0, '+1.99% 低于下界 2%，不得成交')

    def test_just_above_upper_bound_rejected(self):
        self.assertEqual(self._fills(0.0301), 0, '+3.01% 超出上界 3%，不得成交（P0 修复目标）')

    def test_p0_regression_case_9p3pct_rejected(self):
        """P0 实盘反例：2026-09-14 001896 买在 +9.3% —— 修复后必须被拒。"""
        self.assertEqual(self._fills(0.093), 0,
                         '+9.3% 属追高，违反选手「只低吸不追高打板」，必须拒绝')

    def test_old_pool_window_ceiling_9p8pct_rejected(self):
        """池窗上沿 9.8% 不得再成为事实买入上界。"""
        self.assertEqual(self._fills(0.098), 0, '9.8% 只是池窗，不是买入窗')


class E4BoundsSourceGuardTests(unittest.TestCase):
    """源码级防回退（同 `test_weak_market_no_longer_hard_blocks` 的先例）。

    该上界直接决定「当天买不买得进」，属高影响、易被无意改回的改动。
    """
    def setUp(self):
        self.src = (ROOT / 'scripts' / 'scan_and_confirm.py').read_text(encoding='utf-8')

    def test_upper_bound_present_and_uses_constant(self):
        import re
        self.assertIn('E4_MAX_PCT = 0.03', self.src)
        self.assertRegex(
            self.src,
            r'0\.02\s*-\s*PCT_EPS\s*<=\s*_chg\s*<=\s*E4_MAX_PCT\s*\+\s*PCT_EPS',
            '闭区间比较被改动了：e4_support 必须同时有 [2%, 3%] 上下界与浮点容差')

    def test_float_epsilon_guard_present(self):
        self.assertIn('PCT_EPS = 1e-9', self.src,
                      '浮点容差被移除 → 恰好 +3.00% 会被误判越界（测试已实证）')

    def test_no_bare_lower_bound_only_pattern(self):
        """不得回退成「只有下界」的朴素写法。"""
        import re
        bad = re.search(r'if\s*\(?_px\s*/\s*pc\s*-\s*1\)?\s*>=\s*0\.02\s+and', self.src)
        self.assertIsNone(bad, 'e4_support 回退成「只有下界无上界」的写法')

    def test_r6p_replication_shares_same_bound(self):
        """回测与生产必须同源 —— R6' 复制脚本不得只留旧口径。"""
        r6p = (ROOT / 'scripts' / 'r6p_replication.py').read_text(encoding='utf-8')
        self.assertIn('args.e4_max_pct', r6p)
        self.assertIn("0.02 - 1e-9 <= _c <= args.e4_max_pct + 1e-9", r6p)


if __name__ == '__main__':
    unittest.main(verbosity=2)
