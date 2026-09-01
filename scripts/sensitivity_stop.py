"""P3 补充：止损变体（日线重算）

对 5+ 可成交样本：entry=T+1 开盘，exit=T+1 日线（-stop% 止损 or 收盘）
止损 3%/5%/8%/无止损 变体，四年。
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001

def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)

L = ["# P3 补充：5+ 打板止损变体（日线重算）", ""]
L.append("| 年 | -3% | -5%（基线） | -8% | 无止损 |")
L.append("|---|---|---|---|---|")
for y in (2023, 2024, 2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]].copy()
    qfq = QFQStore(str(y))
    vals = {s: [] for s in (0.03, 0.05, 0.08, None)}
    for _, r in g.iterrows():
        sym, t1 = r["sym"], r["t1"]
        daily = qfq.get_stock(sym)
        if not daily:
            continue
        dates = [x[1] for x in daily]
        if t1 not in dates:
            continue
        i = dates.index(t1)
        if i >= len(daily) - 1:
            continue
        entry_px = float(daily[i][2])
        if entry_px <= 0:
            continue
        r1 = daily[i + 1]
        lo1, c1 = float(r1[3]), float(r1[5])
        for stop in (0.03, 0.05, 0.08, None):
            if stop is None:
                exit_px = c1
            else:
                sp = entry_px * (1 - stop)
                exit_px = sp if lo1 <= sp else c1
            vals[stop].append((sell_net(exit_px) / buy_net(entry_px) - 1) * 100)
    row = [f"| {y} |"]
    for stop in (0.03, 0.05, 0.08, None):
        a = np.array(vals[stop])
        row.append(f" {a.mean():+.2f}% |")
    L.append("".join(row))
L.append("")
L.append("> 说明：无止损=T+1 收盘卖出；止损=日内低点触及止损价则按止损价卖出，否则收盘。")
open(OUT / "sensitivity_stop.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
