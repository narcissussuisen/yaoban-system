"""回测引擎与策略检测器单元测试"""
from __future__ import annotations

import pathlib
import sys
import unittest

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def synth_df(n=120, up=True, vol=1e8) -> pd.DataFrame:
    """合成日线：上涨/下跌趋势"""
    dates = pd.bdate_range("2026-01-01", periods=n)
    if up:
        close = [10 * (1.01 ** i) for i in range(n)]
    else:
        close = [10 * (0.99 ** i) for i in range(n)]
    df = pd.DataFrame({
        "date": [d.strftime("%Y-%m-%d") for d in dates],
        "open": close, "close": close,
        "high": [c * 1.02 for c in close],
        "low": [c * 0.98 for c in close],
        "volume": [vol] * n,
    })
    return df


class TestSignalEvaluator(unittest.TestCase):
    def test_forward_returns_with_cost(self):
        from core.backtest import SignalEvaluator

        df = synth_df(60)
        sig = pd.Series(False, index=df.index)
        sig.iloc[10] = True
        ev = SignalEvaluator()
        res = ev.evaluate_signals(df, sig, forward_days=(5,))
        self.assertEqual(len(res), 1)
        self.assertIn("net5d", res.columns)
        # 上涨趋势中 5 日净收益应为正，且毛收益 > 净收益（成本为正）
        self.assertGreater(res["net5d"].iloc[0], 0)
        self.assertGreater(res["ret5d"].iloc[0], res["net5d"].iloc[0])


class TestPortfolioSim(unittest.TestCase):
    def test_buy_and_ma10_exit(self):
        from core.backtest import BacktestConfig, PortfolioSim

        # 上涨 30 天后转跌：信号买入 → 破 MA10 清仓
        n = 80
        close = [10 * (1.01 ** i) for i in range(40)] + [10 * (1.01 ** 39) * (0.99 ** i) for i in range(n - 40)]
        df = pd.DataFrame({
            "date": [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-01", periods=n)],
            "open": close, "close": close,
            "high": [c * 1.01 for c in close], "low": [c * 0.99 for c in close],
            "volume": [1e8] * n,
        })
        sig = pd.Series(False, index=df.index)
        sig.iloc[20] = True  # 第 21 日开盘买入
        sim = PortfolioSim(BacktestConfig(capital=1_000_000))
        result = sim.run({"X": df}, {"X": sig})
        self.assertGreaterEqual(result["n_trades"], 1)
        t = result["trades"][0]
        self.assertIn(t["exit_reason"], ("break_ma10", "break_ma5_half", "profit_take", "stop_loss"))
        self.assertGreaterEqual(len(result["equity_curve"]), 20)
        self.assertLessEqual(result["max_drawdown_pct"], 0)


class TestStrategies(unittest.TestCase):
    def test_huigui_signal_on_synthetic_pullback(self):
        from core import strategies as S

        # 构造：温和上涨 70 天（满足 ma60 预热）→ 缩量回调 2 天（幅度约 6%）→ 检测到信号
        n = 90
        close = [10 * (1.008 ** i) for i in range(70)]
        peak = close[-1]
        close += [peak * (1 - 0.03), peak * (1 - 0.06)]
        close += [close[-1] * (1.01 ** i) for i in range(n - 72)]
        vol = [1e8] * 70 + [2e7, 1.5e7] + [1e8] * (n - 72)
        df = pd.DataFrame({
            "date": [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-01", periods=n)],
            "open": close, "close": close,
            "high": [c * 1.02 for c in close], "low": [c * 0.98 for c in close],
            "volume": vol,
        })
        sig = S.detect_huigui(df)
        self.assertTrue(sig.sum() >= 1, "缩量回调后应产生上升回档信号")

    def test_fanbao_signal(self):
        from core import strategies as S

        n = 30
        close = [10 + i * 0.05 for i in range(n)]
        # 第 15 日阴线，第 16 日低开高走放量阳线包住前一日最高
        close[15] = close[14] - 0.3
        close[16] = close[15] + 1.2  # 收盘远高于前高
        vol = [1e8] * n
        vol[16] = 2e8
        df = pd.DataFrame({
            "date": [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-01-01", periods=n)],
            "open": [close[i] - 0.1 if i not in (15, 16) else (close[i - 1] + 0.1 if i == 15 else close[15]) for i in range(n)],
            "close": close,
            "high": [max(close[i], close[i - 1] if i > 0 else close[i]) + 0.5 for i in range(n)],
            "low": [c - 0.5 for c in close],
            "volume": vol,
        })
        sig = S.detect_fanbao(df)
        self.assertTrue(bool(sig.iloc[16]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
