"""P1-A 预验证：L2 恐慌衰竭因子 × 修复后次日收益

用旧 L2 三天（8/21/24/25）B2 触发样本（l2_quality_score.py 同管线），
把 B2 触发日的质量分与 F 盘修复后日线次日收益（T+1 收盘/-5%止损）对齐。
"""
import pandas as pd, numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

L2 = Path(r"F:/WorkBuddyItem/a股 level2/parquet")
OUT = Path(__file__).resolve().parent.parent / "outputs"
DATES = ["20260821", "20260824", "20260825"]
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001

def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)

def ts_to_int(hhmm): 
    h, m = hhmm.split(":")
    return int(h) * 10000000 + int(m) * 100000

def minute_from_hq(hq, date):
    h = hq[hq["成交价"] > 0].copy()
    if h.empty: return pd.DataFrame()
    h["min"] = h["时间"] // 100000
    h["HH"] = h["min"] // 100
    h["MM"] = h["min"] % 100
    d8 = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    h["ts"] = d8 + " " + h["HH"].apply(lambda x: f"{int(x):02d}") + ":" + h["MM"].apply(lambda x: f"{int(x):02d}")
    g = h.groupby("ts")
    out = pd.DataFrame({"open": g["成交价"].first()/10000.0, "high": g["成交价"].max()/10000.0,
                        "low": g["成交价"].min()/10000.0, "close": g["成交价"].last()/10000.0,
                        "volume": g["成交量"].sum(), "amount": g["成交额"].sum()})
    out["ts"] = out.index
    return out.reset_index(drop=True)

def quality(cj, wt, hq, trig_int):
    out = {"big_buy": 0.0, "bid_net": 0.0, "ba_ratio": 1.0}
    if cj is not None and len(cj):
        c = cj[(cj["时间"] >= trig_int - 30000000) & (cj["时间"] <= trig_int)]
        if len(c):
            c = c.copy()
            c["amt"] = c["成交价格"]/10000.0 * c["成交数量"]
            big = c[c["amt"] >= 300000]
            out["big_buy"] = (big[big["BS标志"]=="B"]["amt"].sum() - big[big["BS标志"]=="S"]["amt"].sum())/1e6
    if wt is not None and len(wt):
        w = wt[(wt["时间"] >= trig_int - 30000000) & (wt["时间"] <= trig_int)]
        if len(w):
            adds = w[w["委托类型"]=="A"]; cancels = w[w["委托类型"]=="D"]
            out["bid_net"] = ((adds[adds["委托代码"]=="B"]["委托数量"].sum() - cancels[cancels["委托代码"]=="B"]["委托数量"].sum())/1e4)
    if hq is not None and len(hq):
        h = hq[hq["时间"] <= trig_int]
        if len(h):
            last = h.iloc[-1]
            ba = float(last.get("叫买总量") or 0); sa = float(last.get("叫卖总量") or 0)
            out["ba_ratio"] = ba/sa if sa > 0 else 2.0
    return out

DAILY_DIR = Path(r"F:/WorkBuddyItem/a股level2/daily")

def load_daily(sym):
    """日线：daily/ 回填库优先（TDX），F 盘兜底（QFQStore）"""
    p = DAILY_DIR / f"{sym}.parquet"
    if p.exists():
        df = pd.read_parquet(p)
        return df.sort_values("date"), True
    qfq = QFQStore("2026")
    rows = qfq.get_stock(sym)
    if not rows:
        return None, False
    return pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"]), False

def next_day_pnl_fixed(sym, b2_date, b2_px):
    """修复后口径次日收益：T+1 收盘 / -5% 止损（LOW 判定 + 开盘跳空按开盘价）"""
    daily, _ = load_daily(sym)
    if daily is None or len(daily) < 2:
        return None
    dates = daily["date"].astype(str).tolist()
    if b2_date not in dates:
        return None
    i = dates.index(b2_date)
    if i + 1 >= len(daily):
        return None
    r1 = daily.iloc[i + 1]
    op2, lo2, cl2 = float(r1["open"]), float(r1["low"]), float(r1["close"])
    stop = b2_px * 0.95
    if lo2 <= stop:
        ex = op2 if op2 <= stop else stop
    else:
        ex = cl2
    return (sell_net(ex) / buy_net(b2_px) - 1) * 100

# 主流程
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from core.intraday import detect_b_point

rows_out = []
for date in DATES:
    d = L2 / date
    if not d.exists(): continue
    syms = sorted({f.name.split("_")[0] for f in d.glob("*_行情.parquet")})
    for sym in syms:
        try:
            hq = pd.read_parquet(d / f"{sym}_行情.parquet")
            cj = pd.read_parquet(d / f"{sym}_逐笔成交.parquet")
            wt = pd.read_parquet(d / f"{sym}_逐笔委托.parquet")
        except Exception:
            continue
        m1 = minute_from_hq(hq, date)
        if len(m1) < 30: continue
        pc = float(hq["前收盘"].iloc[0]) / 10000.0 if len(hq) and hq["前收盘"].iloc[0] > 0 else None
        if not pc: continue
        df1 = m1.copy()
        pts = detect_b_point(df1, prev_close=pc)
        if pts.empty: continue
        b2 = pts[pts["kind"] == "B2"]
        if b2.empty: continue
        r0 = b2.iloc[0]
        trig = ts_to_int(r0["ts"][11:16])
        qs = quality(cj, wt, hq, trig)
        b2px = float(r0["price"])
        b2_date_iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        rows_out.append({"sym": sym, "date": date, "b2_date": b2_date_iso, "b2_px": b2px, **qs})

df = pd.DataFrame(rows_out)
print(f"B2 样本: {len(df)}")
if df.empty:
    sys.exit(0)

# 对齐修复后日线（daily/ 回填库 + F 盘兜底）
df["next_pnl"] = df.apply(lambda r: next_day_pnl_fixed(r["sym"], r["b2_date"], r["b2_px"]), axis=1)
d = df.dropna(subset=["next_pnl"])
print(f"对齐修复后日线: {len(d)}")
d["score"] = (d["big_buy"].rank(pct=True) + d["bid_net"].rank(pct=True) + d["ba_ratio"].rank(pct=True)) / 3
L = ["# P1-A 预验证：恐慌衰竭 × 修复后次日收益（L2 三天）", ""]
L.append(f"> B2 样本 {len(df)}，对齐修复后日线 {len(d)}；次日=T+1 收盘/-5% 止损（修复口径）")
L.append("")
L.append("| 质量分档 | n | 次日均值% | 胜率% |")
L.append("|---|---|---|---|")
for lo, hi, lab in ((0, 0.33, "低分位(恐慌衰竭)"), (0.33, 0.66, "中分位"), (0.66, 1.01, "高分位(承接强)")):
    g = d[(d["score"] >= lo) & (d["score"] < hi)]
    if len(g):
        L.append(f"| {lab} | {len(g)} | {g['next_pnl'].mean():+.2f} | {(g['next_pnl']>0).mean()*100:.0f} |")
L.append("")
L.append("| 因子 | 低档次日% | 高档次日% | 差值pp |")
L.append("|---|---|---|---|")
for col in ("big_buy", "bid_net", "ba_ratio"):
    med = d[col].median()
    lo_g = d[d[col] <= med]["next_pnl"].dropna()
    hi_g = d[d[col] > med]["next_pnl"].dropna()
    if len(lo_g) and len(hi_g):
        L.append(f"| {col} | {lo_g.mean():+.2f} | {hi_g.mean():+.2f} | {hi_g.mean()-lo_g.mean():+.2f} |")
open(OUT / "p1a_prevalidation.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
