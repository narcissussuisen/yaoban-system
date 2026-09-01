"""独立评审复算：W2+5+ 打板 —— 前视/执行口径、显著性、组合、幸存者"""
import sys, json, math, time
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(r"C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system")
OUT = BASE / "outputs"
sys.path.insert(0, str(BASE / "src"))
from data.qfq_store import QFQStore  # noqa: E402

COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001
def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)

CACHE = json.load(open(OUT / "w2_cache.json", encoding="utf-8"))
res = {"cache_keys": len(CACHE)}
from collections import Counter
ck = Counter()
for k in CACHE:
    parts = k.split("|")
    ck[(parts[0], parts[1])] += 1
res["cache_by_year_kind"] = {f"{a}|{b}": v for (a, b), v in ck.items()}

def load_signals(year):
    df = pd.read_csv(OUT / f"backtest_daban_{year}_raw.csv", dtype={"sym": str})
    return df[(df["lb"] >= 5) & ~df["yizi"]].dropna(subset=["pnl_nt"]).copy()

def tstat(v):
    v = np.asarray(v, dtype=float)
    n = len(v)
    if n < 2: return 0.0
    sd = v.std(ddof=1)
    if sd == 0: return 0.0
    return v.mean() / (sd / math.sqrt(n))

def clust_t(df):
    sm = df.groupby("sym")["pnl"].mean()
    if len(sm) < 2: return float("nan")
    return sm.mean() / (sm.std(ddof=1) / math.sqrt(len(sm)))

def exit_px(r1, entry):
    stop = entry * 0.95
    op1, lo1, cl1 = float(r1[2]), float(r1[4]), float(r1[5])
    if lo1 <= stop:
        return op1 if op1 <= stop else stop
    return cl1

def pnl_of(entry, ex):
    return (sell_net(ex) / buy_net(entry) - 1) * 100

years = [2023, 2024, 2025, 2026]
rows_all = []
repro = {}
for y in years:
    g = load_signals(y)
    qfq = QFQStore(str(y))
    n_keep = n_drop = n_short_min = 0
    for _, r in g.iterrows():
        w = CACHE.get(f"{y}|D|{r['sym']}|{r['t1']}")
        if w is None:
            continue
        if w:
            n_drop += 1
            continue
        n_keep += 1
        daily = qfq.get_stock(r["sym"])
        if not daily: continue
        dates = [x[1] for x in daily]
        if r["t1"] not in dates: continue
        i = dates.index(r["t1"])
        if i + 1 >= len(daily): continue
        mrows = qfq.get_minute(r["sym"], start=r["t1"], end=r["t1"])
        if not mrows: continue
        day = pd.DataFrame(mrows, columns=["symbol","freq","ts","open","high","low","close","volume","amount"])
        if len(day) < 30:
            n_short_min += 1
        open_px = float(day["open"].iloc[0])
        row10 = day[day["ts"].str[11:16] >= "10:00"]
        if row10.empty: continue
        p10c = float(row10["close"].iloc[0])
        p10o = float(row10["open"].iloc[0])
        row105 = day[day["ts"].str[11:16] >= "10:05"]
        p105c = float(row105["close"].iloc[0]) if len(row105) else p10c
        r1 = daily[i + 1]
        ex_open = exit_px(r1, open_px)
        pnl_open = pnl_of(open_px, ex_open)
        row = {"year": y, "sym": r["sym"], "t1": r["t1"], "lb": int(r["lb"]),
               "pnl_nt": float(r["pnl_nt"]), "pnl_open_re": pnl_open,
               "gap_p10c": (p10c / open_px - 1) * 100,
               "gap_p10o": (p10o / open_px - 1) * 100,
               "gap_p105c": (p105c / open_px - 1) * 100}
        for lab, entry in (("p10c", p10c), ("p10o", p10o), ("p105c", p105c),
                           ("p10c_s5", p10c * 1.005), ("p10c_s10", p10c * 1.010)):
            row["pnl_" + lab] = pnl_of(entry, exit_px(r1, entry))
        rows_all.append(row)
    repro[y] = {"n_keep": n_keep, "n_drop": n_drop, "n_short_min": n_short_min}

dfA = pd.DataFrame(rows_all)
res["repro"] = repro
res["reconcil"] = {
    "match_raw_pnl": int((dfA["pnl_nt"] - dfA["pnl_open_re"]).abs().max() < 0.01),
    "max_abs_diff_raw_vs_re": float((dfA["pnl_nt"] - dfA["pnl_open_re"]).abs().max()),
}

