"""P2-B 深化：开盘走弱放弃规则完整验证（修复后口径）

规则空间（全部盘中可得，无前视）：
- W1: 低开 > 3%
- W2: 开盘30分钟内跌破前日低点
- W3: W1 或 W2
- W4: W1 且 W2（更严）
验证：动量池（t1raw 修复后）保留组 vs 全量；2025 定规则 → 2026 验证
"""
import pandas as pd, numpy as np
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
    pc = float(daily[i-1][5])
    mrows = qfq.get_minute(sym, start=ed, end=ed)
    if not mrows: return None
    day = pd.DataFrame(mrows, columns=["symbol","freq","ts","open","high","low","close","volume","amount"])
    op = float(day["open"].iloc[0])
    low30 = float(day["low"].iloc[:30].min())
    return {
        "low_open": (op / pc - 1) < -0.03,
        "break_prev_low": low30 < prev_low,
        "gap": (op / pc - 1) * 100,
    }

L = ["# P2-B 深化：开盘走弱规则空间（修复后 t1raw）", ""]
L.append("| 年 | 规则 | n(保留) | 均值% | 胜率% | 剔除n | 剔除均值% |")
L.append("|---|---|---|---|---|---|---|")
for y in (2025, 2026):
    p = OUT / f"backtest_market_{y}_t1raw.csv"
    df = pd.read_csv(p, dtype={"sym": str})
    qfq = QFQStore(str(y))
    rows = []
    for _, r in df.iterrows():
        f = features(qfq, r["sym"], r["ed"])
        if f:
            rows.append({"pnl": r["pnl"], **f})
    d = pd.DataFrame(rows)
    L.append(f"## {y}（n={len(d)}）")
    L.append("")
    L.append("| 规则 | 保留n | 保留均值% | 保留胜率% | 剔除n | 剔除均值% |")
    L.append("|---|---|---|---|---|---|")
    for name, mask in (
        ("W1 低开>3%", d["low_open"]),
        ("W2 破前日低", d["break_prev_low"]),
        ("W3 W1或W2", d["low_open"] | d["break_prev_low"]),
        ("W4 W1且W2", d["low_open"] & d["break_prev_low"]),
    ):
        keep = d[~mask]
        drop = d[mask]
        L.append(f"| {name} | {len(keep)} | {keep['pnl'].mean():+.2f} | {(keep['pnl']>0).mean()*100:.0f} | {len(drop)} | {drop['pnl'].mean():+.2f} |")
    L.append("")
open(OUT / "open_weak_rulespace.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L[:20]))
