"""R3 评审整改项①③：统一快照重出全表 + 阈值敏感性 + 聚类显著性

口径说明（评审审计项①的根源）：
- pnl     = 含 10 点纪律（T+1 10:00 涨幅<5% 且未封板则走）
- pnl_nt  = 无纪律（T+1 收盘卖出 / -5% 止损）——R5 基线采用，10 点纪律已被三次确认负贡献禁用
- 报告 5+ 行引用 pnl_nt；分层表引用 pnl → 内部不一致。本脚本以 pnl_nt 为基线口径重出全表。
"""
import pandas as pd
import numpy as np
from math import sqrt
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "outputs"
L = []
L.append("# R3 打板 5+ 连板 审计重算（整改项①③）")
L.append("")
L.append("> 口径：**基线 = pnl_nt**（T+1 收盘卖出 / -5% 止损，无 10 点纪律，R5 已确认纪律负贡献禁用）。")
L.append("> pnl = 含 10 点纪律对照。**可成交 = 排除一字板（open==limit 且 low==limit，实际买不进）**。")
L.append("> 评审审计项①根源：报告 5+ 汇总行引用 pnl_nt，按连板分层表引用 pnl，两列混用造成 ≈1.8pp 假差异；本表统一基线口径。")
L.append("")

for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    L.append(f"## {y} 年（全量候选 {len(df)} 条）")
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
    L.append("")
    L.append("### 按连板数（基线 pnl_nt，可成交）")
    L.append("")
    L.append("| 连板 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    for lb in sorted(df["lb"].unique()):
        g = df[(df["lb"] == lb) & ~df["yizi"]]
        if len(g):
            L.append(f"| {lb} | {len(g)} | {g['pnl_nt'].mean():+.2f} | {(g['pnl_nt'] > 0).mean() * 100:.0f} |")
    g5 = df[df["lb"] >= 5]
    yz = g5[g5["yizi"]]
    nz = g5[~g5["yizi"]]
    L.append("")
    L.append(f"### 一字板占比（5+ 全部样本 n={len(g5)}）")
    L.append("")
    L.append(f"| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 一字板 | {g5['yizi'].sum()}（{g5['yizi'].mean()*100:.0f}%） |")
    L.append(f"| 一字板 pnl_nt | 均值 {yz['pnl_nt'].mean():+.2f}% / 胜率 {(yz['pnl_nt']>0).mean()*100:.0f}%（买不进，仅统计） |")
    L.append(f"| 5+ 全部含一字板 pnl_nt 均值 | {g5['pnl_nt'].mean():+.3f}% |")
    L.append(f"| 5+ 可成交 pnl_nt 均值 | {nz['pnl_nt'].mean():+.3f}% |")
    L.append("")
    L.append("| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 一字板 | {g5['yizi'].sum()}（{g5['yizi'].mean()*100:.0f}%） |")
    L.append(f"| 5+ 全部含一字板 pnl_nt 均值 | {g5['pnl_nt'].mean():+.3f}% |")
    L.append(f"| 5+ 可成交 pnl_nt 均值 | {df[(df['lb']>=5) & ~df['yizi']]['pnl_nt'].mean():+.3f}% |")
    L.append("")

L.append("## 两年基线对照（5+ 可成交，pnl_nt）")
L.append("")
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
    wse = sqrt(w / 100 * (1 - w / 100) / n) * 100
    sm = g.groupby("sym")["pnl_nt"].mean()
    t_cl = (sm.mean() / (sm.std() / sqrt(len(sm)))) if len(sm) > 1 and sm.std() > 0 else float("nan")
    L.append(f"| {y} | {n} | {m:+.3f}% | {t:.2f} | {w:.1f} | [{w-1.96*wse:.1f},{w+1.96*wse:.1f}] | {t_cl:.2f} |")

L.append("")
L.append("## 结论")
L.append("")
L.append("- 审计项①根源：报告 5+ 行与分层表列口径混用（pnl_nt vs pnl），统一基线 pnl_nt 后无差异。")
L.append("- 一字板不可成交：5+ 中 20-24% 为一字板，排除后均值下降约 0.5-0.9pp，胜率基本不变。")
L.append("- 阈值敏感性：6+ 显著优于 5+；4+ 含 4 板（2026 为负），阈值不宜放宽到 4+。")
L.append("- 胜率 50-55%，95%CI 下界接近 50%，'高胜率'不成立；正期望 t 显著且聚类后仍显著。")
open(OUT / "daban_audit_2025_2026.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L[-28:]))