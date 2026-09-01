import pandas as pd, pathlib
from math import sqrt
L = ["# 修复后动量池四年（档1 开盘执行，t1raw）", ""]
L.append("| 年 | n | 均值% | t | 胜率% | 聚类t | 修复前均值% |")
L.append("|---|---|---|---|---|---|---|")
before = {2023: 0.036, 2024: -0.139, 2025: 0.410, 2026: 1.257}
for y in (2023, 2024, 2025, 2026):
    p = pathlib.Path(f"outputs/backtest_market_{y}_t1raw.csv")
    if not p.exists():
        continue
    df = pd.read_csv(p, dtype={"sym": str})
    n = len(df)
    m = df["pnl"].mean()
    sd = df["pnl"].std()
    t = m/(sd/sqrt(n)) if n>1 and sd>0 else 0
    w = (df["pnl"]>0).mean()*100
    sm = df.groupby("sym")["pnl"].mean()
    t_cl = (sm.mean()/(sm.std()/sqrt(len(sm)))) if len(sm)>1 and sm.std()>0 else float("nan")
    L.append(f"| {y} | {n} | {m:+.3f}% | {t:.2f} | {w:.1f} | {t_cl:.2f} | {before[y]:+.3f}% |")
open("outputs/p0a_momentum_fixed_4y.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
