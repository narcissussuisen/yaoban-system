"""S2 竞价初筛 / S3 观察名单 的纯函数测试（`src/core/selection_chain.py`）。

纪律锚（本文件的核心不是"算得对"，而是钉住三条纪律）：
1. **不引入未标定阈值** ⇒ 竞价大涨/大跌都不得被判 `keep=False`；
2. **数据缺失只标注、不禁用** ⇒ `NO_QUOTE` / `NO_TURN` 是"无法评估"，不是策略否决；
3. **排序键顺序固定** ⇒ in_plan → 信号新鲜度 → 共振数 → 竞价量能分位 → 竞价额 → sym。
"""
from __future__ import annotations
import pathlib, sys, unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT.parent / 'py_libs'))

from core import selection_chain as SC


def rec(sym, pattern="huigui", patterns=None, sig="2026-09-11", bars=1, close=10.0):
    return {"sym": sym, "pattern": pattern, "patterns": patterns or [pattern],
            "sig_date": sig, "bars_since_sig": bars, "close_asof": close}


def q(name="甲", px=10.0, chg=1.0, amt=1000.0, turn=0.5):
    return {"name": name, "px": px, "chg": chg, "vol": 1000, "amt": amt, "turn": turn}


class PctRankTests(unittest.TestCase):

    def test_ascending_rank(self):
        self.assertEqual(SC.pct_rank([1, 2, 3]), [0.0, 0.5, 1.0])

    def test_ties_share_average_rank(self):
        # [5,5,5,1]：1 占名次 0（最小）；三只并列占名次 1..3 ⇒ 平均名次 2 ⇒ 2/3；分母 n-1=3
        self.assertEqual(SC.pct_rank([5, 5, 5, 1]), [2 / 3, 2 / 3, 2 / 3, 0.0])

    def test_none_gets_neutral_and_does_not_take_a_rank(self):
        out = SC.pct_rank([1, None, 3])
        self.assertEqual(out[1], 0.5)                 # 缺失 = 不可分辨
        self.assertEqual(out[0], 0.0)
        self.assertEqual(out[2], 1.0)

    def test_single_or_constant_is_neutral(self):
        self.assertEqual(SC.pct_rank([7]), [0.5])
        self.assertEqual(SC.pct_rank([4, 4]), [0.5, 0.5])
        self.assertEqual(SC.pct_rank([]), [])


class AuctionScreenTests(unittest.TestCase):

    def test_pool_members_without_quote_are_flagged_not_silently_dropped(self):
        pool = [rec("600644"), rec("002436")]
        s = SC.build_auction_screen(pool, {"600644": q()})
        self.assertEqual(s["stats"]["n_pool"], 2)
        self.assertEqual(s["stats"]["n_quoted"], 1)
        self.assertEqual(s["stats"]["n_no_quote"], 1)
        by = {r["sym"]: r for r in s["rows"]}
        self.assertTrue(by["600644"]["keep"])
        self.assertFalse(by["002436"]["keep"])
        self.assertEqual(by["002436"]["drop_code"], SC.DROP_NO_QUOTE)

    def test_missing_volume_fields_is_no_turn(self):
        s = SC.build_auction_screen([rec("600644")], {"600644": {"name": "乐山电力", "px": 9.93, "chg": 1.0}})
        row = s["rows"][0]
        self.assertFalse(row["keep"])
        self.assertEqual(row["drop_code"], SC.DROP_NO_TURN)

    def test_no_threshold_is_applied_to_auction_move(self):
        # 纪律锚 1：竞价 +9%（极端高开）与 -9%（极端低开）都不得被排除 —— 阈值未标定，只落痕
        pool = [rec("600001"), rec("600002"), rec("600003")]
        s = SC.build_auction_screen(pool, {"600001": q(chg=9.0), "600002": q(chg=-9.0), "600003": q(chg=0.0)})
        self.assertTrue(all(r["keep"] for r in s["rows"]))
        self.assertTrue(all(r["drop_code"] is None for r in s["rows"]))

    def test_ranks_computed_only_over_quoted_rows(self):
        pool = [rec("600001"), rec("600002"), rec("600003")]
        s = SC.build_auction_screen(pool, {"600001": q(turn=1.0), "600002": q(turn=3.0)})
        by = {r["sym"]: r for r in s["rows"]}
        self.assertEqual(by["600001"]["turn_rank"], 0.0)
        self.assertEqual(by["600002"]["turn_rank"], 1.0)
        self.assertIsNone(by["600003"]["turn_rank"])      # 无行情 ⇒ 不参与名次、不给分位

    def test_pool_labels_are_carried_through(self):
        s = SC.build_auction_screen([rec("600644", pattern="zt_huicai", patterns=["zt_huicai", "huigui"],
                                        sig="2026-09-11", bars=1, close=9.93)], {"600644": q()})
        r = s["rows"][0]
        self.assertEqual(r["pattern"], "zt_huicai")
        self.assertEqual(r["patterns"], ["zt_huicai", "huigui"])
        self.assertEqual(r["sig_date"], "2026-09-11")
        self.assertEqual(r["bars_since_sig"], 1)
        self.assertEqual(r["close_asof"], 9.93)

    def test_empty_inputs_do_not_crash(self):
        s = SC.build_auction_screen([], {})
        self.assertEqual(s["rows"], [])
        self.assertEqual(s["stats"]["n_pool"], 0)
        self.assertEqual(SC.build_auction_screen(None, None)["rows"], [])


