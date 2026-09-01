"""W2+5+ 组合级验证（用缓存，快）"""
import pandas as pd, numpy as np, json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "outputs"
capital0 = 50000.0
CACHE = json.load(open(OUT / "w2_cache.json"))

def weak_of(year, kind, sym, ed):
    return CACHE.get(f"{year}|{kind}|{sym}|{ed}")

def load(year, w2_on):
    flows = []
    db = pd.read_csv(OUT / f"backtest_daban_{year}_raw.csv", dtype={"sym": str})
    g5 = db[(db["lb"] >= 5) & ~db["yizi"]].dropna(subset=["pnl_nt"]).copy()
    if w2_on:
        g5["weak"] = g5.apply(lambda r: weak_of(year, "D", r["sym"], r["t1"]), axis=1)
        g5 = g5[g5["weak"] == False]
    g5["src"] = "5+"; g5["entry"] = g5["t1"]; g5["pnl"] = g5["pnl_nt"]
    flows.append(g5[["sym","entry","pnl","src"]])
    mf = OUT / f"backtest_market_{year}_t1raw.csv"
    m = None
    if mf.exists():
        m = pd.read_csv(mf, dtype={"sym": str})
        if w2_on:
            m["weak"] = m.apply(lambda r: weak_of(year, "M", r["sym"], r["ed"]), axis=1)
            m = m[m["weak"] == False]
        m["src"] = "动量"; m["entry"] = m["ed"]
    return g5[["sym","entry","pnl","src"]], (m[["sym","entry","pnl","src"]] if m is not None else None)

def run(parts, seed):
    allf = pd.concat(parts, ignore_index=True)
    allf = allf[allf["pnl"].notna() & (allf["pnl"] > -99)]
    prio = {"5+": 0, "动量": 1}
    allf["prio"] = allf["src"].map(prio)
    allf["rnd"] = np.random.default_rng(seed).random(len(allf))
    s = allf.sort_values(["entry","prio","rnd"], ascending=[True,True,True])
    picks = s.groupby("entry").head(2).reset_index(drop=True).sort_values("entry")
    cap = capital0
    for day, g in picks.groupby("entry"):
        if len(g) == 1:
            cap *= (1 + float(g["pnl"].iloc[0]) / 100.0)
        else:
            r1 = float(g["pnl"].iloc[0]) / 100.0
            r2 = float(g["pnl"].iloc[1]) / 100.0
            cap = cap * (0.5 * (1 + r1) + 0.5 * (1 + r2))
    return (cap / capital0 - 1) * 100

L = ["# W2+5+ 组合级验证（双票，20 seeds，缓存）", ""]
L.append("| 年 | 5+全量 | 5+W2 | 5+W2+动量W2 |")
L.append("|---|---|---|---|")
for y in (2023, 2024, 2025, 2026):
    g5_all, _ = load(str(y), w2_on=False)
    g5_w2, m_w2 = load(str(y), w2_on=True)
    combos = {
        "5+全量": [g5_all],
        "5+W2": [g5_w2],
        "5+W2+动量W2": [g5_w2, m_w2] if m_w2 is not None and len(m_w2) else [g5_w2],
    }
    vals = [np.mean([run(parts, s) for s in range(20)]) for parts in combos.values()]
    L.append(f"| {y} | {vals[0]:+.1f}% | {vals[1]:+.1f}% | {vals[2]:+.1f}% |")
L.append("")
L.append("## 2025→2026 连续复利（双票）")
L.append("")
L.append("| 组合 | 两年合计 |")
L.append("|---|---|")
for name, w2on, use_m in (("5+全量", False, False), ("5+W2", True, False), ("5+W2+动量W2", True, True)):
    tot = 1.0
    for y in (2025, 2026):
        g5, m = load(str(y), w2_on=w2on)
        parts = [g5]
        if use_m and m is not None and len(m):
            parts.append(m)
        avg = np.mean([run(parts, s) for s in range(20)])
        tot *= (1 + avg / 100)
    L.append(f"| {name} | {(tot-1)*100:+.1f}% |")
open(OUT / "w2_daban5_combo.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
