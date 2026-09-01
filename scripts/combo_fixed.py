"""修复后组合级重跑：5+ 连板（修复后 pnl_nt）+ 动量池（修复后 t1raw + W2 过滤）± 回踩池

对照：
1. 5+ 单独（修复后）
2. 5+ + 动量（W2 过滤）
3. 5+ + 动量 + 回踩（全部修复后口径）
双票 50%×2，20 seeds 平均
"""
import pandas as pd, numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"
capital0 = 50000.0

def w2_weak(qfq, sym, ed):
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
    return float(day["low"].iloc[:30].min()) < prev_low

def load_flows(year, use_w2):
    flows = []
    db = pd.read_csv(OUT / f"backtest_daban_{year}_raw.csv", dtype={"sym": str})
    g5 = db[(db["lb"] >= 5) & (~db["yizi"])].dropna(subset=["pnl_nt"]).copy()
    g5["src"] = "5+连板"; g5["entry"] = g5["t1"]; g5["pnl"] = g5["pnl_nt"]
    flows.append(g5[["sym","entry","pnl","src"]])
    mf = OUT / f"backtest_market_{year}_t1raw.csv"
    m = None
    if mf.exists():
        m = pd.read_csv(mf, dtype={"sym": str})
        if use_w2:
            qfq = QFQStore(str(year))
            weak_map = {}
            for _, r in m.iterrows():
                w = w2_weak(qfq, r["sym"], r["ed"])
                if w is not None:
                    weak_map[(r["sym"], r["ed"])] = w
            m["weak"] = m.apply(lambda r: weak_map.get((r["sym"], r["ed"]), False), axis=1)
            m = m[~m["weak"]]
        m["src"] = "动量池"; m["entry"] = m["ed"]
    pf = pd.read_csv(OUT / f"pullback_b2_{year}_raw.csv", dtype={"sym": str})
    p = pf.dropna(subset=["p_open"]).copy()
    p["src"] = "回踩池"; p["entry"] = p["ed"]; p["pnl"] = p["p_open"]
    return g5[["sym","entry","pnl","src"]], (m[["sym","entry","pnl","src"]] if m is not None else None), p[["sym","entry","pnl","src"]]

def run_combo(parts, seed):
    allf = pd.concat(parts, ignore_index=True)
    allf = allf[allf["pnl"].notna() & (allf["pnl"] > -99)]
    prio = {"5+连板": 0, "动量池": 1, "回踩池": 2}
    allf["prio"] = allf["src"].map(prio)
    allf["rnd"] = np.random.default_rng(seed).random(len(allf))
    s = allf.sort_values(["entry","prio","rnd"], ascending=[True,True,True])
    picks = s.groupby("entry").head(2).reset_index(drop=True).sort_values("entry")
    capital = capital0
    for day, g in picks.groupby("entry"):
        if len(g) == 1:
            capital *= (1 + float(g["pnl"].iloc[0]) / 100.0)
        else:
            r1 = float(g["pnl"].iloc[0]) / 100.0
            r2 = float(g["pnl"].iloc[1]) / 100.0
            capital = capital * (0.5 * (1 + r1) + 0.5 * (1 + r2))
    return (capital / capital0 - 1) * 100

L = ["# 修复后组合级重跑（双票 50%×2，20 seeds）", ""]
L.append("| 年 | 5+单独 | 5++动量(W2) | 5++动量+回踩 |")
L.append("|---|---|---|---|")
for y in (2023, 2024, 2025, 2026):
    g5, m, p = load_flows(str(y), use_w2=True)
    combos = {
        "5+": [g5],
        "5++动量W2": [g5, m] if m is not None else [g5],
        "全池": [g5, m, p] if m is not None else [g5, p],
    }
    vals = []
    for name, parts in combos.items():
        avg = np.mean([run_combo(parts, s) for s in range(20)])
        vals.append(avg)
    L.append(f"| {y} | {vals[0]:+.1f}% | {vals[1]:+.1f}% | {vals[2]:+.1f}% |")
open(OUT / "combo_fixed_4y.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
