import pandas as pd, pathlib
from math import sqrt
L = []
L.append("# P0-A 动量池四年复验（档1 开盘执行，无纪律——定稿口径）")
L.append("")
L.append("| 年 | n | 均值% | t | 胜率% | 备注 |")
L.append("|---|---|---|---|---|---|")
for y in (2023, 2024, 2025, 2026):
    p = pathlib.Path(f"outputs/backtest_market_{y}_t1raw.csv")
    if not p.exists():
        L.append(f"| {y} | — | — | — | — | 缺失 |")
        continue
    df = pd.read_csv(p, dtype={"sym": str})
    n = len(df)
    m = df["pnl"].mean()
    sd = df["pnl"].std()
    t = m / (sd / sqrt(n)) if n > 1 and sd > 0 else 0
    w = (df["pnl"] > 0).mean() * 100
    note = "含炸板率≤35过滤" if y in (2025, 2026) else "无情绪过滤(缺sentiment)"
    L.append(f"| {y} | {n} | {m:+.3f}% | {t:.2f} | {w:.1f} | {note} |")
open("outputs/p0a_momentum_4y.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
