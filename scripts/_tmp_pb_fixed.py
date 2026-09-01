import pandas as pd, pathlib
from math import sqrt
L = ["# 修复后回踩池四年（A 开盘执行）", ""]
L.append("| 年 | n | 均值% | t | 胜率% | 修复前均值% |")
L.append("|---|---|---|---|---|---|")
before = {2023: 0.252, 2024: -0.379, 2025: 0.662, 2026: 0.491}
for y in (2023, 2024, 2025, 2026):
    p = pathlib.Path(f"outputs/pullback_b2_{y}_raw.csv")
    if not p.exists():
        continue
    df = pd.read_csv(p, dtype={"sym": str})
    d = df.dropna(subset=["p_open"])
    n = len(d)
    m = d["p_open"].mean()
    sd = d["p_open"].std()
    t = m/(sd/sqrt(n)) if n>1 and sd>0 else 0
    w = (d["p_open"]>0).mean()*100
    L.append(f"| {y} | {n} | {m:+.3f}% | {t:.2f} | {w:.1f} | {before[y]:+.3f}% |")
open("outputs/p0a_pullback_fixed_4y.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
