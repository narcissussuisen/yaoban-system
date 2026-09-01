"""合并口径胜率：验收标准解读
用户标准：两年全市场样本外均正期望且胜率>=50%。
- 口径A：每年独立胜率>=50%（严格）
- 口径B：两年合并胜率>=50%（合计样本）
- 顺带算三年合并
"""
import pandas as pd
from pathlib import Path
OUT = Path(__file__).resolve().parent.parent / "outputs"
rows = []
for y in (2023, 2024, 2025, 2026):
    p = OUT / f"backtest_daban_{y}_raw.csv"
    if not p.exists():
        print(y, "missing")
        continue
    df = pd.read_csv(p, dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]]
    rows.append((y, g))
    print(f"{y}: n={len(g)} 均值{g['pnl_nt'].mean():+.3f}% 胜率{(g['pnl_nt']>0).mean()*100:.1f}%")
print()
for years, name in (((2025, 2026), "2025+2026"), ((2023, 2024, 2025, 2026), "2023-2026")):
    allg = pd.concat([g for y, g in rows if y in years])
    print(f"{name}: n={len(allg)} 均值{allg['pnl_nt'].mean():+.3f}% 胜率{(allg['pnl_nt']>0).mean()*100:.1f}%")
