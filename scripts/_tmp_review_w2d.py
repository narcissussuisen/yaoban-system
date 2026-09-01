"""评审补算 v4：可成交子集（10:00 未封板）+ gap 分解 + 配对检验"""
import sys, json, math
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(r"C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system")
OUT = BASE / "outputs"
sys.path.insert(0, str(BASE / "src"))
from data.qfq_store import QFQStore
from core.sell import limit_pct_of

COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001
def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)
CACHE = json.load(open(OUT / "w2_cache.json", encoding="utf-8"))

def load_signals(year):
    df = pd.read_csv(OUT / f"backtest_daban_{year}_raw.csv", dtype={"sym": str})
    return df[(df["lb"] >= 5) & ~df["yizi"]].dropna(subset=["pnl_nt"]).copy()

def exit_px(r1, entry):
    stop = entry * 0.95
    op1, lo1, cl1 = float(r1[2]), float(r1[4]), float(r1[5])
    if lo1 <= stop:
        return op1 if op1 <= stop else stop
    return cl1

def pnl_of(entry, ex):
    return (sell_net(ex) / buy_net(entry) - 1) * 100

def tstat(v):
    v = np.asarray(v, float)
    n = len(v)
    if n < 2: return 0.0
    sd = v.std(ddof=1)
    return v.mean() / (sd / math.sqrt(n)) if sd > 0 else 0.0

rows = []
for y in (2023, 2024, 2025, 2026):
    g = load_signals(y)
    qfq = QFQStore(str(y))
    for _, r in g.iterrows():
        w = CACHE.get(f"{y}|D|{r['sym']}|{r['t1']}")
        if w is not False: continue
        daily = qfq.get_stock(r["sym"])
        if not daily: continue
        dates = [x[1] for x in daily]
        if r["t1"] not in dates: continue
        i = dates.index(r["t1"])
        if i + 1 >= len(daily): continue
        mrows = qfq.get_minute(r["sym"], start=r["t1"], end=r["t1"])
        if not mrows: continue
        day = pd.DataFrame(mrows, columns=["symbol","freq","ts","open","high","low","close","volume","amount"])
        if len(day) < 30: continue
        open_px = float(day["open"].iloc[0])
        r1 = daily[i + 1]
        pc_prev = float(daily[i-1][5])
        limit_px = round(pc_prev * (1 + limit_pct_of(r["sym"])), 2)
        sub10 = day[day["ts"].str[11:16] >= "10:00"]
        if sub10.empty: continue
        p10 = float(sub10["close"].iloc[0])
        sealed = p10 >= limit_px - 0.005
        pnl_open = pnl_of(open_px, exit_px(r1, open_px))
        pnl_p10 = pnl_of(p10, exit_px(r1, p10))
        rows.append({"year": y, "sym": r["sym"], "t1": r["t1"], "sealed": sealed,
                     "gap": (p10 / open_px - 1) * 100, "pnl_nt": float(r["pnl_nt"]),
                     "pnl_p10": pnl_p10, "lb": int(r["lb"])})
df = pd.DataFrame(rows)
out = {}
# gap 分解
out["gap_by_sealed"] = df.groupby("sealed")["gap"].agg(["count", "mean", "median"]).round(3).to_dict()
# 可成交子集（未封板）统计
sub = df[~df["sealed"]]
stats = {}
for y in (2023, 2024, 2025, 2026):
    s = sub[sub["year"] == y]
    stats[str(y)] = {"n": int(len(s)), "mean_open": round(float(s["pnl_nt"].mean()), 3),
                     "mean_p10": round(float(s["pnl_p10"].mean()), 3),
                     "t_p10": round(tstat(s["pnl_p10"]), 3),
                     "win_p10": round(float((s["pnl_p10"] > 0).mean()) * 100, 1)}
s2526 = sub[sub["year"].isin([2025, 2026])]
s4 = sub
out["fillable_stats"] = {"per_year": stats,
    "c2526": {"n": int(len(s2526)), "mean_open": round(float(s2526["pnl_nt"].mean()), 3),
              "mean_p10": round(float(s2526["pnl_p10"].mean()), 3),
              "t_p10": round(tstat(s2526["pnl_p10"]), 3),
              "win_p10": round(float((s2526["pnl_p10"] > 0).mean()) * 100, 1)},
    "c4y": {"n": int(len(s4)), "mean_open": round(float(s4["pnl_nt"].mean()), 3),
            "mean_p10": round(float(s4["pnl_p10"].mean()), 3),
            "t_p10": round(tstat(s4["pnl_p10"]), 3)}}
# 配对检验：open vs p10（全部保留组）
d_all = df
diffv = d_all["pnl_nt"] - d_all["pnl_p10"]
out["paired_timing"] = {"n": len(diffv), "mean_diff_pp": round(float(diffv.mean()), 3),
                        "t_paired": round(float(diffv.mean() / (diffv.std(ddof=1) / math.sqrt(len(diffv)))), 3),
                        "pct_positive_diff": round(float((diffv > 0).mean()) * 100, 1)}
out["sealed_frac_by_year"] = df.groupby("year")["sealed"].mean().round(3).to_dict()
json.dump(out, open(OUT / "_review_w2_result4.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