def year_stats(sub):
    n = len(sub)
    if n == 0: return None
    return {"n": int(n), "mean": round(float(sub["pnl"].mean()), 3),
            "t": round(tstat(sub["pnl"]), 3),
            "win": round(float((sub["pnl"] > 0).mean()) * 100, 1),
            "clust_t": round(clust_t(sub), 3),
            "q05": round(float(np.percentile(sub["pnl"], 5)), 2),
            "q95": round(float(np.percentile(sub["pnl"], 95)), 2),
            "max": round(float(sub["pnl"].max()), 2), "min": round(float(sub["pnl"].min()), 2)}

variants = ["pnl_nt", "pnl_open_re", "pnl_p10c", "pnl_p10o", "pnl_p105c", "pnl_p10c_s5", "pnl_p10c_s10"]
res["signal_stats"] = {}
for v in variants:
    d = dfA.rename(columns={v: "pnl"})
    per_year = {y: year_stats(d[d["year"] == y]) for y in years}
    c2526 = year_stats(d[d["year"].isin([2025, 2026])])
    c4y = year_stats(d)
    res["signal_stats"][v] = {"per_year": per_year, "c2526": c2526, "c4y": c4y}

res["gap"] = {}
for col in ("gap_p10c", "gap_p10o", "gap_p105c"):
    res["gap"][col] = {"mean": round(float(dfA[col].mean()), 3),
                       "median": round(float(dfA[col].median()), 3),
                       "pct_positive": round(float((dfA[col] > 0).mean()) * 100, 1),
                       "p10": round(float(np.percentile(dfA[col], 10)), 2),
                       "p90": round(float(np.percentile(dfA[col], 90)), 2)}
res["gap"]["by_year"] = {y: {c: round(float(dfA[dfA["year"] == y][c].mean()), 3)
                             for c in ("gap_p10c", "gap_p10o", "gap_p105c")} for y in years}

dfA["month"] = dfA["t1"].str[:7]
mons = []
for m, gg in dfA.groupby("month"):
    if len(gg) >= 5:
        mons.append((m, len(gg), float(gg["pnl_p10c"].mean())))
pos = sum(1 for _, _, v in mons if v > 0)
try:
    from scipy.stats import binomtest
    bp = float(binomtest(pos, len(mons), 0.5, alternative="greater").pvalue)
except Exception:
    bp = None
res["months"] = {"n_months": len(mons), "pos": pos, "binom_p_one_sided": bp,
                 "months": [(m, n, round(v, 2)) for m, n, v in mons]}

def load_pool(year, w2_on):
    db = pd.read_csv(OUT / f"backtest_daban_{year}_raw.csv", dtype={"sym": str})
    g5 = db[(db["lb"] >= 5) & ~db["yizi"]].dropna(subset=["pnl_nt"]).copy()
    if w2_on:
        g5["weak"] = g5.apply(lambda r: CACHE.get(f"{year}|D|{r['sym']}|{r['t1']}"), axis=1)
        g5 = g5[g5["weak"] == False]
    g5["src"] = "5+"; g5["entry"] = g5["t1"]; g5["pnl"] = g5["pnl_nt"]
    return g5[["sym", "entry", "pnl", "src"]]

def run(parts, seed):
    allf = pd.concat(parts, ignore_index=True)
    allf = allf[allf["pnl"].notna() & (allf["pnl"] > -99)]
    prio = {"5+": 0}
    allf["prio"] = allf["src"].map(prio)
    allf["rnd"] = np.random.default_rng(seed).random(len(allf))
    s = allf.sort_values(["entry", "prio", "rnd"], ascending=[True, True, True])
    picks = s.groupby("entry").head(2).reset_index(drop=True).sort_values("entry")
    cap = 50000.0
    for day, g in picks.groupby("entry"):
        if len(g) == 1:
            cap *= (1 + float(g["pnl"].iloc[0]) / 100.0)
        else:
            r1 = float(g["pnl"].iloc[0]) / 100.0
            r2 = float(g["pnl"].iloc[1]) / 100.0
            cap = cap * (0.5 * (1 + r1) + 0.5 * (1 + r2))
    return (cap / 50000.0 - 1) * 100

