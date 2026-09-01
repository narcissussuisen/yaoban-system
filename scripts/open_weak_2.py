"""P2-B 前置补充：开盘走弱放弃规则（回踩池 + 5+ 打板池）"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"
L = ["# P2-B 前置补充：开盘走弱（回踩池 + 5+ 打板）", ""]

for y in (2025, 2026):
    qfq = QFQStore(str(y))
    # 回踩池
    pf = pd.read_csv(OUT / f"pullback_b2_{y}_raw.csv", dtype={"sym": str})
    p = pf.dropna(subset=["p_open"]).copy()
    rows = []
    for _, r in p.iterrows():
        sym, ed = r["sym"], r["ed"]
        daily = qfq.get_stock(sym)
        if not daily:
            continue
        dates = [x[1] for x in daily]
        if ed not in dates:
            continue
        i = dates.index(ed)
        if i < 1:
            continue
        prev_low = float(daily[i - 1][3])
        mrows = qfq.get_minute(sym, start=ed, end=ed)
        weak = None
        if mrows:
            day = pd.DataFrame(mrows, columns=["symbol", "freq", "ts", "open", "high",
                                               "low", "close", "volume", "amount"])
            op = float(day["open"].iloc[0])
            pc = float(daily[i - 1][5])
            low30 = float(day["low"].iloc[:30].min())
            weak = (op / pc - 1 < -0.03) or (low30 < prev_low)
        rows.append({"pnl": r["p_open"], "weak": weak})
    d = pd.DataFrame(rows).dropna(subset=["weak"])
    allv = d["pnl"]
    keep = d[d["weak"] == False]["pnl"]
    drop = d[d["weak"] == True]["pnl"]
    L.append(f"## {y} 回踩池（n={len(allv)}，走弱占比 {(d['weak']==True).mean()*100:.0f}%）")
    L.append("")
    L.append("| 口径 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    L.append(f"| 全量 | {len(allv)} | {allv.mean():+.2f} | {(allv>0).mean()*100:.0f} |")
    L.append(f"| 保留（未走弱） | {len(keep)} | {keep.mean():+.2f} | {(keep>0).mean()*100:.0f} |")
    if len(drop):
        L.append(f"| 放弃（走弱） | {len(drop)} | {drop.mean():+.2f} | {(drop>0).mean()*100:.0f} |")
    L.append("")
    # 5+ 打板池
    db = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g5 = db[(db["lb"] >= 5) & ~db["yizi"]].dropna(subset=["pnl_nt"]).copy()
    rows = []
    for _, r in g5.iterrows():
        sym, t1 = r["sym"], r["t1"]
        daily = qfq.get_stock(sym)
        if not daily:
            continue
        dates = [x[1] for x in daily]
        if t1 not in dates:
            continue
        i = dates.index(t1)
        if i < 1:
            continue
        prev_low = float(daily[i - 1][3])
        mrows = qfq.get_minute(sym, start=t1, end=t1)
        weak = None
        if mrows:
            day = pd.DataFrame(mrows, columns=["symbol", "freq", "ts", "open", "high",
                                               "low", "close", "volume", "amount"])
            op = float(day["open"].iloc[0])
            pc = float(daily[i - 1][5])
            low30 = float(day["low"].iloc[:30].min())
            weak = (op / pc - 1 < -0.03) or (low30 < prev_low)
        rows.append({"pnl": r["pnl_nt"], "weak": weak})
    d5 = pd.DataFrame(rows).dropna(subset=["weak"])
    allv = d5["pnl"]
    keep = d5[d5["weak"] == False]["pnl"]
    drop = d5[d5["weak"] == True]["pnl"]
    L.append(f"## {y} 5+ 打板（n={len(allv)}，走弱占比 {(d5['weak']==True).mean()*100:.0f}%）")
    L.append("")
    L.append("| 口径 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    L.append(f"| 全量 | {len(allv)} | {allv.mean():+.2f} | {(allv>0).mean()*100:.0f} |")
    L.append(f"| 保留（未走弱） | {len(keep)} | {keep.mean():+.2f} | {(keep>0).mean()*100:.0f} |")
    if len(drop):
        L.append(f"| 放弃（走弱） | {len(drop)} | {drop.mean():+.2f} | {(drop>0).mean()*100:.0f} |")
    L.append("")

open(OUT / "open_weak_pullback_daban.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L[:24]))
