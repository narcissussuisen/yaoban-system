"""评审整改项：动量池 W2 结论按 10:00 口径复测

此前动量池 W2 结论（2025 保留 +2.08% t=2.70）用开盘买入口径——同样存在前视嫌疑。
按 10:00 入场（判定=前缀未破前低）重算。
"""
import pandas as pd, numpy as np
from pathlib import Path
from math import sqrt
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001
def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)

L = ["# 动量池 W2 10:00 口径复测（评审整改项①）", ""]
L.append("| 年 | 口径 | n | 均值% | 胜率% | t |")
L.append("|---|---|---|---|---|---|")
for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_market_{y}_t1raw.csv", dtype={"sym": str})
    qfq = QFQStore(str(y))
    rows = []
    for _, r in df.iterrows():
        sym, ed = r["sym"], r["ed"]
        daily = qfq.get_stock(sym)
        if not daily: continue
        dates = [x[1] for x in daily]
        if ed not in dates: continue
        i = dates.index(ed)
        if i < 1 or i + 1 >= len(daily): continue
        prev_low = float(daily[i-1][3])
        mrows = qfq.get_minute(sym, start=ed, end=ed)
        if not mrows: continue
        day = pd.DataFrame(mrows, columns=["symbol","freq","ts","open","high","low","close","volume","amount"])
        low30 = float(day["low"].iloc[:30].min())
        row10 = day[day["ts"].str[11:16] >= "10:00"]
        if row10.empty: continue
        p10 = float(row10["close"].iloc[0])
        r1 = daily[i + 1]
        op2, lo2, cl2 = float(r1[2]), float(r1[4]), float(r1[5])
        def pnl_at(entry_px):
            sp = entry_px * 0.95
            ex = (op2 if op2 <= sp else sp) if lo2 <= sp else cl2
            return (sell_net(ex) / buy_net(entry_px) - 1) * 100
        weak = low30 < prev_low
        rows.append({"weak": weak, "pnl_open": pnl_at(float(day["open"].iloc[0])), "pnl_p10": pnl_at(p10)})
    d = pd.DataFrame(rows)
    for name, sub, col in (("全量(开盘)", d, "pnl_open"), ("W2保留(开盘)", d[~d["weak"]], "pnl_open"),
                           ("W2保留(10:00)", d[~d["weak"]], "pnl_p10")):
        n = len(sub)
        m = sub[col].mean()
        sd = sub[col].std()
        t = m/(sd/sqrt(n)) if n > 1 and sd > 0 else 0
        w = (sub[col]>0).mean()*100
        L.append(f"| {y} | {name} | {n} | {m:+.2f} | {w:.0f} | {t:.2f} |")
    L.append("")
open(OUT / "momentum_w2_10am_recheck.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
