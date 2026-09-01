import pandas as pd, pathlib
from math import sqrt
L = ["# P0-A 整改④：动量池 t 值补充（无过滤口径）", ""]
L.append("| 年 | n | 均值% | t(iid) | 股票数 | 聚类t |")
L.append("|---|---|---|---|---|---|")
for y in (2023, 2024, 2025, 2026):
    p = pathlib.Path(f"outputs/backtest_market_{y}_t1raw.csv")
    if not p.exists():
        L.append(f"| {y} | — | — | — | — |")
        continue
    df = pd.read_csv(p, dtype={"sym": str})
    n = len(df)
    m = df["pnl"].mean()
    sd = df["pnl"].std()
    t = m / (sd / sqrt(n)) if n > 1 and sd > 0 else 0
    sm = df.groupby("sym")["pnl"].mean()
    t_cl = (sm.mean() / (sm.std() / sqrt(len(sm)))) if len(sm) > 1 and sm.std() > 0 else float("nan")
    L.append(f"| {y} | {n} | {m:+.3f}% | {t:.2f} | {len(sm)} | {t_cl:.2f} |")
open("outputs/p0a_momentum_tvalues.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
