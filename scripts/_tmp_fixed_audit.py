"""修复后审计：5+ 连板可成交口径（2025/2026）"""
import pandas as pd, numpy as np
from math import sqrt
from pathlib import Path
OUT = Path(__file__).resolve().parent.parent / "outputs"
L = ["# 修复后 5+ 连板审计（2025/2026，LOW+真实成交口径）", ""]
L.append("| 年 | n | 均值% | t | 胜率% | 95%CI | 聚类t |")
L.append("|---|---|---|---|---|---|---|")
for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]]
    n = len(g)
    m = g["pnl_nt"].mean()
    sd = g["pnl_nt"].std()
    t = m / (sd / sqrt(n)) if n > 1 else 0
    w = (g["pnl_nt"] > 0).mean() * 100
    wse = sqrt(w/100*(1-w/100)/n)*100
    sm = g.groupby("sym")["pnl_nt"].mean()
    t_cl = (sm.mean()/(sm.std()/sqrt(len(sm)))) if len(sm)>1 and sm.std()>0 else float("nan")
    L.append(f"| {y} | {n} | {m:+.3f}% | {t:.2f} | {w:.1f} | [{w-1.96*wse:.1f},{w+1.96*wse:.1f}] | {t_cl:.2f} |")
open(OUT / "daban_audit_fixed_2025_2026.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