class WatchlistTests(unittest.TestCase):

    def _screen(self, rows):
        for r in rows:
            r.setdefault("keep", True)
            r.setdefault("drop_code", None)
            r.setdefault("patterns", [r.get("pattern") or "huigui"])
            r.setdefault("bars_since_sig", 1)
            r.setdefault("turn_rank", 0.5)
            r.setdefault("auc_amt_wan", 0.0)
            r.setdefault("in_plan", False)
        return {"rows": rows, "stats": {}, "n_target": 5}

    def test_in_plan_wins_over_everything(self):
        rows = [{"sym": "600001", "in_plan": False, "bars_since_sig": 0, "turn_rank": 1.0},
                {"sym": "600002", "in_plan": True, "bars_since_sig": 5, "turn_rank": 0.0}]
        w = SC.build_watchlist(self._screen(rows), target_n=1)
        self.assertEqual(w["picks"][0]["sym"], "600002")

    def test_freshness_then_resonance_then_turn_rank(self):
        rows = [
            {"sym": "600003", "bars_since_sig": 2, "patterns": ["a"], "turn_rank": 0.9},
            {"sym": "600001", "bars_since_sig": 0, "patterns": ["a"], "turn_rank": 0.1},
            {"sym": "600002", "bars_since_sig": 0, "patterns": ["a"], "turn_rank": 0.1},
            {"sym": "600004", "bars_since_sig": 0, "patterns": ["a", "b"], "turn_rank": 0.1},
        ]
        w = SC.build_watchlist(self._screen(rows), target_n=4)
        self.assertEqual([p["sym"] for p in w["picks"]], ["600004", "600001", "600002", "600003"])

    def test_turn_rank_is_last_resort_before_tiebreak(self):
        rows = [{"sym": "600001", "bars_since_sig": 1, "patterns": ["a"], "turn_rank": 0.1},
                {"sym": "600002", "bars_since_sig": 1, "patterns": ["a"], "turn_rank": 0.9}]
        w = SC.build_watchlist(self._screen(rows), target_n=2)
        self.assertEqual([p["sym"] for p in w["picks"]], ["600002", "600001"])

    def test_unquoted_rows_never_enter_watchlist(self):
        rows = [{"sym": "600001", "keep": False, "drop_code": "NO_QUOTE"},
                {"sym": "600002", "keep": True, "drop_code": None}]
        w = SC.build_watchlist(self._screen(rows), target_n=5)
        self.assertEqual([p["sym"] for p in w["picks"]], ["600002"])
        self.assertEqual(w["n_observed"], 1)              # 温度计口径：只数"可排序"的

    def test_target_n_caps_and_n_observed_is_reported(self):
        rows = [{"sym": f"60000{i}"} for i in range(1, 9)]
        w = SC.build_watchlist(self._screen(rows), target_n=3)
        self.assertEqual(len(w["picks"]), 3)
        self.assertEqual(w["n_observed"], 8)

    def test_rank_key_is_stable_and_declared(self):
        rows = [{"sym": "600001"}]
        w = SC.build_watchlist(self._screen(rows), target_n=5)
        self.assertEqual(w["rank_key"],
                         ["in_plan", "bars_since_sig", "n_resonance", "turn_rank_desc", "amt_desc"])

    def test_deterministic_on_full_tie(self):
        rows = [{"sym": "600003"}, {"sym": "600001"}, {"sym": "600002"}]
        w = SC.build_watchlist(self._screen(rows), target_n=3)
        self.assertEqual([p["sym"] for p in w["picks"]], ["600001", "600002", "600003"])


class SentimentPreTests(unittest.TestCase):

    def test_counts_and_pool_quantiles(self):
        market = [{"chg_pct": 1.0, "amount_wan": 100.0}, {"chg_pct": -1.0, "amount_wan": 200.0},
                  {"chg_pct": 0.0, "amount_wan": 300.0}]
        pool_rows = [{"auc_chg": -2.0, "pattern": "huigui"}, {"auc_chg": 0.0, "pattern": "huigui"},
                     {"auc_chg": 2.0, "pattern": "zt_huicai"}]
        s = SC.build_sentiment_pre(market, pool_rows)
        self.assertEqual(s["tier"], "auction")
        self.assertEqual((s["n_up_open"], s["n_down_open"], s["n_flat_open"]), (1, 1, 1))
        self.assertEqual(s["amt_total_wan"], 600.0)
        self.assertEqual(s["pool_n"], 3)
        self.assertEqual(s["pool_median_chg"], 0.0)
        self.assertEqual(s["pool_chg_q25"], -1.0)
        self.assertEqual(s["by_pattern"]["huigui"]["n"], 2)
        self.assertEqual(s["by_pattern"]["huigui"]["median_chg"], -1.0)

    def test_empty_inputs_are_null_not_zero(self):
        s = SC.build_sentiment_pre([], [])
        self.assertIsNone(s["up_open_ratio"])
        self.assertIsNone(s["pool_median_chg"])
        self.assertEqual(s["pool_n"], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
