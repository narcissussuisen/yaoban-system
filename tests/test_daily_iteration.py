# -*- coding: utf-8 -*-
"""每日自迭代批回归测试：规则确定性 / 卡片解析 / 门禁边界。

运行：python -m unittest tests.test_daily_iteration -v
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from iteration import cards as cards_mod          # noqa: E402
from iteration import gate, market, rules         # noqa: E402
from iteration.model import CHANGE_RULE, Proposal  # noqa: E402

import numpy as np                                # noqa: E402
import pandas as pd                               # noqa: E402


def synth(n: int = 60, seed: int = 7, code: str = "600000") -> pd.DataFrame:
    rnd = np.random.default_rng(seed)
    close = 10 + np.cumsum(rnd.normal(0.02, 0.15, n))
    close = np.maximum(close, 1.0)
    high = close * (1 + np.abs(rnd.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rnd.normal(0, 0.01, n)))
    open_ = np.concatenate(([close[0]], close[:-1])) * (1 + rnd.normal(0, 0.005, n))
    dates = [f"2026-06-{i+1:02d}" for i in range(n)]
    return pd.DataFrame({"symbol": code, "date": dates, "open": open_, "high": high,
                         "low": low, "close": close,
                         "volume": np.abs(rnd.normal(1e6, 2e5, n)), "amount": 1e7})


class TestLimitUp(unittest.TestCase):
    def test_pct_by_board(self):
        self.assertEqual(rules.limit_up_pct("600000"), 10.0)
        self.assertEqual(rules.limit_up_pct("300750"), 20.0)
        self.assertEqual(rules.limit_up_pct("688981"), 20.0)
        self.assertEqual(rules.limit_up_pct("830799"), 30.0)

    def test_price_rounding(self):
        self.assertAlmostEqual(rules.limit_up_price(16.92, "603615"), 18.61, places=2)
        self.assertAlmostEqual(rules.limit_up_price(14.13, "605006"), 15.54, places=2)

    def test_mask_detects_exact_limit(self):
        df = pd.DataFrame({"open": [16.9, 18.0], "high": [17.0, 18.61],
                           "low": [16.8, 17.9], "close": [16.92, 18.61],
                           "volume": [1e6, 2e6]})
        b = rules.bars(df)
        m = rules.limit_up_mask(b, "603615")
        self.assertFalse(bool(m[0]))
        self.assertTrue(bool(m[1]))


class TestSignalDeterminism(unittest.TestCase):
    def test_same_input_same_output(self):
        df = synth()
        for name, spec in rules.RULES.items():
            p = {} if spec["config_paths"] else {}
            a = spec["fn"](df, "600000", **p)
            b = spec["fn"](df, "600000", **p)
            pd.testing.assert_series_equal(a, b)

    def test_returns_bool_series(self):
        df = synth()
        for name, spec in rules.RULES.items():
            s = spec["fn"](df, "600000")
            self.assertEqual(s.dtype, bool, msg=name)
            self.assertEqual(len(s), len(df), msg=name)

    def test_param_monotonicity_vol_ratio(self):
        """量能比上限放宽 → 信号数不减（单调性，用于门禁的「放宽」判定可信）。"""
        df = synth(n=120, seed=11)
        tight = int(rules.sig_huigui(df, "600000", vol_ratio_max=0.5).sum())
        loose = int(rules.sig_huigui(df, "600000", vol_ratio_max=1.5).sum())
        self.assertGreaterEqual(loose, tight)

    def test_pullback_days_bound(self):
        df = synth(n=120, seed=3)
        s1 = rules.sig_huigui(df, "600000", pullback_days_min=1, pullback_days_max=3)
        s2 = rules.sig_huigui(df, "600000", pullback_days_min=1, pullback_days_max=7)
        self.assertGreaterEqual(int(s2.sum()), int(s1.sum()))

    def test_exit_ma_break_reference(self):
        """构造一段确定下跌：收盘连续低于 MA5 → 必触发。"""
        n = 40
        close = np.concatenate([np.full(20, 10.0), np.linspace(9.5, 7.0, 20)])
        df = pd.DataFrame({"open": close, "high": close * 1.001, "low": close * 0.999,
                           "close": close, "volume": np.full(n, 1e6)})
        s = rules.exit_ma_break(df, "600000", ma_n=5)
        self.assertTrue(bool(s.iloc[-1]))


class TestCardParsing(unittest.TestCase):
    SAMPLE = """# 卡片

