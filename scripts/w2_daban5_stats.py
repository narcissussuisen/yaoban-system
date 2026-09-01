"""W2+5+ 统计确认：聚类 t + 分月 + 两年合并 gate 检验"""
import pandas as pd, numpy as np
from pathlib import Path
from math import sqrt
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

L = ["# W2+5+ 统计确认（2026-08-26）", ""]
L.append("| 年 | n保留 | 均值% | t | 胜率% | 95%CI | 聚类t | 剔除n | 剔除均值% |")
L.append("|---|---|---|---|---|---|---|---|---|")
all_keep = []
for y in (2023, 2024, 2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]].dropna(subset=["pnl_nt"]).copy()
    qfq = QFQStore(str(y))
    rows = []
    for _, r in g.iterrows():
        w = w2_weak(qfq, r["sym"], r["t1"])
        if w is not None:
            rows.append({"sym": r["sym"], "pnl": r["pnl_nt"], "weak": w, "t1": r["t1"]})
    d = pd.DataFrame(rows)
    keep = d[~d["weak"]]
    drop = d[d["weak"]]
    n1 = len(keep)
    m1 = keep["pnl"].mean()
    sd1 = keep["pnl"].std()
    t1 = m1/(sd1/sqrt(n1)) if n1 > 1 and sd1 > 0 else 0
    w1 = (keep["pnl"]>0).mean()*100
    wse = sqrt(w1/100*(1-w1/100)/n1)*100 if n1 > 1 else 0
    sm = keep.groupby("sym")["pnl"].mean()
    t_cl = (sm.mean()/(sm.std()/sqrt(len(sm)))) if len(sm)>1 and sm.std()>0 else float("nan")
    L.append(f"| {y} | {n1} | {m1:+.2f} | {t1:.2f} | {w1:.0f} | [{w1-1.96*wse:.1f},{w1+1.96*wse:.1f}] | {t_cl:.2f} | {len(drop)} | {drop['pnl'].mean():+.2f} |")
    keep["year"] = y
    all_keep.append(keep[["sym","pnl","t1","year"]])
# 两年合并（验收年份 2025+2026）
g = pd.concat(all_keep)
for ys, name in (((2025, 2026), "2025+2026"), ((2023, 2024, 2025, 2026), "2023-2026")):
    gg = g[g["year"].isin(ys)]
    n = len(gg); m = gg["pnl"].mean(); sd = gg["pnl"].std()
    t = m/(sd/sqrt(n)) if n > 1 and sd > 0 else 0
    w = (gg["pnl"]>0).mean()*100
    sm = gg.groupby("sym")["pnl"].mean()
    t_cl = (sm.mean()/(sm.std()/sqrt(len(sm)))) if len(sm)>1 and sm.std()>0 else float("nan")
    L.append(f"| {name}合并 | {n} | {m:+.2f} | {t:.2f} | {w:.0f} | — | {t_cl:.2f} | — | — |")
# 分月
L.append("")
L.append("## 分月（保留组 n≥5）")
L.append("")
L.append("| 月 | n | 均值% | 胜率% |")
L.append("|---|---|---|---|")
g["month"] = g["t1"].str[:7]
pos = tot = 0
for m, gg in g.groupby("month"):
    if len(gg) >= 5:
        tot += 1
        if gg["pnl"].mean() > 0: pos += 1
        L.append(f"| {m} | {len(gg)} | {gg['pnl'].mean():+.2f} | {(gg['pnl']>0).mean()*100:.0f} |")
L.append(f"")
L.append(f"正期望月份：{pos}/{tot}")
open(OUT / "w2_daban5_stats.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
