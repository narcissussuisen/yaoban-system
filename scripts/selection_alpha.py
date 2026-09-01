"""P0-B 同日择股系统化：5+ 池内排序规则 vs 随机

场景：组合模拟在 5+ 连板同日多信号时随机取 1（稀释期望）。
规则化候选（T 日收盘可知）：连板数降序（lb）、一字板剔除、T 日炸板（zb_t False 优先）。
2025/2026 各自独立验证：规则化选取 vs 随机基线（多 seed 平均）。
"""
import pandas as pd
import numpy as np
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "outputs"
L = []
L.append("# P0-B 同日择股系统化（5+ 池内排序 vs 随机）")
L.append("")

for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g5 = df[(df["lb"] >= 5) & ~df["yizi"]].dropna(subset=["pnl_nt"]).copy()
    # 同日多信号场景
    same = g5.groupby("t1").size()
    multi = same[same > 1]
    L.append(f"## {y}（5+ 可成交 n={len(g5)}，同日多信号 {len(multi)} 天）")
    L.append("")
    if len(multi) == 0:
        L.append("无同日多信号——规则化无对象（随机与规则等价）。")
        L.append("")
        continue
    # 规则：连板数降序 → 取 1（优先最高连板）
    g5["rnd"] = np.random.default_rng(42).random(len(g5))
    g5s = g5.sort_values(["t1", "lb", "rnd"], ascending=[True, False, True])
    picks_rule = g5s.groupby("t1").head(1)
    # 随机基线：多 seed 平均
    rnd_pnls = []
    for seed in range(20):
        g5r = g5.copy()
        g5r["rnd"] = np.random.default_rng(seed).random(len(g5r))
        g5r = g5r.sort_values(["t1", "rnd"], ascending=[True, True])
        picks_r = g5r.groupby("t1").head(1)
        rnd_pnls.append(picks_r["pnl_nt"].mean())
    # 只在 multi 天对比（规则 vs 随机在那些天的选择）
    multi_days = set(multi.index)
    rule_multi = picks_rule[picks_rule["t1"].isin(multi_days)]
    rnd_multi = []
    for seed in range(20):
        g5r = g5.copy()
        g5r["rnd"] = np.random.default_rng(seed).random(len(g5r))
        g5r = g5r.sort_values(["t1", "rnd"], ascending=[True, True])
        picks_r = g5r.groupby("t1").head(1)
        rnd_multi.append(picks_r[picks_r["t1"].isin(multi_days)]["pnl_nt"].mean())
    L.append("| 口径 | 全部天均值% | 多信号天均值%（n=" + str(len(multi_days)) + "天） |")
    L.append("|---|---|---|")
    L.append(f"| 规则（连板降序） | {picks_rule['pnl_nt'].mean():+.2f} | {rule_multi['pnl_nt'].mean():+.2f} |")
    rm = np.mean(rnd_multi)
    L.append(f"| 随机（20-seed平均） | {np.mean(rnd_pnls):+.2f} | {rm:+.2f} |")
    L.append("")
    # 规则与随机在多信号天的逐日对比
    L.append("### 多信号日明细（规则选择）")
    L.append("")
    L.append("| t1 | 规则选 sym/lb | pnl_nt% | 候选 |")
    L.append("|---|---|---|---|")
    for d, g in g5[g5["t1"].isin(multi_days)].groupby("t1"):
        g = g.sort_values("lb", ascending=False)
        sel = g.iloc[0]
        cands = "; ".join(f"{r['sym']}({int(r['lb'])}板,{r['pnl_nt']:+.1f}%)" for _, r in g.iterrows())
        L.append(f"| {d} | {sel['sym']}/{int(sel['lb'])}板 | {sel['pnl_nt']:+.2f} | {cands} |")
    L.append("")

open(OUT / "selection_alpha.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L[:10]))