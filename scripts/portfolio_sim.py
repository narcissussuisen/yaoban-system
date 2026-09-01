"""R5 组合级模拟：单吊复利资金曲线（5+ 连板 + 动量池 + 回踩池 三模式）

规则：
  - 初始资金 5 万（选手口径），单吊（每日最多 1 只持仓），T+1 收盘卖出（各模式 hold=1）
  - 同日多信号优先级：5+ 连板 > 动量池 > 回踩池；同模式多信号按 pnl 期望降序取 1 只（seed 固定）
  - 一字板（买不进）跳过
输出: outputs/portfolio_sim_{year}.md（资金曲线/年化/回撤/月度/胜率/盈亏比）
"""
from __future__ import annotations

import pathlib
import sys

import pandas as pd
import numpy as np

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"


def main():
    year = sys.argv[1] if len(sys.argv) > 1 else "2026"
    capital0 = 50000.0
    seed = 42
    for a in sys.argv[2:]:
        if a.isdigit():
            seed = int(a)
    double_mode = "--double" in sys.argv
    rng = np.random.default_rng(seed)

    # 交易流
    flows = []
    db = pd.read_csv(OUT_DIR / f"backtest_daban_{year}_raw.csv", dtype={"sym": str})
    g5 = db[(db["lb"] >= 5) & (~db["yizi"])].dropna(subset=["pnl_nt"]).copy()
    g5["src"] = "5+连板"
    g5["entry"] = g5["t1"]
    g5["pnl"] = g5["pnl_nt"]
    flows.append(g5[["sym", "entry", "pnl", "src"]])
    mf = OUT_DIR / f"backtest_market_{year}_t3raw.csv"
    if mf.exists():
        m = pd.read_csv(mf, dtype={"sym": str})
        m["src"] = "动量池"
        m["entry"] = m["ed"]
        flows.append(m[["sym", "entry", "pnl", "src"]])
    pf = pd.read_csv(OUT_DIR / f"pullback_b2_{year}_raw.csv", dtype={"sym": str})
    p = pf.dropna(subset=["p_open"]).copy()
    p["src"] = "回踩池"
    p["entry"] = p["ed"]
    p["pnl"] = p["p_open"]
    flows.append(p[["sym", "entry", "pnl", "src"]])

    allf = pd.concat(flows, ignore_index=True)
    allf = allf[allf["pnl"].notna() & (allf["pnl"] > -99)]
    # 优先级：5+连板 0 > 动量池 1 > 回踩池 2
    prio = {"5+连板": 0, "动量池": 1, "回踩池": 2}
    allf["prio"] = allf["src"].map(prio)
    # 同日同模式多信号：随机取 1（不可用未来 pnl 排序——前视偏差；随机代表无择股能力）
    allf["rnd"] = rng.random(len(allf))
    allf = allf.sort_values(["entry", "prio", "rnd"], ascending=[True, True, True])
    picks = allf.groupby("entry").head(1).reset_index(drop=True)
    picks = picks.sort_values("entry")

    # 组合复利（单吊=每日1只全仓；双票=每日最多2只各50%）
    capital = capital0
    curve = []
    trades = []
    if double_mode:
        picks2 = allf.sort_values(["entry", "prio", "rnd"], ascending=[True, True, True])
        picks2 = picks2.groupby("entry").head(2).reset_index(drop=True).sort_values("entry")
        for day, g in picks2.groupby("entry"):
            if len(g) == 1:
                capital *= (1 + float(g["pnl"].iloc[0]) / 100.0)
                trades.append({"entry": day, "sym": g["sym"].iloc[0], "src": g["src"].iloc[0],
                               "pnl": float(g["pnl"].iloc[0]), "cap": capital})
            else:
                r1 = float(g["pnl"].iloc[0]) / 100.0
                r2 = float(g["pnl"].iloc[1]) / 100.0
                capital = capital * (0.5 * (1 + r1) + 0.5 * (1 + r2))
                trades.append({"entry": day, "sym": g["sym"].iloc[0], "src": g["src"].iloc[0],
                               "pnl": float(g["pnl"].iloc[0]), "cap": capital})
                trades.append({"entry": day, "sym": g["sym"].iloc[1], "src": g["src"].iloc[1],
                               "pnl": float(g["pnl"].iloc[1]), "cap": capital})
            curve.append({"date": day, "equity": capital})
    else:
        for _, r in picks.iterrows():
            capital *= (1 + r["pnl"] / 100.0)
            trades.append({"entry": r["entry"], "sym": r["sym"], "src": r["src"],
                           "pnl": r["pnl"], "cap": capital})
            curve.append({"date": r["entry"], "equity": capital})
    # 按日补齐曲线（非交易日月末标记）
    eq = pd.DataFrame(curve)
    trades_df = pd.DataFrame(trades)
    total_ret = (capital / capital0 - 1) * 100
    # 最大回撤（按交易序列）
    eq["peak"] = eq["equity"].cummax()
    eq["dd"] = (eq["equity"] / eq["peak"] - 1) * 100
    max_dd = eq["dd"].min()
    years = len(eq) / 244
    annual = ((capital / capital0) ** (1 / years) - 1) * 100 if years > 0 and capital > 0 else 0
    pnls = trades_df["pnl"]
    win = pnls[pnls > 0]
    loss = pnls[pnls < 0]
    pl = win.mean() / abs(loss.mean()) if len(loss) else float("nan")

    L = [f"# R5 组合级模拟 {year}（{'双票50%×2' if double_mode else '单吊全仓'}，初始 {capital0:.0f}）", "",
         f"> 三模式：5+连板（{len(g5)} 信号）优先 > 动量池（{len(m) if mf.exists() else 0}）> 回踩池（{len(p)}）；"
         f"同日多信号按优先级+期望取 1；一字板跳过。", "",
         "| 指标 | 值 |", "|---|---|"]
    L.append(f"| 交易笔数 | {len(trades_df)} |")
    L.append(f"| 期末资金 | {capital:,.0f} |")
    L.append(f"| 总收益 | {total_ret:+.1f}% |")
    L.append(f"| 年化 | {annual:+.1f}% |")
    L.append(f"| 最大回撤（按笔） | {max_dd:.1f}% |")
    L.append(f"| 胜率 | {(pnls > 0).mean() * 100:.0f}% |")
    L.append(f"| 盈亏比 | {pl:.2f} |")
    L.append("")
    L.append("## 按来源")
    L.append("")
    L.append("| 来源 | 笔数 | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    for s, g in trades_df.groupby("src"):
        L.append(f"| {s} | {len(g)} | {g['pnl'].mean():+.2f} | {(g['pnl'] > 0).mean() * 100:.0f} |")
    L.append("")
    L.append("## 按月（月末资金）")
    L.append("")
    L.append("| 月份 | 笔数 | 月末资金 | 月收益% |")
    L.append("|---|---|---|---|")
    eq["month"] = eq["date"].str[:7]
    prev_cap = capital0
    for m, g in eq.groupby("month"):
        L.append(f"| {m} | {int((trades_df['entry'].str[:7] == m).sum())} | {g['equity'].iloc[-1]:,.0f} "
                 f"| {(g['equity'].iloc[-1] / prev_cap - 1) * 100:+.1f} |")
        prev_cap = g["equity"].iloc[-1]
    L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    outp = OUT_DIR / f"portfolio_sim_{year}.md"
    outp.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {outp}")
    # 交易明细（抽查用）
    trades_df.to_csv(OUT_DIR / f"portfolio_trades_{year}.csv", index=False)


if __name__ == "__main__":
    main()