combo = {}
for y in (2025, 2026):
    p_all = load_pool(str(y), False)
    p_w2 = load_pool(str(y), True)
    daycount = p_w2.groupby("entry").size()
    combo[str(y)] = {
        "n_signals": {"all": len(p_all), "w2": len(p_w2)},
        "days_w2": int(len(daycount)),
        "days_1sig": int((daycount == 1).sum()),
        "days_2sig": int((daycount >= 2).sum()),
        "seeds_all": [round(run([p_all], s), 1) for s in range(20)],
        "seeds_w2": [round(run([p_w2], s), 1) for s in range(20)],
    }
seeds2_all = []
seeds2_w2 = []
for s in range(20):
    tot_all = tot_w2 = 1.0
    for y in (2025, 2026):
        p_all = load_pool(str(y), False)
        p_w2 = load_pool(str(y), True)
        tot_all *= (1 + run([p_all], s) / 100)
        tot_w2 *= (1 + run([p_w2], s) / 100)
    seeds2_all.append(round((tot_all - 1) * 100, 1))
    seeds2_w2.append(round((tot_w2 - 1) * 100, 1))
combo["2y_seeds"] = {"all": seeds2_all, "w2": seeds2_w2}

def seed_summary(v):
    v = np.asarray(v, float)
    return {"min": round(float(v.min()), 1), "p25": round(float(np.percentile(v, 25)), 1),
            "median": round(float(np.median(v)), 1), "p75": round(float(np.percentile(v, 75)), 1),
            "max": round(float(v.max()), 1), "mean": round(float(v.mean()), 1),
            "neg_count": int((v < 0).sum()), "zeroed_count": int((v <= -99.9).sum())}
combo["2y_summary"] = {"all": seed_summary(combo["2y_seeds"]["all"]), "w2": seed_summary(combo["2y_seeds"]["w2"])}

d4 = dfA
res["extremes"] = {
    "top5": d4.nlargest(5, "pnl_nt")[["year", "sym", "t1", "pnl_nt", "gap_p10c"]].to_dict("records"),
    "bot5": d4.nsmallest(5, "pnl_nt")[["year", "sym", "t1", "pnl_nt", "gap_p10c"]].to_dict("records"),
    "n_gt15": int((d4["pnl_nt"] > 15).sum()), "n_lt15": int((d4["pnl_nt"] < -15).sum()),
    "n_gt20": int((d4["pnl_nt"] > 20).sum()), "n_lt20": int((d4["pnl_nt"] < -20).sum()),
}

import re
def stock_of(fname):
    return not re.match(r"^(399|5|15|16)", fname)
surv = {}
for y in years:
    d = Path(rf"F:/WorkBuddyItem/a股分钟线/parquet_qfq_{y}")
    files = [f.stem for f in d.glob("*.parquet")] if d.exists() else []
    surv[str(y)] = {"files": len(files), "stock_files": sum(1 for f in files if stock_of(f))}
res["survivorship"] = surv
g23 = load_signals(2023)
syms23 = set(g23["sym"])
d26 = Path(r"F:/WorkBuddyItem/a股分钟线/parquet_qfq_2026")
files26 = {f.stem for f in d26.glob("*.parquet")} if d26.exists() else set()
missing26 = sorted(s for s in syms23 if s not in files26)
res["survivorship"]["2023_5p_syms_missing_in_2026"] = missing26
res["survivorship"]["2023_5p_n"] = len(syms23)

res["sample10"] = dfA.head(10)[["year", "sym", "t1", "lb", "pnl_nt", "pnl_open_re", "pnl_p10c", "gap_p10c"]].to_dict("records")

json.dump(res, open(OUT / "_review_w2_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("OK saved. keep rows:", len(dfA))
print("repro:", json.dumps(repro, ensure_ascii=False))
print("reconcil:", json.dumps(res["reconcil"], ensure_ascii=False))
for v in variants:
    st = res["signal_stats"][v]
    print(v, {y: (st["per_year"][y]["n"], st["per_year"][y]["mean"], st["per_year"][y]["t"]) for y in years},
          "2526:", st["c2526"]["n"], st["c2526"]["mean"], st["c2526"]["t"], "clust:", st["c2526"]["clust_t"],
          "4y:", st["c4y"]["n"], st["c4y"]["mean"], st["c4y"]["t"], "clust:", st["c4y"]["clust_t"])
print("gap:", json.dumps(res["gap"], ensure_ascii=False))
print("months:", res["months"]["n_months"], res["months"]["pos"], "binom p:", res["months"]["binom_p_one_sided"])
print("combo 2y:", json.dumps(combo["2y_summary"], ensure_ascii=False))
print("extremes:", json.dumps(res["extremes"], ensure_ascii=False))
print("surv:", json.dumps(res["survivorship"], ensure_ascii=False))
