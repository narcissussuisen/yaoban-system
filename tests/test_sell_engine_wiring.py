# -*- coding: utf-8 -*-
"""R0.5 回归：完整卖点引擎（core.sell::manage_day）已接进生产 tick daemon。

为什么需要这组测试（2026-09-12）：
  完整卖出纪律（破均价线减半→再破清仓 / 冲高回落破位 / 冲高止盈 / 炸板 / 次高点）
  一直只活在 `core/sell.py`，**只被研究脚本引用**，生产 `tick_monitor.py` 只有
  3 类简化触发。用户裁定「妖板模式直接上线，不做影子对照」→ 本次接线。
  接线有两个**容易在后续修改中被无声破坏**的约束，故用测试钉住：
    ① **买单不得进 daemon**：daemon 与门禁解耦（门禁失败只停买入类），
       让它能买入 = 绕过计划门禁。故 `t_enabled` 必须为 False，且执行侧要过滤 buy。
    ② **成交价必须用实时价**：引擎的 fills 落在 high[i]/low[i] 等回测理想价上，
       生产中不可达；拿它记账会系统性高估收益。
    ③ **legacy 秒级 stop_px 硬止损必须保留**：引擎的 stop_loss 盘中触及只标记、
       把即时执行留给日线兜底 → 直接替换会丢掉盘中硬止损。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from core.sell import limit_price, manage_day  # noqa: E402
import tick_monitor as tm  # noqa: E402


def _synthetic_day() -> pd.DataFrame:
    """昨收 10.00：先冲到 10.95（+9.5%，使移动止损布防），再回落 >4% → 必触发 break_low。"""
    rows = []
    t0 = "2026-09-14 "
    plan = ([(f"09:{30 + i:02d}", 10.00 + i * 0.05) for i in range(20)]
            + [(f"09:{50 + i:02d}", 10.95 - i * 0.07) for i in range(20)]
            + [(f"10:{10 + i:02d}", 9.55 - i * 0.01) for i in range(20)])
    for ts_hm, px in plan:
        rows.append([t0 + ts_hm, px, px * 1.002, px * 0.998, px, 100000.0, px * 100000.0])
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "amount"])


class SellEngineParamsTests(unittest.TestCase):
    def test_buy_paths_are_disabled(self):
        """① 买入路径必须关闭 —— daemon 不能绕过计划门禁。"""
        for k in ("t_enabled", "dragon_link_sell", "sector_retreat_sell"):
            self.assertIs(tm.SELL_ENGINE_PARAMS[k], False, f"{k} 必须为 False")

    def test_sell_rules_remain_enabled(self):
        """完整卖出纪律必须真的开着（否则接线等于空转）。"""
        for k in ("vwap_halve", "vwap_break_all", "break_low_clear",
                  "zhaban_sell", "second_high_sell"):
            self.assertIs(tm.SELL_ENGINE_PARAMS[k], True, f"{k} 必须为 True")
        self.assertGreater(tm.SELL_ENGINE_PARAMS["profit_take_pct"], 0)

    def test_engine_emits_sells_but_no_buys(self):
        """在生产参数下跑合成日线：必须有卖单、且**一笔买单都没有**。"""
        df = _synthetic_day()
        eng = manage_day(df, 10.00, 1000, None, 9.90,
                         params=tm.SELL_ENGINE_PARAMS, limit_px=limit_price(10.00, "600000"))
        fills = eng.get("fills") or []
        buys = [f for f in fills if f.get("side") == "buy"]
        sells = [f for f in fills if f.get("side") == "sell"]
        self.assertEqual(buys, [], f"生产参数下不得出现买单: {buys}")
        self.assertTrue(sells, "引擎未触发任何卖出理由 —— 接线等于空转")

    def test_t_enabled_default_would_emit_buys(self):
        """反证：若按 DEFAULT 参数（做T开启）就会出买单 —— 证明上面的排除是必要的，不是多虑。"""
        df = _synthetic_day()
        eng = manage_day(df, 10.00, 1000, None, 9.90,
                         params={**tm.SELL_DEFAULTS, "dragon_link_sell": False,
                                 "sector_retreat_sell": False},
                         limit_px=limit_price(10.00, "600000"))
        buys = [f for f in (eng.get("fills") or []) if f.get("side") == "buy"]
        self.assertTrue(buys, "预期 DEFAULT 参数会发买单（t_enabled=True）")


class TickMonitorWiringContractTests(unittest.TestCase):
    """源码级契约：这些约束一旦被改掉，运行期不会报错、只会静默失去保护。"""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "scripts" / "tick_monitor.py").read_text(encoding="utf-8")

    def test_engine_is_called_with_production_params(self):
        self.assertIn("from core.sell import", self.src)
        self.assertIn("manage_day", self.src)
        self.assertIn("params=SELL_ENGINE_PARAMS", self.src)
        self.assertIn("limit_px=lup", self.src)

    def test_buy_fills_are_filtered_on_execution_side(self):
        """执行侧必须显式过滤 side != 'sell'（只靠参数不够：引擎若改了 side 命名就漏了）。"""
        self.assertIn("if f.get('side')!='sell':continue", self.src)

    def test_execution_price_is_live_not_engine_bar_price(self):
        """② 成交价取实时 px，不得用引擎的 bar 价（那会系统性高估）。"""
        self.assertIn("fired.add((sym,trig,fts));epx=px;", self.src)

    def test_legacy_intraday_stop_is_kept(self):
        """③ 秒级硬止损必须保留 —— 持仓保护不可降级。"""
        self.assertIn("if pos.get('stop_px') and px<=float(pos['stop_px']):", self.src)
        self.assertIn("trigs.append(('stop_loss',int(pos['qty']),''))", self.src)

    def test_engine_failure_degrades_instead_of_killing_daemon(self):
        """引擎异常必须降级为 legacy 触发 —— daemon 死 = 持仓裸奔（9/3 事故形态）。"""
        self.assertIn("except Exception as e:", self.src)
        self.assertIn("[ENGINE-FAIL]", self.src)
        self.assertIn("trigs.append(('zhaban_sell'", self.src)


class DailyGuardTests(unittest.TestCase):
    """R0.5 Layer 2：日线兜底（破 MA5 减半 / 破 MA10 清 / 时间止损）。

    密封测试：临时造一只票的日线 parquet 并 monkeypatch `DAILY_REBUILT`，
    不依赖 F 盘真实数据（否则测试会随外部数据源状态飘）。
    判据逐条对照 `core/sell.py::simulate_hold`（L416-457）。
    """

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        # 30 个"交易日"，收盘价恒定 10.0 → MA5=MA10=10.0，便于构造"破线"场景
        dates = [f"2026-08-{d:02d}" for d in range(1, 31)]
        self.dates = dates
        self.sym = "600000"
        self.tm_dr_orig = tm.DAILY_REBUILT
        tm.DAILY_REBUILT = self.td
        self.addCleanup(lambda: setattr(tm, "DAILY_REBUILT", self.tm_dr_orig))

    def _write(self, closes):
        df = pd.DataFrame({
            "symbol": [self.sym] * len(self.dates),
            "date": self.dates,
            "open": closes, "high": closes, "low": closes, "close": closes,
            "volume": [1e5] * len(self.dates), "amount": [1e6] * len(self.dates),
        })
        df.to_parquet(self.td / f"{self.sym}.parquet")

    def _sig(self, entry_date, close_px, entry_px):
        tm._daily_guard_cache.clear()
        return tm.daily_guard_signals(self.sym, entry_date, "2026-08-31",
                                      close_px, entry_px, tm.SELL_ENGINE_PARAMS)

    def test_k_below_two_does_not_trigger(self):
        """k=1（买入次日即当日）不得触发任何日线兜底 —— k>=2 是 ma5_halve 的门槛。"""
        self._write([10.0] * len(self.dates))
        self.assertEqual(self._sig(self.dates[-1], 9.0, 5.0), [])

    def test_ma5_halve_requires_profit(self):
        """破 MA5 减半只适用『有浮盈』语境（回档刚买入不启用）。"""
        self._write([10.0] * len(self.dates))
        self.assertEqual(self._sig(self.dates[-3], 9.0, 9.5), [], "亏损时不得减半")
        sig = self._sig(self.dates[-3], 9.0, 5.0)
        self.assertEqual(sig, [{"reason": "ma5_halve", "frac": 0.5}])

    def test_time_stop_clears_and_takes_precedence_over_halve(self):
        """⚠️ 关键不变量：k>=horizon 时必须**清仓**(frac=1.0)，且**优先于** ma5_halve。

        simulate_hold 的实际顺序是 ma5_halve 之后再独立走 time_stop → 净效果=全清。
        若写成"互斥且 ma5 优先"，horizon 日只卖一半 ⇒ **少卖**、不等价；
        若同时发两条 ⇒ 执行侧用同一份 t1 重复卖 ⇒ **超卖** → 卖单失败 → daemon 退出。
        """
        self._write([10.0] * len(self.dates))
        horizon = int(tm.SELL_ENGINE_PARAMS["time_stop_days"])
        entry = self.dates[-1 - horizon]          # k == horizon
        sig = self._sig(entry, 9.0, 5.0)          # 同时满足"破 MA5 + 有浮盈"
        self.assertEqual(sig, [{"reason": "time_stop", "frac": 1.0}],
                         f"k=horizon 必须清仓且优先于减半，实得 {sig}")

    def test_ma10_clear_wins_over_time_stop(self):
        """ma10_clear 与 time_stop 同时成立时，reason 记为 ma10_clear（更具体）。同为 frac=1.0。"""
        closes = [10.0] * (len(self.dates) - 3) + [9.0, 9.0, 9.0]   # 连续 3 日收盘低于 MA10
        self._write(closes)
        horizon = int(tm.SELL_ENGINE_PARAMS["time_stop_days"])
        entry = self.dates[-1 - max(horizon, 3)]
        sig = self._sig(entry, 8.0, 5.0)
        self.assertEqual(sig, [{"reason": "ma10_clear", "frac": 1.0}], f"实得 {sig}")

    def test_missing_data_degrades_silently(self):
        """取不到日线 / entry 不在日线里 → 返回 []（保守跳过），不得抛异常打死 daemon。"""
        self._write([10.0] * len(self.dates))
        self.assertEqual(self._sig("1990-01-01", 9.0, 5.0), [])
        self.assertEqual(tm.daily_guard_signals("", "2026-08-01", "2026-08-31", 9.0, 5.0,
                                                tm.SELL_ENGINE_PARAMS), [])

    def test_k_is_computed_from_entry_not_pos_days(self):
        """⚠️ `pos['days']` 在生产里恒为 0（ledger 只在买入时置 0，无脚本 +1）
        ⇒ 源码必须从 entry_ts/日线自算 k，否则三条规则永不触发（接了也是死代码）。"""
        src = (ROOT / "scripts" / "tick_monitor.py").read_text(encoding="utf-8")
        self.assertIn("k = di - i0", src)
        fn = src.split("def daily_guard_signals", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("pos['days']", fn)
        self.assertNotIn('pos["days"]', fn)

    def test_wired_only_in_after_hours_window(self):
        """日频判据只在盘后固定价格窗口评估（成交价=收盘价）；盘中评估会反复命中。"""
        src = (ROOT / "scripts" / "tick_monitor.py").read_text(encoding="utf-8")
        self.assertIn("if in_after_hours_session(hm2):", src)
        self.assertIn("for g in daily_guard_signals(", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
