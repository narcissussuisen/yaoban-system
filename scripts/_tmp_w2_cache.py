"""W2 缓存 v2：按股票读一次全量分钟，按日切片（大幅减少 I/O）"""
import pandas as pd, numpy as np, json
from pathlib import Path
import sys, time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"
t0 = time.time()

cache = {}
for y in (2023, 2024, 2025, 2026):
    qfq = QFQStore(str(y))
    # 收集需要计算的 (sym, date) 集合，按 sym 分组
    by_sym = {}
    db = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g5 = db[(db["lb"] >= 5) & ~db["yizi"]].dropna(subset=["pnl_nt"])
    for _, r in g5.iterrows():
        by_sym.setdefault(r["sym"], set()).add(("D", r["t1"]))
    mf = OUT / f"backtest_market_{y}_t1raw.csv"
    if mf.exists():
        m = pd.read_csv(mf, dtype={"sym": str})
        for _, r in m.iterrows():
            by_sym.setdefault(r["sym"], set()).add(("M", r["ed"]))
    print(f"{y}: {len(by_sym)} symbols", flush=True)
    for sym, items in by_sym.items():
        daily = qfq.get_stock(sym)
        if not daily:
            continue
        dates = [x[1] for x in daily]
        # 昨收/前低映射
        prev_low = {}
        for i in range(1, len(daily)):
            prev_low[daily[i][1]] = float(daily[i-1][3])
        # 该股全部需要的日期
        need_dates = {d for _, d in items}
        mrows_all = qfq.get_minute(sym)  # 一次读全年
        if not mrows_all:
            continue
        mdf = pd.DataFrame(mrows_all, columns=["symbol","freq","ts","open","high","low","close","volume","amount"])
        mdf["date"] = mdf["ts"].str[:10]
        for kind, ed in items:
            if ed not in prev_low or ed not in need_dates:
                continue
            day = mdf[mdf["date"] == ed]
            if len(day) < 30:
                continue
            low30 = float(day["low"].iloc[:30].min())
            cache[f"{y}|{kind}|{sym}|{ed}"] = bool(low30 < prev_low[ed])
    print(f"  {y} done in {time.time()-t0:.0f}s, cache={len(cache)}", flush=True)

json.dump(cache, open(OUT / "w2_cache.json", "w"))
print(f"total cached: {len(cache)} in {time.time()-t0:.0f}s")
