"""W2 执行价格敏感性：开盘买入 vs 10:00 后买入

W2 判定需开盘后 30 分钟（9:31-10:00 破前低则放弃）。
真实执行：10:00 后确认未破前低 → 市价买入（价格高于开盘）。
敏感性：按 10:00 价买入（+0%/+0.5%/+1% 相对开盘溢价）重算 5+ W2 保留组期望。
"""
import pandas as pd, numpy as np, json
from pathlib import Path
from math import sqrt
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"
CACHE = json.load(open(OUT / "w2_cache.json"))
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001

def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)

L = ["# W2 执行价格敏感性（10:00 后买入 vs 开盘买入）", ""]
L.append("| 年 | n | 开盘买入均值% | 10:00价买入% | +0.5%溢价% | +1.0%溢价% |")
L.append("|---|---|---|---|---|---|")
for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]].dropna(subset=["pnl_nt"]).copy()
    qfq = QFQStore(str(y))
    rows = []
    for _, r in g.iterrows():
        if CACHE.get(f"{y}|D|{r['sym']}|{r['t1']}") == True:
            continue  # W2 剔除
        if CACHE.get(f"{y}|D|{r['sym']}|{r['t1']}") is None:
            continue
        daily = qfq.get_stock(r["sym"])
        if not daily: continue
        dates = [x[1] for x in daily]
        if r["t1"] not in dates: continue
        i = dates.index(r["t1"])
        if i + 1 >= len(daily): continue
        entry_open = float(daily[i][2])
        mrows = qfq.get_minute(r["sym"], start=r["t1"], end=r["t1"])
        if not mrows: continue
        day = pd.DataFrame(mrows, columns=["symbol","freq","ts","open","high","low","close","volume","amount"])
        row10 = day[day["ts"].str[11:16] >= "10:00"]
        if row10.empty: continue
        p10 = float(row10["close"].iloc[0])
        r1 = daily[i + 1]
        lo1, cl1 = float(r1[4]), float(r1[5])
        stop = entry_open * 0.95
        def exit_of(entry_px):
            sp = entry_px * 0.95
            op2 = float(r1[2]); lo2 = float(r1[4]); cl2 = float(r1[5])
            if lo2 <= sp:
                return (op2 if op2 <= sp else sp)
            return cl2
        def pnl(entry_px):
            ex = exit_of(entry_px)
            return (sell_net(ex) / buy_net(entry_px) - 1) * 100
        rows.append({
            "open": pnl(entry_open),
            "p10": pnl(p10),
            "p105": pnl(p10 * 1.005),
            "p101": pnl(p10 * 1.01),
        })
    d = pd.DataFrame(rows)
    L.append(f"| {y} | {len(d)} | {d['open'].mean():+.2f} | {d['p10'].mean():+.2f} | {d['p105'].mean():+.2f} | {d['p101'].mean():+.2f} |")
    # 胜率
    L.append(f"| 胜率 | | {(d['open']>0).mean()*100:.0f}% | {(d['p10']>0).mean()*100:.0f}% | {(d['p105']>0).mean()*100:.0f}% | {(d['p101']>0).mean()*100:.0f}% |")
L.append("")
open(OUT / "w2_exec_price_sensitivity.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
