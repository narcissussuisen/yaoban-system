"""P1-B 预验证：封板质量 × 修复后次日收益（旧 L2 三天）

旧 L2 行情快照含五档（叫买量1-5/叫卖量1-5）→ 封单强度 = 买一量/流通股本近似
对涨停标的（8/21/24/25），收盘封单强度 × 次日收益（daily回填库，修复口径）
"""
import pandas as pd, numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore
from core.sell import limit_pct_of

L2 = Path(r"F:/WorkBuddyItem/a股 level2/parquet")
DAILY = Path(r"F:/WorkBuddyItem/a股level2/daily")
OUT = Path(__file__).resolve().parent.parent / "outputs"
DATES = ["20260821", "20260824", "20260825"]
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001

def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)

def next_day_pnl(sym, b2_date, b2_px):
    p = DAILY / f"{sym}.parquet"
    if not p.exists(): return None
    daily = pd.read_parquet(p).sort_values("date")
    dates = daily["date"].astype(str).tolist()
    if b2_date not in dates: return None
    i = dates.index(b2_date)
    if i + 1 >= len(daily): return None
    r1 = daily.iloc[i + 1]
    op2, lo2, cl2 = float(r1["open"]), float(r1["low"]), float(r1["close"])
    stop = b2_px * 0.95
    ex = (op2 if op2 <= stop else stop) if lo2 <= stop else cl2
    return (sell_net(ex) / buy_net(b2_px) - 1) * 100

rows = []
for date in DATES:
    d = L2 / date
    if not d.exists(): continue
    d8 = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    for f in d.glob("*_行情.parquet"):
        sym = f.name.split("_")[0]
        try:
            hq = pd.read_parquet(f)
        except Exception:
            continue
        if hq.empty: continue
        h = hq[hq["成交价"] > 0].copy()
        if h.empty: continue
        pc = float(hq["前收盘"].iloc[0]) / 10000.0
        if pc <= 0: continue
        lpx = round(pc * (1 + limit_pct_of(sym)), 2)
        # 收盘封单：最后一根的买一量（叫买量1）
        last = h.iloc[-1]
        bid1 = float(last.get("申买量1") or 0)  # 单位=股
        close_px = float(h["成交价"].iloc[-1]) / 10000.0
        is_zt = close_px >= lpx - 0.005
        if not is_zt:
            continue
        # 流通股本近似：当日成交额/换手率不可得 → 用成交额和封单比做相对强度
        amt = float(h["成交额"].sum())
        seal_ratio = bid1 / amt if amt > 0 else 0  # 封单/成交额（相对强度代理）
        pnl = next_day_pnl(sym, d8, close_px)
        rows.append({"sym": sym, "date": date, "seal_ratio": seal_ratio, "bid1": bid1,
                     "pnl": pnl, "close": close_px})

df = pd.DataFrame(rows)
print(f"涨停样本: {len(df)}, 有次日: {df['pnl'].notna().sum()}")
if len(df) >= 10:
    d = df.dropna(subset=["pnl"])
    med = d["seal_ratio"].median()
    lo_g = d[d["seal_ratio"] <= med]["pnl"]
    hi_g = d[d["seal_ratio"] > med]["pnl"]
    L = ["# P1-B 预验证：封单强度 × 次日收益（旧 L2 三天）", ""]
    L.append(f"> 涨停样本 {len(d)}（有次日收益），封单强度=收盘买一量/当日成交额")
    L.append("")
    L.append("| 封单强度 | n | 次日均值% | 胜率% |")
    L.append("|---|---|---|---|")
    L.append(f"| 低（≤中位） | {len(lo_g)} | {lo_g.mean():+.2f} | {(lo_g>0).mean()*100:.0f} |")
    L.append(f"| 高（>中位） | {len(hi_g)} | {hi_g.mean():+.2f} | {(hi_g>0).mean()*100:.0f} |")
    L.append("")
    L.append(f"差值: {hi_g.mean()-lo_g.mean():+.2f}pp")
    open(OUT / "p1b_prevalidation.md", "w", encoding="utf-8").write(chr(10).join(L))
    print(chr(10).join(L))