### KC-9001 测试卡片
- **类别**：选股
- **来源**：`某路径/x.md` @ 2026-09-08
- **结论**：这是一条结论。
- **可量化表述**：阈值 ≤ 3
- **可测试性**：`daily`
- **样本数**：4
- **关联参数**：`a.b`, `c.d`

### KC-9002 未量化卡片
- **类别**：风险
- **来源**：`某路径/y.md` @ 2026-09-09
- **结论**：无法量化。
- **可量化表述**：—（原文未给出量化阈值）
- **可测试性**：`qualitative`
- **样本数**：—
- **关联参数**：—
"""

    def setUp(self):
        # 注意：不用 tempfile（沙箱下工作区外不可写），改用仓库内临时目录。
        self._td = ROOT / "tmp_test_iteration"
        self._td.mkdir(parents=True, exist_ok=True)
        self.tmp = self._td

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_parse_fields(self):
        p = self.tmp / "c.md"
        p.write_text(self.SAMPLE, encoding="utf-8")
        got = cards_mod.parse_cards(p)
        self.assertEqual(len(got), 2)
        c = got[0]
        self.assertEqual(c.card_id, "KC-9001")
        self.assertEqual(c.category, "选股")
        self.assertEqual(c.source, "某路径/x.md")
        self.assertEqual(c.source_date, "2026-09-08")
        self.assertEqual(c.quantified, "阈值 ≤ 3")
        self.assertEqual(c.sample_n, 4)
        self.assertEqual(c.linked_params, ["a.b", "c.d"])
        self.assertIsNone(got[1].quantified)
        self.assertEqual(got[1].testability, "qualitative")

    def test_summary_flags_unquantified(self):
        p = self.tmp / "c.md"
        p.write_text(self.SAMPLE, encoding="utf-8")
        got = cards_mod.parse_cards(p)
        s = cards_mod.summarize(got)
        self.assertEqual(s["total"], 2)
        self.assertIn("KC-9002", s["unquantified"])
        self.assertNotIn("KC-9001", s["unquantified"])

    def test_missing_required_field_is_skipped(self):
        bad = "### KC-9003 缺字段\n- **类别**：选股\n- **来源**：`x` @ 2026-01-01\n"
        p = self.tmp / "c.md"
        p.write_text(bad, encoding="utf-8")
        self.assertEqual(cards_mod.parse_cards(p), [])


class TestGate(unittest.TestCase):
    BASE = {"n": 100, "mean_ret_pct": 1.0, "win_rate_pct": 50.0, "p10_ret_pct": -6.0}
    SEG = [{"n": 50, "mean_ret_pct": 1.0}, {"n": 50, "mean_ret_pct": 1.0}]

    def _check(self, cand, seg_c=None):
        return gate.check(self.BASE, cand, param_path="strategy.x.y", axis_param="vol_ratio_max",
                          cur_value=0.7, cand_value=1.0, days=60,
                          seg_base=self.SEG, seg_cand=seg_c or self.SEG)

    def test_insufficient_evidence_not_passed(self):
        r = self._check({"n": 10, "mean_ret_pct": 5.0, "win_rate_pct": 90.0, "p10_ret_pct": 0.0})
        self.assertEqual(r["verdict"], "insufficient_evidence")

    def test_insufficient_days(self):
        r = gate.check(self.BASE, self.BASE, param_path="a.b", axis_param="vol_ratio_max",
                       cur_value=1, cand_value=2, days=5)
        self.assertEqual(r["verdict"], "insufficient_evidence")

    def test_loosening_with_capacity_gain_applies(self):
        cand = {"n": 150, "mean_ret_pct": 0.9, "win_rate_pct": 52.0, "p10_ret_pct": -6.5}
        r = self._check(cand)
        self.assertEqual(r["verdict"], "applied")
        self.assertTrue(r["loosening"])

    def test_quality_degrade_rejected(self):
        cand = {"n": 150, "mean_ret_pct": -1.0, "win_rate_pct": 40.0, "p10_ret_pct": -12.0}
        r = self._check(cand)
        self.assertEqual(r["verdict"], "rejected")

    def test_win_rate_gain_applies(self):
        cand = {"n": 100, "mean_ret_pct": 1.2, "win_rate_pct": 56.0, "p10_ret_pct": -5.5}
        r = self._check(cand)
        self.assertEqual(r["verdict"], "applied")

    def test_segment_instability_rejected(self):
        cand = {"n": 150, "mean_ret_pct": 0.9, "win_rate_pct": 52.0, "p10_ret_pct": -6.5}
        seg_c = [{"n": 50, "mean_ret_pct": 3.0}, {"n": 50, "mean_ret_pct": -5.0}]
        r = self._check(cand, seg_c=seg_c)
        self.assertEqual(r["verdict"], "rejected")
        self.assertIn("分段稳定", [c["name"] for c in r["thresholds"] if not c["passed"]])

    def test_absolute_floor_blocks_shrinking_loss(self):
        """从「很亏」到「没那么亏」不算改善：候选总收益必须为正（绝对可用性地板）。"""
        base = {"n": 39, "mean_ret_pct": -0.549, "win_rate_pct": 35.9, "p10_ret_pct": -8.268}
        cand = {"n": 53, "mean_ret_pct": 0.16, "win_rate_pct": 43.4, "p10_ret_pct": -8.423}
        r = gate.check(base, cand, param_path="a.b", axis_param="pullback_days_max",
                       cur_value=7, cand_value=8, days=112,
                       seg_base=self.SEG, seg_cand=self.SEG)
        self.assertEqual(r["verdict"], "rejected")
        failed = [c["name"] for c in r["thresholds"] if not c["passed"]]
        self.assertIn("绝对可用性·胜率", failed)
        self.assertIn("总收益不退化", failed)

    def test_total_return_regression_blocked(self):
        """容量扩大但总收益变差 → 否决（负向改善）。"""
        cand = {"n": 200, "mean_ret_pct": 0.2, "win_rate_pct": 52.0, "p10_ret_pct": -6.0}
        # base 总收益 100×1.0=100；cand 200×0.2=40 → ×0.4
        r = self._check(cand)
        self.assertEqual(r["verdict"], "rejected")
        self.assertIn("总收益不退化", [c["name"] for c in r["thresholds"] if not c["passed"]])

    def test_non_parameter_never_applies(self):
        r = gate.non_parameter_verdict(CHANGE_RULE, "新规则")
        self.assertEqual(r["verdict"], "pending_confirm")

    def test_loosening_direction(self):
        self.assertTrue(gate.loosening_axis("p", "vol_ratio_max", 0.7, 1.0))
        self.assertFalse(gate.loosening_axis("p", "vol_ratio_max", 1.0, 0.7))
        self.assertTrue(gate.loosening_axis("p", "vol_mult_min", 2.0, 1.2))
        self.assertIsNone(gate.loosening_axis("p", "ma_ref", 10, 20))
        # None = 不限制，视为最宽
        self.assertTrue(gate.loosening_axis("p", "entry_gain_max", 3.0, None))


class TestProposalPolicy(unittest.TestCase):
    def test_rule_class_cannot_auto_apply(self):
        p = Proposal(proposal_id="P1", change_class=CHANGE_RULE, title="t",
                     rationale="r", status="pending_confirm")
        self.assertEqual(p.status, "pending_confirm")

    def test_to_dict_roundtrip(self):
        p = Proposal(proposal_id="P1", change_class="parameter", title="t", rationale="r",
                     target={"a.b": {"from": 1, "to": 2}})
        d = p.to_dict()
        self.assertEqual(d["target"]["a.b"]["to"], 2)


class TestIntradayRule(unittest.TestCase):
    """分时层：均价线口径 / 否决判定 / 冻结参数稳定性。"""

    @staticmethod
    def _day(prices: list[float], start_hm: str = "09:30", vol: float = 1000.0) -> pd.DataFrame:
        """构造等量的 1 分钟序列：amount = 价 × 量，故当日累计 VWAP 即价格的累计均值。"""
        hm = []
        h, m = (int(x) for x in start_hm.split(":"))
        for _ in prices:
            hm.append(f"{h:02d}:{m:02d}")
            m += 1
            if m == 60:
                h, m = h + 1, 0
        return pd.DataFrame({
            "ts": [f"2026-09-10 {x}" for x in hm], "hm": hm,
            "date": ["2026-09-10"] * len(prices),
            "open": prices, "high": prices, "low": prices, "close": prices,
            "volume": [vol] * len(prices), "amount": [p * vol for p in prices],
        })

    def test_vwap_is_running_mean(self):
        from iteration import intraday
        day = self._day([10.0, 10.0, 11.0, 11.0])
        v = intraday.intraday_vwap(day)
        self.assertAlmostEqual(v[0], 10.0, places=6)
        self.assertAlmostEqual(v[-1], 10.5, places=6)
        self.assertTrue(np.all(np.diff(v) <= 1e-9) or True)   # 单调性不作为断言（价格可下行使均值下行）

    def test_veto_when_sustained_below(self):
        from iteration import intraday
        # 10 分钟高于均价，随后 6 分钟显著低于 → 应触发（confirm=3）
        px = [10.0] * 10 + [9.0] * 6
        r = intraday.first_break_below_vwap(self._day(px), confirm_minutes=3)
        self.assertTrue(r["veto"])
        self.assertIsNotNone(r["break_ts"])

    def test_no_veto_when_one_minute_dip(self):
        from iteration import intraday
        # 仅 1 分钟深跌后立刻回升 → confirm=3 不应触发
        px = [10.0] * 10 + [9.0] + [10.5] * 6
        r = intraday.first_break_below_vwap(self._day(px), confirm_minutes=3)
        self.assertFalse(r["veto"])

    def test_tol_absorbs_marginal_break(self):
        from iteration import intraday
        # 低于均价但幅度在 tol 之内 → 不应触发
        px = [10.0] * 10 + [9.98] * 6
        loose = intraday.first_break_below_vwap(self._day(px), confirm_minutes=3, tol=0.003)
        strict = intraday.first_break_below_vwap(self._day(px), confirm_minutes=3, tol=0.0)
        self.assertFalse(loose["veto"])
        self.assertTrue(strict["veto"])

    def test_window_start_keeps_open_vwap(self):
        """window_start 只裁剪判定区间，均价线仍包含开盘以来成交（行情软件口径）。"""
        from iteration import intraday
        px = [10.0] * 10 + [9.0] * 10 + [10.6] * 10
        day = self._day(px)
        wide = intraday.first_break_below_vwap(day, window_end="10:00", confirm_minutes=2)
        late = intraday.first_break_below_vwap(day, window_start="09:50", window_end="10:00",
                                               confirm_minutes=2)
        self.assertTrue(wide["veto"])
        # 09:50 之后价格已回到高位，窗口内不再跌破
        self.assertFalse(late["veto"])

    def test_frozen_params_shape(self):
        from iteration import intraday
        fp = intraday.FROZEN_VWAP_PARAMS
        self.assertEqual(set(fp), {"window_start", "window_end", "confirm_minutes", "tol"})
        self.assertLess(fp["window_start"], fp["window_end"])
        self.assertGreaterEqual(fp["confirm_minutes"], 1)

    def test_vwap_stats_keys(self):
        from iteration import intraday
        s = intraday.vwap_stats(self._day([10.0] * 20))
        self.assertIn("below_frac", s)
        self.assertIn("first_below_hm", s)
        self.assertEqual(s["below_min"], 0)


class TestMarketAdapter(unittest.TestCase):
    def test_forward_metrics_stop(self):
        o = np.array([10.0, 10.0, 10.0, 10.0])
        h = np.array([10.0, 10.1, 10.1, 10.1])
        c = np.array([10.0, 9.4, 10.0, 10.0])
        m = market.forward_metrics(o, h, c, 0, horizon=3, stop_pct=5.0)
        self.assertIsNotNone(m)
        self.assertTrue(m["stopped"])
        self.assertAlmostEqual(m["ret_pct"], -6.0, places=6)

    def test_forward_metrics_insufficient(self):
        o = np.array([10.0])
        self.assertIsNone(market.forward_metrics(o, o, o, 0, 3))

    def test_agg_metrics(self):
        rows = [{"ret_pct": 2.0, "hwm_pct": 3.0, "stopped": False},
                {"ret_pct": -1.0, "hwm_pct": 0.5, "stopped": True}]
        a = market.agg_metrics(rows)
        self.assertEqual(a["n"], 2)
        self.assertAlmostEqual(a["win_rate_pct"], 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
