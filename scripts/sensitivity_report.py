"""P3 敏感性报告：5+ 打板滑点敏感性 + 卖出变体

基线：pnl_nt（T+1 收盘/-5% 止损），可成交（排除一字板）
滑点敏感性：0.1%（基线）→ 0.2%/0.3% 单边
卖出变体：-5% 止损 → -3%/-8%/无止损
"""
import pandas as pd
import numpy as np
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "outputs"
COMM, STAMP, SLIP_BASE = 0.00025, 0.0005, 0.001

def recompute(df, slip, stop_pct):
    """按 raw 的 t1 日线重算：entry=开盘，exit=stop 或收盘"""
    rows = []
    for _, r in df.iterrows():
        rows.append(r)
    out = []
    for _, r in df.iterrows():
        # raw 已含 pnl_nt（0.1% 滑点 + 5% 止损）。近似重算：从原始收益反推毛收益再按新成本
        # pnl_nt = sell_net(exit)/buy_net(entry) - 1；反推 exit/entry 比值
        buy_f = 1 + COMM + SLIP_BASE
        sell_f = 1 - COMM - SLIP_BASE - STAMP
        ratio = (1 + r["pnl_nt"] / 100) * buy_f / sell_f  # exit/entry 毛比
        # 新成本下：
        buy_f2 = 1 + COMM + slip
        sell_f2 = 1 - COMM - slip - STAMP
        out.append((ratio * sell_f2 / buy_f2 - 1) * 100)
    return np.array(out)

L = ["# P3 敏感性报告：5+ 打板（滑点 + 止损变体）", ""]
L.append("> 口径：可成交（排除一字板），四年。滑点基线 0.1% 单边。")
L.append("")
L.append("## 一、滑点敏感性（均值%/胜率）")
L.append("")
L.append("| 年 | n | 滑点0.1%（基线） | 滑点0.2% | 滑点0.3% |")
L.append("|---|---|---|---|---|")
for y in (2023, 2024, 2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]]
    n = len(g)
    base = g["pnl_nt"].mean()
    s2 = recompute(g, 0.002, 0.05).mean()
    s3 = recompute(g, 0.003, 0.05).mean()
    L.append(f"| {y} | {n} | {base:+.2f}% | {s2:+.2f}% | {s3:+.2f}% |")
L.append("")
L.append("## 二、止损变体（均值%：-3% / -5%基线 / -8% / 无止损）")
L.append("")
L.append("| 年 | -3% 止损 | -5% 止损（基线） | -8% 止损 | 无止损 |")
L.append("|---|---|---|---|---|")
for y in (2023, 2024, 2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]].copy()
    vals = []
    for stop in (0.03, 0.05, 0.08, None):
        # 用 t1 日线重算（raw 有 t1 但无日线数据——用 raw 的 pnl 结构近似）
        # 简化：raw 的 pnl_nt 已按 5% 止损。对止损变体，用 reason/close 近似不可行，
        # 需要日线。这里用 pnl_nt（5%）与 pnl（10点纪律）做上下界近似，标注局限。
        vals.append("见说明")
    L.append(f"| {y} | {vals[0]} | 见下 | {vals[2]} | {vals[3]} |")
L.append("")
L.append("> 止损变体需日线重算，本报告滑点部分先行；止损部分待日线重算脚本（注明局限）。")
open(OUT / "sensitivity_report.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L[:20]))
