"""评审补算 v2：幸存者修正 + 分月 + 组合细节 + keep/drop 差值"""
import sys, json, math, re
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(r"C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system")
OUT = BASE / "outputs"
sys.path.insert(0, str(BASE / "src"))

CACHE = json.load(open(OUT / "w2_cache.json", encoding="utf-8"))
res = {}

def load_signals(year):
    df = pd.read_csv(OUT / f"backtest_daban_{year}_raw.csv", dtype={"sym": str})
    return df[(df["lb"] >= 5) & ~df["yizi"]].dropna(subset=["pnl_nt"]).copy()

def stem_sym(fname):
    return fname.split(".")[0]

# ---- 幸存者：分年 parquet 标的数 + 5+ 标的存在性 ----
surv2 = {}
for y in (2023, 2024, 2025, 2026):
    d = Path(rf"F:/WorkBuddyItem/a股分钟线/parquet_qfq_{y}")
    files = [f.stem for f in d.glob("*.parquet")] if d.exists() else []
    syms = set(stem_sym(f) for f in files)
    surv2[str(y)] = {"n_files": len(files), "n_syms": len(syms)}
res["survivorship2"] = surv2

d26 = Path(r"F:/WorkBuddyItem/a股分钟线/parquet_qfq_2026")
syms26 = {stem_sym(f.stem) for f in d26.glob("*.parquet")}
for y in (2023, 2024):
    g5 = load_signals(y)
    syms = set(g5["sym"])
    miss = sorted(s for s in syms if s not in syms26)
    res[f"surv_{y}_5p_missing_in_2026"] = {"n_5p_syms": len(syms), "missing": miss}
    if miss:
        sub = g5[g5["sym"].isin(miss)]
        res[f"surv_{y}_5p_missing_detail"] = {
            "n_signals": len(sub), "mean_pnl_nt": round(float(sub["pnl_nt"].mean()), 3),
            "signals": sub[["sym","t1","lb","pnl_nt"]].to_dict("records")}

# ---- 分月 open 口径（复现 doc 16/22） ----
rows = []
for y in (2023, 2024, 2025, 2026):
    g = load_signals(y)
    for _, r in g.iterrows():
        w = CACHE.get(f"{y}|D|{r['sym']}|{r['t1']}")
        if w is False:
            rows.append({"year": y, "t1": r["t1"], "pnl_nt": float(r["pnl_nt"])})
dfM = pd.DataFrame(rows)
dfM["month"] = dfM["t1"].str[:7]
mons_open = []
for m, gg in dfM.groupby("month"):
    if len(gg) >= 5:
        mons_open.append((m, len(gg), float(gg["pnl_nt"].mean())))
pos_open = sum(1 for _,_,v in mons_open if v > 0)
res["months_open"] = {"n": len(mons_open), "pos": pos_open,
                      "neg_months": [m for m,n,v in mons_open if v <= 0]}

# ---- keep vs drop（open 口径） ----
diff = {}
def t_two(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    se = math.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
    return (a.mean() - b.mean()) / se if se > 0 else 0.0
for y in (2023, 2024, 2025, 2026):
    g = load_signals(y)
    vals = []
    for _, r in g.iterrows():
        w = CACHE.get(f"{y}|D|{r['sym']}|{r['t1']}")
        if w is not None:
            vals.append((w, float(r["pnl_nt"])))
    keep = [v for w, v in vals if w is False]
    drop = [v for w, v in vals if w is True]
    diff[str(y)] = {"keep_n": len(keep), "keep_mean": round(float(np.mean(keep)), 3),
                    "drop_n": len(drop), "drop_mean": round(float(np.mean(drop)), 3),
                    "diff_pp": round(float(np.mean(keep) - np.mean(drop)), 3),
                    "t_diff": round(t_two(keep, drop), 3)}
res["keep_vs_drop"] = diff

# ---- 组合细节（cache 快算） ----
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
    allf["prio"] = 0
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
seeds2_all, seeds2_w2 = [], []
for s in range(20):
    tot_all = tot_w2 = 1.0
    for y in (2025, 2026):
        p_all = load_pool(str(y), False)
        p_w2 = load_pool(str(y), True)
        tot_all *= (1 + run([p_all], s) / 100)
        tot_w2 *= (1 + run([p_w2], s) / 100)
    seeds2_all.append(round((tot_all - 1) * 100, 1))
    seeds2_w2.append(round((tot_w2 - 1) * 100, 1))
def seed_summary(v):
    v = np.asarray(v, float)
    return {"min": round(float(v.min()), 1), "p25": round(float(np.percentile(v, 25)), 1),
            "median": round(float(np.median(v)), 1), "p75": round(float(np.percentile(v, 75)), 1),
            "max": round(float(v.max()), 1), "mean": round(float(v.mean()), 1),
            "neg_count": int((v < 0).sum()), "zeroed_count": int((v <= -99.9).sum())}
res["combo"] = combo
res["combo_2y"] = {"all": seed_summary(seeds2_all), "w2": seed_summary(seeds2_w2)}
res["combo_2y_seeds"] = {"all": seeds2_all, "w2": seeds2_w2}

json.dump(res, open(OUT / "_review_w2_result2.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("survivorship2:", json.dumps(surv2, ensure_ascii=False))
for y in (2023, 2024):
    print(f"{y} missing:", json.dumps(res.get(f"surv_{y}_5p_missing_in_2026"), ensure_ascii=False))
    print(f"{y} missing detail:", json.dumps(res.get(f"surv_{y}_5p_missing_detail"), ensure_ascii=False))
print("months_open:", res["months_open"])
print("keep_vs_drop:", json.dumps(diff, ensure_ascii=False))
print("combo per year:", json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "seeds_all" and kk != "seeds_w2"} for k, v in combo.items()}, ensure_ascii=False))
print("combo_2y:", json.dumps(res["combo_2y"], ensure_ascii=False))
print("seeds_w2_2y:", seeds2_w2)
print("seeds_all_2y:", seeds2_all)
