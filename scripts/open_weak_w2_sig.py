"""P2-B：W2 规则的显著性检验（t 检验 + 分月稳健性）"""
import pandas as pd, numpy as np
from math import sqrt
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"

def features(qfq, sym, ed):
    daily = qfq.get_stock(sym)
    if not daily: return None
    dates = [x[1] for x in daily]
    if ed not in dates: return None
    i = dates.index(ed)
    if i < 1: return None
    prev_low = float(daily[i-1][3])
    mrows = qfq.get_minute(sym, start=ed, end=ed)
    if not mrows: return None
    day = pd.DataFrame(mrows, columns=["symbol","freq","ts","open","high","low","close","volume","amount"])
    low30 = float(day["low"].iloc[:30].min())
    return low30 < prev_low

L = ["# P2-B：W2 破前日低规则显著性（修复后）", ""]
L.append("| 年 | 保留n | 均值% | 胜率% | 剔除n | 剔除均值% | 差值pp | t(差值) |")
L.append("|---|---|---|---|---|---|---|---|")
all_rows = []
for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_market_{y}_t1raw.csv", dtype={"sym": str})
    qfq = QFQStore(str(y))
    rows = []
    for _, r in df.iterrows():
        w = features(qfq, r["sym"], r["ed"])
        if w is not None:
            rows.append({"pnl": r["pnl"], "ed": r["ed"], "weak": w})
    d = pd.DataFrame(rows)
    keep, drop = d[~d["weak"]], d[d["weak"]]
    diff = keep["pnl"].mean() - drop["pnl"].mean()
    # Welch t
    s1, s2 = keep["pnl"].std(), drop["pnl"].std()
    n1, n2 = len(keep), len(drop)
    se = sqrt(s1**2/n1 + s2**2/n2)
    t = diff / se if se > 0 else 0
    L.append(f"| {y} | {n1} | {keep['pnl'].mean():+.2f} | {(keep['pnl']>0).mean()*100:.0f} | {n2} | {drop['pnl'].mean():+.2f} | {diff:+.2f} | {t:.2f} |")
    d["year"] = y
    all_rows.append(d)
# 合并 + 分月
d = pd.concat(all_rows)
d["month"] = d["ed"].str[:7]
L.append("")
L.append("## 分月稳健性（保留组 vs 全量，月均值）")
L.append("")
L.append("| 月 | 保留n | 保留均值% | 全量均值% | 保留胜率% |")
L.append("|---|---|---|---|---|")
for m, g in d.groupby("month"):
    k = g[~g["weak"]]
    if len(k) >= 5:
        L.append(f"| {m} | {len(k)} | {k['pnl'].mean():+.2f} | {g['pnl'].mean():+.2f} | {(k['pnl']>0).mean()*100:.0f} |")
L.append("")
# 分月方向一致性：保留 > 全量的月数
pos = 0; tot = 0
for m, g in d.groupby("month"):
    k = g[~g["weak"]]
    if len(k) >= 5:
        tot += 1
        if k["pnl"].mean() > g["pnl"].mean(): pos += 1
L.append(f"保留组 > 全量 的月份：{pos}/{tot}")
open(OUT / "open_weak_w2_significance.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
