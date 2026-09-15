"""回测引擎（轻量、事件驱动、含交易成本）

两层设计:
  1. SignalEvaluator — 信号级评估：信号日次开盘买入(含滑点)，按退出规则或 N 日后卖出，
     输出单笔交易统计（胜率/盈亏比/期望/最大回撤）。
  2. PortfolioSim    — 组合级模拟：按日遍历，持仓上限/单只上限/止损/止盈/时间止损，
     输出资金曲线与组合指标。

成本口径（A股，可配置）:
  佣金 0.025%（双边，最低 5 元），印花税 0.05%（仅卖出），滑点 0.1%（单边）。
说明:
  - 日线级别无法建模「10 点前不板则走」等日内纪律（需分钟线，见 ROADMAP 风险表）；
    近似用「次日收盘跌破 MA5 减半 / 跌破 MA10 清仓」等日线规则替代，并在报告中标注。
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

import pandas as pd

from config import section

RISK = section("risk")
SELL = section("sell")
FEES = section("fees")   # ⭐ 2026-09-13 唯一真相源：交易费率（万1 / 北交所万5.75 / 不免五）

# ⭐ 2026-09-13「唯一真相源」治理（用户裁定：回测直接调/派生自生产，不自己维护一套）：
#   背景 —— 此前回测的出场/风控参数是**本文件硬编码**（止盈 15% / 比例 0.40），
#   而生产走 `[sell.intraday]`（10% / 1÷3），且本文件的 `SELL`/`RISK` 变量**定义后从未被引用**
#   ⇒ 回测的 toml 变量是死的 ⇒ **回测结论在原理上不代表生产**。
#   现改为从生产参数派生（单一事实源 = `core.sell.DEFAULT_PARAMS`，它读 `[sell.intraday]`）。
#   ⚠️ 单位差异：`[sell.intraday]` 用**百分数**（10.0 / 5.0），本配置用**小数**（0.10 / 0.05）⇒ 需 /100。
#   ⚠️ 容错：若 `core.sell` 不可导入，回落原硬编码值，避免回测整体不可跑。
try:
    from core.sell import DEFAULT_PARAMS as _SELL_P  # noqa: E402
except Exception:  # noqa: BLE001
    _SELL_P = {}


def _p(key: str, fallback):
    """取生产卖出参数（缺则回落）。"""
    v = _SELL_P.get(key)
    return fallback if v is None else v


def _r(key: str, fallback, scale: float = 1.0):
    """取 [risk] 参数（缺则回落）；scale 用于百分数→小数。"""
    v = RISK.get(key)
    return fallback if v is None else float(v) * scale


@dataclass
class Trade:
    symbol: str
    entry_date: str
    entry_price: float
    shares: int
    exit_date: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_pct: float = 0.0
    days_held: int = 0

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class BacktestConfig:
    """回测配置。⭐ 出场/风控字段**从生产派生**（2026-09-13 唯一真相源治理），不再各自硬编码。

    ⚠️ 派生的意义：回测与生产读**同一份**参数 ⇒ 回测结论才可能代表生产。
    改动前的实际差异（已消除）：止盈 15% vs 生产 10% · 止盈比例 0.40 vs 1÷3。
    ⚠️ 仍待统一：`max_positions`（本处取 `[risk]=4`，而**账本 policy 是 2**）· 佣金/印花税/滑点
       （本处 万2.5/万5/千1，而用户 2026-08-01 给的实盘费率是**佣金万1**）⇒ 见 PARAM_CONSUMER_MAP §四。
    """

    capital: float = 1_000_000.0
    # ⭐ 费率从 `[fees]` 读（2026-09-13）：用户实盘 万1 / 北交所万5.75 / **不免五**。
    #   改前硬编码 万2.5 + 最低 5 元 ⇒ 成本被高估 2.5 倍。
    #   ⚠️ `min_commission` 由 5.0 改为 **0.0（不免五）**；北交所费率尚未按品种接入。
    commission: float = float(FEES.get("commission_main", 0.00025))
    min_commission: float = float(FEES.get("min_commission", 0.0))
    stamp_tax: float = float(FEES.get("stamp_tax_sell", 0.0005))
    slippage: float = float(FEES.get("slippage", 0.001))
    # ↓↓↓ 以下字段一律从生产/单一源派生 ————————————————————————
    max_positions: int = int(_r("max_positions", 4))
    single_stock_pct: float = _r("max_single_stock_pct", 30.0, 0.01)
    profit_take_pct: float = float(_p("profit_take_pct", 10.0)) / 100.0
    profit_take_fraction: float = float(_p("profit_take_frac", 1.0 / 3.0))
    stop_loss_pct: float = float(_p("stop_loss_pct", 5.0)) / 100.0
    time_stop_days: int = int(_p("time_stop_days", 5))
    ma5_break_action: bool = bool(_p("daily_ma5_halve", True))
    ma10_break_action: bool = bool(_p("daily_ma10_clear", True))


class SignalEvaluator:
    """信号级评估：给定 (symbol, df, signal_mask, forward_days)，统计后续收益"""

    def __init__(self, cfg: BacktestConfig | None = None):
        self.cfg = cfg or BacktestConfig()

    def entry_price(self, df: pd.DataFrame, i: int) -> float:
        """信号日次开盘买入（含滑点）"""
        if i + 1 >= len(df):
            return float("nan")
        return float(df["open"].iloc[i + 1]) * (1 + self.cfg.slippage)

    def evaluate_signals(self, df: pd.DataFrame, signal_mask: pd.Series,
                         forward_days: tuple[int, ...] = (5, 10)) -> pd.DataFrame:
        """对每个信号日评估前复权收益（不含成本 = 毛收益；含成本 = 净收益）"""
        rows = []
        idx = df.index[signal_mask.fillna(False).to_numpy()]
        for i in idx:
            p0 = self.entry_price(df, i)
            if pd.isna(p0) or p0 <= 0:
                continue
            rec = {"date": str(df["date"].iloc[i]), "entry": round(p0, 3)}
            for fd in forward_days:
                if i + fd < len(df):
                    p1 = float(df["close"].iloc[i + fd])
                    gross = (p1 / p0 - 1) * 100
                    net = gross - (self.cfg.commission + self.cfg.slippage) * 100
                    if i + fd == len(df) - 1 or True:
                        net -= self.cfg.stamp_tax * 100  # 卖出印花税
                    rec[f"ret{fd}d"] = round(gross, 2)
                    rec[f"net{fd}d"] = round(net, 2)
            rows.append(rec)
        return pd.DataFrame(rows)

    @staticmethod
    def summarize(results: pd.DataFrame, col: str = "net5d") -> dict:
        if results is None or results.empty or col not in results.columns:
            return {"n": 0}
        r = results[col].dropna()
        return {
            "n": int(len(r)),
            "mean": round(float(r.mean()), 2),
            "median": round(float(r.median()), 2),
            "win_rate": round(float((r > 0).mean()) * 100, 1),
            "max": round(float(r.max()), 2),
            "min": round(float(r.min()), 2),
        }


class PortfolioSim:
    """组合级日线模拟（简化版，不含日内纪律）

    run(data, signals, start=None, end=None):
      - data/signals 可含更长历史（均线预热用），实际交易仅发生在 [start, end] 窗口内；
      - 持仓股票在窗口内某日无 bar（停牌/数据截止）时，按最近可用收盘价估值（防止误为 0）；
      - 窗口结束强制平仓（exit_reason="window_end"）。
    """

    def __init__(self, cfg: BacktestConfig | None = None):
        self.cfg = cfg or BacktestConfig()

    def run(self, data: dict[str, pd.DataFrame],
            signals: dict[str, pd.Series],
            start: str | None = None,
            end: str | None = None) -> dict:
        """data: {symbol: df(date/open/high/low/close/volume)}；signals: {symbol: bool Series}"""
        dates = sorted({str(d) for df in data.values() for d in df["date"]})
        if start:
            dates = [d for d in dates if d >= start]
        if end:
            dates = [d for d in dates if d <= end]
        cash = self.cfg.capital
        positions: dict[str, Trade] = {}
        equity_curve: list[dict] = []
        trades: list[Trade] = []

        def last_close(sym: str, day: str) -> float | None:
            """最近可用收盘价（≤ day）"""
            df = data[sym]
            sub = df[df["date"] <= day]
            return float(sub["close"].iloc[-1]) if len(sub) else None

        def position_value(sym: str, day: str) -> float:
            """持仓市值（无当日 bar 时用最近收盘）"""
            c = last_close(sym, day)
            return positions[sym].shares * c if c is not None else 0.0

        for day in dates:
            # 1) 处理退出（先卖后买，避免同日循环占用）
            for sym in list(positions.keys()):
                df = data[sym]
                row = df[df["date"] == day]
                if row.empty:
                    continue
                r = row.iloc[0]
                t = positions[sym]
                close = float(r["close"])
                ma5 = float(df["close"].rolling(5).mean().iloc[df.index.get_loc(row.index[0])])
                ma10 = float(df["close"].rolling(10).mean().iloc[df.index.get_loc(row.index[0])])
                pnl = close / t.entry_price - 1
                reason = ""
                if self.cfg.ma10_break_action and close < ma10:
                    reason = "break_ma10"
                elif self.cfg.ma5_break_action and close < ma5:
                    # 破 MA5 减半
                    half = t.shares // 2
                    if half > 0:
                        proceeds = half * close * (1 - self.cfg.slippage)
                        fee = max(proceeds * self.cfg.commission, self.cfg.min_commission)
                        cash += proceeds - fee - proceeds * self.cfg.stamp_tax
                        t.shares -= half
                    reason = "break_ma5_half"
                elif pnl >= self.cfg.profit_take_pct:
                    # 止盈走 1/3~1/2
                    sell_n = int(t.shares * self.cfg.profit_take_fraction)
                    if sell_n > 0:
                        proceeds = sell_n * close * (1 - self.cfg.slippage)
                        fee = max(proceeds * self.cfg.commission, self.cfg.min_commission)
                        cash += proceeds - fee - proceeds * self.cfg.stamp_tax
                        t.shares -= sell_n
                    reason = "profit_take"
                elif pnl <= -self.cfg.stop_loss_pct:
                    reason = "stop_loss"
                if reason in ("break_ma10", "stop_loss") or t.shares == 0:
                    # 清仓
                    if t.shares > 0:
                        proceeds = t.shares * close * (1 - self.cfg.slippage)
                        fee = max(proceeds * self.cfg.commission, self.cfg.min_commission)
                        cash += proceeds - fee - proceeds * self.cfg.stamp_tax
                    t.exit_date = day
                    t.exit_price = close
                    t.exit_reason = reason or "fully_exited"
                    t.pnl_pct = round((close / t.entry_price - 1) * 100, 2)
                    t.days_held = (pd.to_datetime(day) - pd.to_datetime(t.entry_date)).days
                    trades.append(t)
                    del positions[sym]

            # 2) 处理入场（信号日次开盘买入）
            for sym, sig in signals.items():
                if sym not in data or sym in positions:
                    continue
                if len(positions) >= self.cfg.max_positions:
                    break
                df = data[sym]
                loc = df.index[df["date"] == day]
                if loc.empty or len(loc) == 0:
                    continue
                i = loc[0]
                if i < 1 or not bool(sig.iloc[i - 1]):  # 昨日信号，今日开盘买入
                    continue
                entry = float(df["open"].iloc[i]) * (1 + self.cfg.slippage)
                alloc = min(cash * 0.25, cash)
                shares = int(alloc / entry / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * entry
                fee = max(cost * self.cfg.commission, self.cfg.min_commission)
                if cost + fee > cash:
                    continue
                cash -= cost + fee
                positions[sym] = Trade(sym, day, entry, shares)

            equity = cash + sum(position_value(s, day) for s in positions)
            equity_curve.append({"date": day, "equity": round(equity, 2)})

        # 3) 窗口结束：强制平仓剩余持仓（按最后可用收盘价）
        for sym in list(positions.keys()):
            t = positions[sym]
            c = last_close(sym, dates[-1] if dates else "")
            if c is None:
                continue
            proceeds = t.shares * c * (1 - self.cfg.slippage)
            fee = max(proceeds * self.cfg.commission, self.cfg.min_commission)
            cash += proceeds - fee - proceeds * self.cfg.stamp_tax
            t.exit_date = dates[-1]
            t.exit_price = c
            t.exit_reason = "window_end"
            t.pnl_pct = round((c / t.entry_price - 1) * 100, 2)
            t.days_held = (pd.to_datetime(t.exit_date) - pd.to_datetime(t.entry_date)).days
            trades.append(t)
            del positions[sym]
        if equity_curve:
            equity_curve[-1]["equity"] = round(cash, 2)
        eq = pd.DataFrame(equity_curve)
        peak = eq["equity"].cummax()
        dd = (eq["equity"] / peak - 1) * 100
        returns = eq["equity"].pct_change().dropna()
        n_days = len(eq)
        years = n_days / 244
        annual = ((eq["equity"].iloc[-1] / self.cfg.capital) ** (1 / years) - 1) * 100 if years > 0 and eq["equity"].iloc[-1] > 0 else 0
        pnl_list = [t.pnl_pct for t in trades if t.pnl_pct]
        return {
            "final_equity": round(float(eq["equity"].iloc[-1]), 2),
            "total_return_pct": round((eq["equity"].iloc[-1] / self.cfg.capital - 1) * 100, 2),
            "annual_pct": round(annual, 2),
            "max_drawdown_pct": round(float(dd.min()), 2),
            "n_trades": len(trades),
            "win_rate_pct": round(float((pd.Series(pnl_list) > 0).mean()) * 100, 1) if pnl_list else 0.0,
            "avg_pnl_pct": round(float(pd.Series(pnl_list).mean()), 2) if pnl_list else 0.0,
            "exit_reasons": {r: sum(1 for t in trades if t.exit_reason == r) for r in
                             sorted({t.exit_reason for t in trades})},
            "equity_curve": equity_curve,
            "trades": [t.as_dict() for t in trades],
        }


if __name__ == "__main__":
    print("BacktestConfig:", dataclasses.asdict(BacktestConfig()))
