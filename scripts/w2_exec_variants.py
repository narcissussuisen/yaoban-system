"""W2 执行变体：10:00 后买入 + 走强确认（10:00 涨幅>0 且未破前低）

真实执行语义：观察 30 分钟 → 未破前低 且 10:00 时仍在上方 → 10:00 后买入
"""
import pandas as pd, numpy as np, json
from pathlib import Path
from math import sqrt
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001

def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)

L = ["# W2 执行变体：10:00 后买入 + 走强确认", ""]
L.append("| 年 | 口径 | n | 均值% | 胜率% | t |")
L.append("|---|---|---|---|---|---|")
for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]].dropna(subset=["pnl_nt"]).copy()
    qfq = QFQStore(str(y))
    rows = []
    for _, r in g.iterrows():
        daily = qfq.get_stock(r["sym"])
        if not daily: continue
        dates = [x[1] for x in daily]
        if r["t1"] not in dates: continue
        i = dates.index(r["t1"])
        if i < 1 or i + 1 >= len(daily): continue
        prev_low = float(daily[i-1][3])
        pc = float(daily[i-1][5])
        mrows = qfq.get_minute(r["sym"], start=r["t1"], end=r["t1"])
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
        strong = p10 > pc  # 10:00 相对昨收上涨
        rows.append({"weak": weak, "strong": strong,
                     "pnl_open": pnl_at(float(day["open"].iloc[0])),
                     "pnl_p10": pnl_at(p10)})
    d = pd.DataFrame(rows)
    # 口径1：未破前低（W2 保留）+ 10:00 买入
    k1 = d[~d["weak"]]
    # 口径2：未破前低 + 走强 + 10:00 买入
    k2 = d[(~d["weak"]) & d["strong"]]
    # 口径3：全部 + 10:00 买入（对照）
    for name, sub in (("全部(10:00买入)", d), ("W2保留(10:00买入)", k1), ("W2+走强(10:00买入)", k2)):
        n = len(sub)
        m = sub["pnl_p10"].mean()
        sd = sub["pnl_p10"].std()
        t = m/(sd/sqrt(n)) if n > 1 and sd > 0 else 0
        w = (sub["pnl_p10"]>0).mean()*100
        L.append(f"| {y} | {name} | {n} | {m:+.2f} | {w:.0f} | {t:.2f} |")
    L.append("")
open(OUT / "w2_exec_variants.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
