"""修复后口径：P0-D 早封特征 + 连板分层重验"""
import pandas as pd, numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore
from core.sell import limit_pct_of

OUT = Path(__file__).resolve().parent.parent / "outputs"
L = ["# 修复后重验：P0-D 早封特征 + 连板分层（2025/2026）", ""]

def first_seal(qfq, sym, t):
    daily = qfq.get_stock(sym)
    if not daily: return None
    dates = [x[1] for x in daily]
    if t not in dates: return None
    i = dates.index(t)
    if i < 1: return None
    prev_c = float(daily[i-1][5])
    lpx = round(prev_c * (1 + limit_pct_of(sym)), 2)
    mrows = qfq.get_minute(sym, start=t, end=t)
    if not mrows: return None
    for r in mrows:
        if float(r[4]) >= lpx - 0.01:
            return r[2][11:16]
    return None

for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    qfq = QFQStore(str(y))
    # 连板分层（修复后 pnl_nt，可成交）
    L.append(f"## {y} 连板分层（可成交，修复后 pnl_nt）")
    L.append("")
    L.append("| 连板 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    for lb in sorted(df["lb"].unique()):
        g = df[(df["lb"]==lb) & ~df["yizi"]]
        if len(g) >= 5:
            L.append(f"| {lb} | {len(g)} | {g['pnl_nt'].mean():+.2f} | {(g['pnl_nt']>0).mean()*100:.0f} |")
    # 4板早封
    g4 = df[(df["lb"]==4) & ~df["yizi"]].copy()
    if len(g4):
        g4["fs"] = g4.apply(lambda r: first_seal(qfq, r["sym"], r["t"]), axis=1)
        g4["early"] = g4["fs"].apply(lambda x: "早封(≤10:30)" if x and x <= "10:30" else ("中封" if x and x <= "13:30" else "尾盘"))
        L.append("")
        L.append(f"### 4 板首封（修复后，n={len(g4)}）")
        L.append("")
        L.append("| 首封 | n | 均值% | 胜率% |")
        L.append("|---|---|---|---|")
        for k, gg in g4.groupby("early"):
            L.append(f"| {k} | {len(gg)} | {gg['pnl_nt'].mean():+.2f} | {(gg['pnl_nt']>0).mean()*100:.0f} |")
    L.append("")

open(OUT / "promo4_fixed_recheck.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
