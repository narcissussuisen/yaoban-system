"""调试：单笔 manage_day fills 明细"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pandas as pd
from data.qfq_store import QFQStore
from core.sell import manage_day, limit_price

y = "2026"
cand = pd.read_csv(f"outputs/pullback_b2_{y}_raw.csv", dtype={"sym": str})
sub = cand[cand["p_b2"].notna()].sample(n=3, random_state=1)
qfq = QFQStore(y)
for _, r in sub.iterrows():
    sym, ed = r["sym"], r["ed"]
    daily = qfq.get_stock(sym)
    dates = [x[1] for x in daily]
    if ed not in dates: continue
    i = dates.index(ed)
    if i + 3 >= len(daily): continue
    entry_px = float(daily[i][2])
    print(f"=== {sym} {ed} entry={entry_px:.2f}")
    qty = 10000
    low_track = entry_px
    prev_c = float(daily[i][5])
    for k in range(1, 4):
        d = dates[i + k]
        mrows = qfq.get_minute(sym, start=d, end=d)
        if not mrows:
            print(f"  day{k} {d}: no minute"); break
        day_df = pd.DataFrame(mrows, columns=["symbol","freq","ts","open","high","low","close","volume","amount"])
        res = manage_day(day_df, prev_c, qty, stop_px=entry_px*0.95, low_track=low_track,
                         limit_px=limit_price(prev_c, sym))
        print(f"  day{k} {d}: fills={len(res['fills'])} qty_end={res['qty']} t_round={res['t_round']}")
        for f in res["fills"][:8]:
            print(f"    {f['ts']} {f['side']} qty={f['qty']} px={f['px']:.2f} reason={f['reason']}")
        qty = res["qty"]; low_track = res["low_track"]; prev_c = float(day_df["close"].iloc[-1])
        if qty <= 0: break
