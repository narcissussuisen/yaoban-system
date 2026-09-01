"""P2-B 第一轮执行：W2 过滤 + B2 点确认叠加

变体1（W2+B2入场）：开盘走弱放弃 + 以 B2 触发价买入（选手 B 点确认）
变体2（W2+回踩站稳）：开盘走弱放弃 + 回踩后站稳均价线再买（简化：B2 存在即买）
对比：W2 单独（已有）vs 叠加
"""
import pandas as pd, numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore
from core.intraday import day_b_points

OUT = Path(__file__).resolve().parent.parent / "outputs"

def w2_and_b2(qfq, sym, ed):
    """返回 (w2_weak, b2_px) 或 None"""
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
    weak = low30 < prev_low
    # B2 点
    b2 = None
    pts = day_b_points(qfq, sym, ed, freq="1m", lookback=20, cool_min=30, open_win=30, surge_win=60)
    if pts is not None and not pts.empty:
        b2p = pts[pts["kind"] == "B2"]
        if not b2p.empty:
            b2 = float(b2p.iloc[0]["price"])
    return weak, b2

L = ["# P2-B 第一轮：W2 + B2 叠加（修复后）", ""]
for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_market_{y}_t1raw.csv", dtype={"sym": str})
    qfq = QFQStore(str(y))
    rows = []
    for _, r in df.iterrows():
        f = w2_and_b2(qfq, r["sym"], r["ed"])
        if f:
            weak, b2 = f
            rows.append({"pnl_open": r["pnl"], "weak": weak, "b2": b2})
    d = pd.DataFrame(rows)
    # W2 保留组
    keep = d[~d["weak"]]
    # W2+B2：保留组且有 B2
    k2 = keep[keep["b2"].notna()]
    L.append(f"## {y}")
    L.append("")
    L.append("| 口径 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    L.append(f"| 全量（开盘） | {len(d)} | {d['pnl_open'].mean():+.2f} | {(d['pnl_open']>0).mean()*100:.0f} |")
    L.append(f"| W2 保留（开盘） | {len(keep)} | {keep['pnl_open'].mean():+.2f} | {(keep['pnl_open']>0).mean()*100:.0f} |")
    L.append(f"| W2保留+B2存在 | {len(k2)} | {k2['pnl_open'].mean():+.2f} | {(k2['pnl_open']>0).mean()*100:.0f} |")
    L.append("")
open(OUT / "intraday_bpoint_2025_2026.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
