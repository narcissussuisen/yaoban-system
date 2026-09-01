"""R3 整改项⑥b：regime 分桶（炸板率/涨停家数对 5+ 打板期望的影响）

用 raw CSV 里的 zhaban_rate / zt 列分桶（信号日 T 的情绪状态）
"""
import pandas as pd
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "outputs"
L = []
L.append("# R3 regime 分桶（整改项⑥b）")
L.append("")
L.append("> 分桶依据：信号日 T 的炸板率 / 涨停家数。基线口径 pnl_nt、5+ 可成交。")
L.append("")

for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]].copy()
    L.append(f"## {y}（5+ 可成交 n={len(g)}）")
    L.append("")
    L.append("### 按炸板率分桶")
    L.append("")
    L.append("| 炸板率 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    for lo, hi, name in ((0, 20, "<20"), (20, 35, "20-35"), (35, 101, ">35")):
        gg = g[(g["zhaban_rate"] >= lo) & (g["zhaban_rate"] < hi)]
        if len(gg):
            L.append(f"| {name} | {len(gg)} | {gg['pnl_nt'].mean():+.2f} | {(gg['pnl_nt']>0).mean()*100:.0f} |")
    L.append("")
    L.append("### 按涨停家数分桶")
    L.append("")
    L.append("| 涨停家数 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    for lo, hi, name in ((0, 40, "<40"), (40, 80, "40-80"), (80, 10**9, ">80")):
        gg = g[(g["zt"] >= lo) & (g["zt"] < hi)]
        if len(gg):
            L.append(f"| {name} | {len(gg)} | {gg['pnl_nt'].mean():+.2f} | {(gg['pnl_nt']>0).mean()*100:.0f} |")
    L.append("")
    L.append("### 冰点期空仓开关测试（炸板率>35 或 涨停家数<25 时空仓）")
    L.append("")
    for name, mask in (
        ("炸板率>35 空仓", g["zhaban_rate"] > 35),
        ("涨停家数<25 空仓", g["zt"] < 25),
        ("炸板率>35 或 涨停家数<25 空仓", (g["zhaban_rate"] > 35) | (g["zt"] < 25)),
    ):
        keep = g[~mask]
        L.append(f"| {name} | 保留 n={len(keep)} 均值 {keep['pnl_nt'].mean():+.2f}% 胜率 {(keep['pnl_nt']>0).mean()*100:.0f}% | 剔除 n={mask.sum()} |")
    L.append("")

open(OUT / "daban_regime_bucket.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L[:12]))
