"""修复后四年统一审计（5+ 连板可成交，pnl_nt 修复版）"""
import pandas as pd, numpy as np
from math import sqrt
from pathlib import Path
OUT = Path(__file__).resolve().parent.parent / "outputs"
L = ["# 修复后 5+ 连板四年审计（LOW + 真实成交口径）", ""]
L.append("| 年 | n | 均值% | t | 胜率% | 95%CI | 聚类t | 修复前均值% |")
L.append("|---|---|---|---|---|---|---|---|")
before = {2023: 2.157, 2024: 0.636, 2025: 1.238, 2026: 3.470}
for y in (2023, 2024, 2025, 2026):
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
    L.append(f"| {y} | {n} | {m:+.3f}% | {t:.2f} | {w:.1f} | [{w-1.96*wse:.1f},{w+1.96*wse:.1f}] | {t_cl:.2f} | {before[y]:+.3f}% |")
# 合并
allg = []
for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    allg.append(df[(df["lb"] >= 5) & ~df["yizi"]])
g = pd.concat(allg)
n = len(g); m = g["pnl_nt"].mean(); sd = g["pnl_nt"].std()
t = m/(sd/sqrt(n)) if n>1 else 0
w = (g["pnl_nt"]>0).mean()*100
L.append(f"| 25+26合并 | {n} | {m:+.3f}% | {t:.2f} | {w:.1f} | — | — | — |")
open(OUT / "daban_audit_fixed_4y.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
