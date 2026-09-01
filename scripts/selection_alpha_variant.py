"""P0-B 变体：首封时间早作为同日择股规则（P0-D 发现早封<=10:30 方向一致）

5+ 池同日多信号 → 选首封最早的（T 日分钟数据提取首封时间）
对比随机基线（20-seed 平均）
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore
from core.sell import limit_pct_of

OUT = Path(__file__).resolve().parent.parent / "outputs"
L = []
L.append("# P0-B 变体：首封时间早择股（5+ 池内）")
L.append("")

def first_seal_of(qfq, sym, t):
    """T 日首封时间（分钟 high>=limit 的最早一根）"""
    daily = qfq.get_stock(sym)
    if not daily:
        return None
    dates = [x[1] for x in daily]
    if t not in dates:
        return None
    i = dates.index(t)
    if i < 1:
        return None
    prev_c = float(daily[i - 1][5])
    lpx = round(prev_c * (1 + limit_pct_of(sym)), 2)
    mrows = qfq.get_minute(sym, start=t, end=t)
    if not mrows:
        return None
    for r in mrows:
        if float(r[4]) >= lpx - 0.01:  # high >= limit
            return r[2][11:16]
    return None

for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g5 = df[(df["lb"] >= 5) & ~df["yizi"]].dropna(subset=["pnl_nt"]).copy()
    same = g5.groupby("t1").size()
    multi_days = set(same[same > 1].index)
    if not multi_days:
        L.append(f"## {y}：无同日多信号")
        L.append("")
        continue
    qfq = QFQStore(str(y))
    g5["first_seal"] = g5.apply(lambda r: first_seal_of(qfq, r["sym"], r["t"]), axis=1)
    # 规则：多信号日选首封最早的（缺首封的放最后）
    g5["seal_rank"] = g5["first_seal"].fillna("99:99")
    g5s = g5.sort_values(["t1", "seal_rank", "lb"], ascending=[True, True, False])
    picks_rule = g5s.groupby("t1").head(1)
    rule_multi = picks_rule[picks_rule["t1"].isin(multi_days)]
    # 随机基线
    rnd_multi = []
    for seed in range(20):
        g5r = g5.copy()
        g5r["rnd"] = np.random.default_rng(seed).random(len(g5r))
        g5r = g5r.sort_values(["t1", "rnd"], ascending=[True, True])
        picks_r = g5r.groupby("t1").head(1)
        rnd_multi.append(picks_r[picks_r["t1"].isin(multi_days)]["pnl_nt"].mean())
    L.append(f"## {y}（5+ 可成交 n={len(g5)}，多信号 {len(multi_days)} 天，首封可得 "
             f"{g5['first_seal'].notna().sum()}）")
    L.append("")
    L.append("| 口径 | 多信号天均值%（n=" + str(len(multi_days)) + "） |")
    L.append("|---|---|")
    L.append(f"| 规则（首封最早） | {rule_multi['pnl_nt'].mean():+.2f} |")
    L.append(f"| 随机（20-seed平均） | {np.mean(rnd_multi):+.2f} |")
    L.append("")
    L.append("| t1 | 规则选 sym/首封 | pnl_nt% | 候选 |")
    L.append("|---|---|---|---|")
    for d, g in g5[g5["t1"].isin(multi_days)].groupby("t1"):
        g = g.sort_values(["seal_rank", "lb"], ascending=[True, False])
        sel = g.iloc[0]
        cands = "; ".join(f"{r['sym']}({r['first_seal'] if pd.notna(r['first_seal']) else '?'},{r['pnl_nt']:+.1f}%)" for _, r in g.iterrows())
        L.append(f"| {d} | {sel['sym']}/{sel['first_seal']} | {sel['pnl_nt']:+.2f} | {cands} |")
    L.append("")

open(OUT / "selection_alpha_variant.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L[:10]))
