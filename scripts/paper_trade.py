"""P5 模拟盘回放：信号 → 组合模拟 → 交易日志落库 → 周度报告

流程（复用 PortfolioSim 引擎）:
  1. 对日期范围内每个交易日，用五大战法检测器扫描全池（信号日 = 当日）
  2. PortfolioSim 按次日开盘成交，应用日线级退出规则（MA5减半/MA10清仓/止盈/止损/时间止损）
  3. 全部成交导出到 journal.db（trades/exits，规则触发标记 compliant=1）
  4. 输出模拟盘周报（胜率/盈亏比/回撤/退出原因分布）

用法:
    python scripts/paper_trade.py --start 2026-07-01 --end 2026-08-21
    python scripts/paper_trade.py --start 2026-07-01 --end 2026-08-21 --no-journal   # 只出报告不落库
输出: outputs/paper_trade_report.md
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from data.store import Store  # noqa: E402
from core import strategies as S  # noqa: E402
from core import indicators as ind  # noqa: E402
from core.backtest import PortfolioSim, BacktestConfig  # noqa: E402
from core.env_score import env_score, regime_from_simple  # noqa: E402
from core.names import stock_names  # noqa: E402

OUT = ROOT / "outputs" / "paper_trade_report.md"
MIN_ENV = 2  # 环境过滤：简版分 ≥2（中性）才允许开仓


def rows_to_df(rows) -> pd.DataFrame:
    cols = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    df = pd.DataFrame(rows, columns=cols[:len(rows[0])])
    for c in ("open", "high", "low", "close", "volume", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = df["date"].astype(str)
    return df.reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser(description="模拟盘回放")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--no-journal", action="store_true", help="只出报告不写日志库")
    args = ap.parse_args()

    store = Store()
    names = stock_names()
    symbols = [r[0] for r in store.conn.execute(
        "SELECT DISTINCT symbol FROM stock_daily").fetchall()]

    # 日期序列（用指数交易日历）
    idx_rows = store.get_index("sh000001", start=args.start, end=args.end)
    if not idx_rows:
        print("指数数据不足，无法确定交易日历")
        return
    dates = [r[1] for r in idx_rows]

    # 环境过滤：用目标日之前的指数数据逐日计算简版环境分（信号日当天收盘后判定）
    idx_full = store.get_index("sh000001")
    env_ok_by_date: dict[str, bool] = {}
    if idx_full:
        idx_df = pd.DataFrame(idx_full, columns=["symbol", "date", "open", "high", "low", "close", "volume"])
        for d in dates:
            sub = idx_df[idx_df["date"] <= d]
            if len(sub) >= 25:
                e = env_score(sub)
                if set(e["available_dims"]) <= {"trend", "volume"}:
                    simple = e["dims"]["trend"] + e["dims"]["volume"]
                    env_ok_by_date[d] = simple >= MIN_ENV
                else:
                    env_ok_by_date[d] = True
            else:
                env_ok_by_date[d] = True

    data, signals = {}, {}
    for sym in symbols:
        db = store.get_stock(sym, start=args.start, end=args.end)
        if not db or len(db) < 5:
            continue
        df = rows_to_df(db)
        # 补齐回测所需历史（信号检测需要 70+ 天前置数据）
        full = rows_to_df(store.get_stock(sym))
        if len(full) < 80:
            continue
        data[sym] = full
        sig = pd.Series(False, index=full.index)
        # 上升回档：仅当日放量阳线（买点确认，v4.0 §4.2）才触发
        hg = S.detect_huigui(full, mode="live")
        bullish = full["close"] > full["open"]
        vol_up = full["volume"] > full["volume"].shift(1)
        hg_confirm = hg & bullish & vol_up
        sig |= hg_confirm
        for det in (S.detect_zt_huicai(full), S.detect_fanbao(full),
                    S.detect_xianren(full, with_confirm=True)):
            sig |= det
        # 只保留回放窗口内 + 环境允许的信号
        sig &= (full["date"] >= args.start) & (full["date"] <= args.end)
        sig &= full["date"].map(lambda d: env_ok_by_date.get(d, True))
        # 去重：5 日内仅一次
        dedup = pd.Series(False, index=full.index)
        last = -10**9
        for i in full.index[sig.to_numpy()]:
            if i - last >= 5:
                dedup.iloc[i] = True
                last = i
        signals[sym] = dedup

    sim = PortfolioSim(BacktestConfig(capital=args.capital))
    result = sim.run(data, signals, start=args.start, end=args.end)

    # 周报
    L: list[str] = []
    L.append(f"# 模拟盘回放报告 — {args.start} ~ {args.end}")
    L.append("")
    L.append(f"> 初始资金 {args.capital:,.0f}；成本=佣金0.025%双边(最低5元)+印花税0.05%卖出+滑点0.1%")
    L.append(f"> 信号源：五大战法（上升回档 live / 涨停回踩 / 趋势反包 / 仙人指路确认）；"
             f"退出规则：MA5减半/MA10清仓/止盈15%走40%/止损-5%/时间止损5日（日线近似，不含10点纪律）")
    L.append("")
    L.append("## 组合表现")
    L.append("")
    L.append(f"- 期末资金：**{result['final_equity']:,.2f}**（总收益 **{result['total_return_pct']:.2f}%**，"
             f"年化 **{result['annual_pct']:.2f}%**）")
    L.append(f"- 最大回撤：**{result['max_drawdown_pct']:.2f}%**")
    L.append(f"- 交易笔数：{result['n_trades']}，胜率 **{result['win_rate_pct']:.1f}%**，"
             f"平均盈亏 {result['avg_pnl_pct']:.2f}%")
    L.append(f"- 退出原因分布：{result['exit_reasons']}")
    L.append("")
    L.append("## 交易明细")
    L.append("")
    L.append("| # | 代码 | 入场 | 出场 | 持有天 | 盈亏% | 退出原因 |")
    L.append("|---|---|---|---|---|---|---|")
    for i, t in enumerate(result["trades"], 1):
        L.append(f"| {i} | {t['symbol']} {names.get(t['symbol'], '')} | {t['entry_date']} "
                 f"| {t['exit_date'] or '-'} | {t['days_held']} | {t['pnl_pct']:.2f} | {t['exit_reason']} |")
    L.append("")
    L.append("## 周度统计（按出场周）")
    L.append("")
    trades_df = pd.DataFrame(result["trades"])
    if not trades_df.empty:
        trades_df["week"] = pd.to_datetime(trades_df["exit_date"]).dt.to_period("W").astype(str)
        g = trades_df.groupby("week").agg(
            n=("pnl_pct", "count"), avg=("pnl_pct", "mean"),
            win=("pnl_pct", lambda x: (x > 0).mean() * 100))
        L.append("| 周 | 笔数 | 平均盈亏% | 胜率% |")
        L.append("|---|---|---|---|")
        for week, r in g.iterrows():
            L.append(f"| {week} | {int(r['n'])} | {r['avg']:.2f} | {r['win']:.0f} |")
    L.append("")
    L.append("> 说明：日线近似无法建模「10点前不板则走」等日内纪律（待分钟线数据，H6）；"
             "回放窗口较短，结论仅作执行层验证。")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")

    # 落库到 journal.db（可选）
    if not args.no_journal and result["trades"]:
        jdb = ROOT / "data" / "journal.db"
        conn = sqlite3.connect(jdb)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, entry_date TEXT, entry_price REAL, shares INTEGER,
                exit_date TEXT, exit_price REAL, pnl_pct REAL, exit_reason TEXT,
                days_held INTEGER, batch TEXT
            );
        """)
        batch = f"{args.start}_{args.end}"
        conn.execute("DELETE FROM paper_trades WHERE batch=?", (batch,))  # 覆盖同批次旧结果
        for t in result["trades"]:
            conn.execute(
                "INSERT INTO paper_trades(symbol,entry_date,entry_price,shares,exit_date,exit_price,"
                "pnl_pct,exit_reason,days_held,batch) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (t["symbol"], t["entry_date"], t["entry_price"], t["shares"],
                 t["exit_date"], t["exit_price"], t["pnl_pct"], t["exit_reason"],
                 t["days_held"], batch))
        conn.commit()
        conn.close()
        print(f"paper trades -> journal.db ({len(result['trades'])} 笔, batch={batch})")

    print("\n".join(L))
    print(f"\nREPORT -> {OUT}")
    store.close()


if __name__ == "__main__":
    main()
