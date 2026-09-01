"""扩展审计：2023/2024 年 5+ 板可成交口径（与 daban_audit 对齐）"""
import pandas as pd
import numpy as np
from math import sqrt
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "outputs"
L = []
L.append("# R3 打板 2023/2024 复验（整改项⑥，审计口径）")
L.append("")
L.append("> 口径与 daban_audit_2025_2026.md 一致：基线 pnl_nt、可成交=排除一字板。")
L.append("> 注意：2023-2024 目录退市股覆盖不完整（2023 退市股抽查 8/10 缺失），结果需打折解读。")
L.append("")
for y in (2023, 2024):
    p = OUT / f"backtest_daban_{y}_raw.csv"
    if not p.exists():
        L.append(f"## {y}：raw 缺失（回测未完成）")
        L.append("")
        continue
    df = pd.read_csv(p, dtype={"sym": str})
    L.append(f"## {y}（全量候选 {len(df)}）")
    L.append("")
    L.append("| 口径 | 阈值 | 可成交n | 均值% | t值 | 胜率% | 95%CI | 聚类t | 股票数 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for col, label in (("pnl_nt", "基线(无纪律)"), ("pnl", "含10点纪律")):
        for thr, tn in ((4, "4+"), (5, "5+"), (6, "6+")):
            g = df[(df["lb"] >= thr) & ~df["yizi"]]
            n = len(g)
            if n == 0:
                continue
            m = g[col].mean()
            sd = g[col].std()
            se = sd / sqrt(n)
            t = m / se if se else 0
            w = (g[col] > 0).mean() * 100
            wse = sqrt(w / 100 * (1 - w / 100) / n) * 100
            sm = g.groupby("sym")[col].mean()
            t_cl = (sm.mean() / (sm.std() / sqrt(len(sm)))) if len(sm) > 1 and sm.std() > 0 else float("nan")
            L.append(f"| {label} | {tn}板 | {n} | {m:+.3f}% | {t:.2f} | {w:.1f} | [{w-1.96*wse:.1f},{w+1.96*wse:.1f}] | {t_cl:.2f} | {len(sm)} |")
    g5 = df[df["lb"] >= 5]
    yz = g5[g5["yizi"]]
    nz = g5[~g5["yizi"]]
    L.append("")
    L.append(f"### 一字板占比（5+ 全部 n={len(g5)}）")
    L.append("")
    L.append(f"| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 一字板 | {g5['yizi'].sum()}（{g5['yizi'].mean()*100:.0f}%） |")
    if len(yz):
        L.append(f"| 一字板 pnl_nt | 均值 {yz['pnl_nt'].mean():+.2f}% / 胜率 {(yz['pnl_nt']>0).mean()*100:.0f}% |")
    L.append(f"| 5+ 全部 pnl_nt | {g5['pnl_nt'].mean():+.3f}% |")
    L.append(f"| 5+ 可成交 pnl_nt | {nz['pnl_nt'].mean():+.3f}% |")
    L.append("")

open(OUT / "daban_audit_2023_2024.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L[:14]))
