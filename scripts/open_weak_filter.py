"""P2-B 前置研究：开盘走弱放弃规则验证（不依赖 L2）

选手规则：次日开盘 30 分钟内跌破前日低点 / 低开>3% → 当日放弃买入
验证：动量池/回踩池候选，若 ed 日开盘走弱（低开>3% 或 前30分钟跌破前日低点）→ 放弃
对比：全量 vs 放弃后的收益/胜率
数据：backtest_market_{year}_t1raw.csv（动量池）+ 分钟线
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"
L = ["# P2-B 前置：开盘走弱放弃规则验证（动量池）", ""]
L.append("| 年 | 口径 | n | 均值% | 胜率% |")
L.append("|---|---|---|---|---|")

for y in (2025, 2026):
    p = OUT / f"backtest_market_{y}_t1raw.csv"
    if not p.exists():
        continue
    df = pd.read_csv(p, dtype={"sym": str})
    qfq = QFQStore(str(y))
    rows = []
    for _, r in df.iterrows():
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
        rows.append({"pnl": r["pnl"], "weak": weak})
    d = pd.DataFrame(rows)
    allv = d["pnl"]
    keep = d[d["weak"] == False]["pnl"]
    drop = d[d["weak"] == True]["pnl"]
    L.append(f"| {y} | 全量 | {len(allv)} | {allv.mean():+.2f} | {(allv>0).mean()*100:.0f} |")
    L.append(f"| {y} | 保留（未走弱） | {len(keep)} | {keep.mean():+.2f} | {(keep>0).mean()*100:.0f} |")
    if len(drop):
        L.append(f"| {y} | 放弃（走弱） | {len(drop)} | {drop.mean():+.2f} | {(drop>0).mean()*100:.0f} |")
    L.append("")
open(OUT / "open_weak_filter.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
