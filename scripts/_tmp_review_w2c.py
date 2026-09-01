"""评审补算 v3：前缀一致入场族（buy at t using info<=t）+ 10:00 涨停封死占比"""
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

def clust_t(df):
    sm = df.groupby("sym")["pnl"].mean()
    if len(sm) < 2: return float("nan")
    return sm.mean() / (sm.std(ddof=1) / math.sqrt(len(sm))) if sm.std(ddof=1) > 0 else float("nan")

# 入场时间表：bar 标签 (ts[11:16] >= t) 的第一个 bar 的 close 作为 entry
entry_times = ["09:35", "09:40", "09:45", "09:50", "09:55", "10:00", "10:05", "10:15"]
res = {}
all_rows = []
sealed_info = {}
for y in (2023, 2024, 2025, 2026):
    g = load_signals(y)
    qfq = QFQStore(str(y))
    n_sealed_at10 = n_kept = 0
    for _, r in g.iterrows():
        w = CACHE.get(f"{y}|D|{r['sym']}|{r['t1']}")
        if w is not False:
            continue
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
        # 涨停价（t1 当日）
        pc_prev = float(daily[i-1][5]) if i >= 1 else float(day["close"].iloc[0])
        limit_px = round(pc_prev * (1 + limit_pct_of(r["sym"])), 2)
        row = {"year": y, "sym": r["sym"], "t1": r["t1"], "pnl_nt": float(r["pnl_nt"])}
        for t in entry_times:
            sub = day[day["ts"].str[11:16] >= t]
            if sub.empty:
                continue
            # 前缀一致过滤：min(low[:t]) > prev_low 才保留该入场
            prev_low = float(daily[i-1][3])
            low_prefix = float(day.loc[day["ts"].str[11:16] < t, "low"].min()) if (day["ts"].str[11:16] < t).any() else float(day["low"].iloc[0])
            entry = float(sub["close"].iloc[0])
            # 用当日分钟判断是否已封板（close >= limit - tol）
            sealed = entry >= limit_px - 0.005
            row[f"pnl_{t.replace(':','')}"] = pnl_of(entry, exit_px(r1, entry))
            row[f"keep_{t.replace(':','')}"] = bool(low_prefix > prev_low)
            row[f"sealed_{t.replace(':','')}"] = bool(sealed)
        # 10:00 封死统计
        sub10 = day[day["ts"].str[11:16] >= "10:00"]
        if not sub10.empty:
            p10 = float(sub10["close"].iloc[0])
            if p10 >= limit_px - 0.005:
                n_sealed_at10 += 1
        n_kept += 1
        all_rows.append(row)
    sealed_info[str(y)] = {"kept": n_kept, "sealed_at_10": n_sealed_at10}
res["sealed"] = sealed_info

dfF = pd.DataFrame(all_rows)
# 对每个入场 t：仅保留前缀一致的行，统计
stats = {}
for t in entry_times:
    col = "pnl_" + t.replace(":", "")
    kc = "keep_" + t.replace(":", "")
    d = dfF[dfF[kc] == True].copy()
    d["pnl"] = d[col]
    per = {}
    for y in (2023, 2024, 2025, 2026):
        yy = d[d["year"] == y]
        per[str(y)] = {"n": int(len(yy)), "mean": round(float(yy["pnl"].mean()), 3),
                       "t": round(tstat(yy["pnl"]), 3),
                       "win": round(float((yy["pnl"] > 0).mean()) * 100, 1)}
    c2526 = d[d["year"].isin([2025, 2026])]
    c4 = d
    stats[t] = {"per_year": per,
                "c2526": {"n": int(len(c2526)), "mean": round(float(c2526["pnl"].mean()), 3),
                          "t": round(tstat(c2526["pnl"]), 3),
                          "win": round(float((c2526["pnl"] > 0).mean()) * 100, 1),
                          "clust": round(clust_t(c2526), 3)},
                "c4y": {"n": int(len(c4)), "mean": round(float(c4["pnl"].mean()), 3),
                        "t": round(tstat(c4["pnl"]), 3),
                        "clust": round(clust_t(c4), 3)}}
res["entry_family"] = stats

json.dump(res, open(OUT / "_review_w2_result3.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("sealed:", json.dumps(sealed_info, ensure_ascii=False))
print()
print(f"{'entry':>6} | {'25+26 n':>7} {'mean':>7} {'t':>6} {'win':>4} {'clust':>6} | {'4y n':>5} {'mean':>7} {'t':>6} {'clust':>6}")
for t in entry_times:
    s = stats[t]
    print(f"{t:>6} | {s['c2526']['n']:>7} {s['c2526']['mean']:>+7.3f} {s['c2526']['t']:>6.2f} {s['c2526']['win']:>4.0f} {s['c2526']['clust']:>6.2f} | {s['c4y']['n']:>5} {s['c4y']['mean']:>+7.3f} {s['c4y']['t']:>6.2f} {s['c4y']['clust']:>6.2f}")
print()
for t in entry_times:
    print(t, {y: (stats[t]["per_year"][y]["n"], stats[t]["per_year"][y]["mean"], stats[t]["per_year"][y]["t"]) for y in ("2023","2024","2025","2026")})
