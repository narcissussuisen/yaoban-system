import pandas as pd, pathlib
from math import sqrt
L = []
L.append("# P0-A 回踩池四年复验（开盘执行 A）")
L.append("")
L.append("| 年 | n | 均值% | t | 胜率% |")
L.append("|---|---|---|---|---|")
for y in (2023, 2024, 2025, 2026):
    p = pathlib.Path(f"outputs/pullback_b2_{y}_raw.csv")
    if not p.exists():
        L.append(f"| {y} | — | — | — | — |")
        continue
    df = pd.read_csv(p, dtype={"sym": str})
    # raw 列：p_open 是次日开盘收益
    d = df.dropna(subset=["p_open"])
    n = len(d)
    m = d["p_open"].mean()
    sd = d["p_open"].std()
    t = m / (sd / sqrt(n)) if n > 1 and sd > 0 else 0
    w = (d["p_open"] > 0).mean() * 100
    L.append(f"| {y} | {n} | {m:+.3f}% | {t:.2f} | {w:.1f} |")
open("outputs/p0a_pullback_4y.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
