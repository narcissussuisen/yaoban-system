"""W2 扩展验证：5+ 打板池（修复后口径）

5+ 连板（可成交）样本：T+1 开盘买入，若开盘30分钟跌破前日低点 → 放弃
对比：全量 vs W2保留 vs W2剔除
"""
import pandas as pd, numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"

def w2_weak(qfq, sym, ed):
    daily = qfq.get_stock(sym)
    if not daily: return None
    dates = [x[1] for x in daily]
    if ed not in dates: return None
    i = dates.index(ed)
    if i < 1: return None
    prev_low = float(daily[i-1][3])
    mrows = qfq.get_minute(sym, start=ed, end=ed)
    if not mrows: return None
    day = pd.DataFrame(mrows, columns=["symbol","freq","ts","open","high","low","close","volume","amount"])
    return float(day["low"].iloc[:30].min()) < prev_low

L = ["# W2 扩展：5+ 打板池（修复后）", ""]
L.append("| 年 | 口径 | n | 均值% | 胜率% | t |")
L.append("|---|---|---|---|---|---|")
for y in (2023, 2024, 2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]].dropna(subset=["pnl_nt"]).copy()
    qfq = QFQStore(str(y))
    rows = []
    for _, r in g.iterrows():
        w = w2_weak(qfq, r["sym"], r["t1"])
        if w is not None:
            rows.append({"pnl": r["pnl_nt"], "weak": w})
    d = pd.DataFrame(rows)
    if len(d) == 0:
        continue
    allv = d["pnl"]
    keep = d[~d["weak"]]["pnl"]
    drop = d[d["weak"]]["pnl"]
    n1 = len(keep)
    m1 = keep.mean()
    sd1 = keep.std()
    t1 = m1/(sd1/np.sqrt(n1)) if n1 > 1 and sd1 > 0 else 0
    w1 = (keep>0).mean()*100
    L.append(f"| {y} | 全量 | {len(allv)} | {allv.mean():+.2f} | {(allv>0).mean()*100:.0f} | — |")
    L.append(f"| {y} | W2保留 | {n1} | {m1:+.2f} | {w1:.0f} | {t1:.2f} |")
    L.append(f"| {y} | W2剔除 | {len(drop)} | {drop.mean():+.2f} | {(drop>0).mean()*100:.0f} | — |")
    L.append("")
open(OUT / "open_weak_daban5.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
