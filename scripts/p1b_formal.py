"""P1-B 正式论证：5+ 板内选优（封板质量因子）

数据：TDX tick 每日采集（tick/{date}/{sym}.parquet 逐笔）+ quotes/{date}.parquet（收盘五档）
     + daily/{sym}.parquet（日线回填）
因子（信号日 T 收盘可得）：
  - seal_strength: 收盘封单强度 = 买一量 / 当日成交额（quotes 或 tick 末档）
  - first_seal: 首封时间（分钟线 high>=limit 首根，tick 聚合）
  - zhaban_cnt: 炸板次数（触及-回落-再封）
分层：因子分位 × 次日收益（修复口径）——选优层是否提升 5+ 池期望
触发：loop_engine 在 tick_days >= 20 时自动调用
用法: python -B scripts/p1b_formal.py [--min-days 20]
"""
import argparse, pathlib, sys, time
import pandas as pd
import numpy as np

TICK = pathlib.Path(r"F:/WorkBuddyItem/a股level2/tick")
QUOTES = pathlib.Path(r"F:/WorkBuddyItem/a股level2/quotes")
DAILY = pathlib.Path(r"F:/WorkBuddyItem/a股level2/daily")
OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001

def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)

def next_day_pnl(sym, date8, px):
    p = DAILY / f"{sym}.parquet"
    if not p.exists(): return None
    daily = pd.read_parquet(p).sort_values("date")
    dates = daily["date"].astype(str).tolist()
    if date8 not in dates: return None
    i = dates.index(date8)
    if i + 1 >= len(daily): return None
    r1 = daily.iloc[i + 1]
    op2, lo2, cl2 = float(r1["open"]), float(r1["low"]), float(r1["close"])
    stop = px * 0.95
    ex = (op2 if op2 <= stop else stop) if lo2 <= stop else cl2
    return (sell_net(ex) / buy_net(px) - 1) * 100

def limit_pct_of(sym):
    if sym.startswith(("300", "301", "688", "689")): return 0.20
    if sym.startswith(("4", "8")): return 0.30
    return 0.10

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=20)
    args = ap.parse_args()
    days = sorted(p.name for p in TICK.iterdir() if p.is_dir())
    if len(days) < args.min_days:
        print(f"数据不足: {len(days)}/{args.min_days} 交易日——等待积累（loop_engine 自动重试）")
        return
    t0 = time.time()
    rows = []
    for date in days:
        d8 = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        tick_dir = TICK / date
        for p in sorted(tick_dir.glob("*.parquet")):
            sym = p.stem
            try:
                df = pd.read_parquet(p)
            except Exception:
                continue
            if df.empty: continue
            t = df[df["buyorsell"].isin((0, 1)) & (df["vol"] > 0)].copy()
            if len(t) < 200: continue
            t["amt"] = t["price"] * t["vol"] * 100.0
            # 首封时间（high>=limit 首根；limit 用昨收——从 daily 库取前一日）
            dp = DAILY / f"{sym}.parquet"
            prev_close = None
            if dp.exists():
                dd = pd.read_parquet(dp).sort_values("date")
                dd8 = dd[dd["date"].astype(str) < d8]
                if len(dd8):
                    prev_close = float(dd8["close"].iloc[-1])
            if not prev_close or prev_close <= 0: continue
            lpx = round(prev_close * (1 + limit_pct_of(sym)), 2)
            first_seal = None
            for _, r in t.iterrows():
                if r["price"] >= lpx - 0.01:
                    first_seal = str(r["time"])
                    break
            # 炸板次数
            seals = 0
            above = False
            for px in t["price"]:
                if px >= lpx - 0.01:
                    if not above: seals += 1
                    above = True
                else:
                    above = False
            close_px = float(t["price"].iloc[-1])
            is_zt = close_px >= lpx - 0.005
            if not is_zt: continue
            # 封单强度：收盘末档买一量/成交额（quotes 或 tick 内计算）
            amt = float(t["amt"].sum())
            seal_strength = 0.0
            qf = QUOTES / f"{date}.parquet"
            if qf.exists():
                q = pd.read_parquet(qf)
                qq = q[q["code"] == sym]
                if len(qq):
                    seal_strength = float(qq["bid_vol1"].iloc[0]) * 100 / amt if amt > 0 else 0
            pnl = next_day_pnl(sym, d8, close_px)
            rows.append({"sym": sym, "date": date, "first_seal": first_seal, "seals": seals,
                         "seal_strength": seal_strength, "pnl": pnl})
    d = pd.DataFrame(rows)
    if d.empty:
        print("no samples"); return
    L = [f"# P1-B 正式论证（{len(days)} 交易日，涨停样本 {len(d)}）", ""]
    L.append("> 因子：首封时间/炸板次数/封单强度(买一量×100/成交额)；次日=修复口径")
    L.append("")
    L.append("## 按封单强度分档（次日收益）")
    L.append("")
    L.append("| 档 | n | 次日均值% | 胜率% |")
    L.append("|---|---|---|---|")
    dd = d.dropna(subset=["pnl"])
    if len(dd) >= 10 and dd["seal_strength"].nunique() > 1:
        dd["q"] = pd.qcut(dd["seal_strength"], 3, labels=["低", "中", "高"], duplicates="drop")
        for q, g in dd.groupby("q", observed=True):
            L.append(f"| {q} | {len(g)} | {g['pnl'].mean():+.2f} | {(g['pnl']>0).mean()*100:.0f} |")
    L.append("")
    L.append("## 按首封时间")
    L.append("")
    L.append("| 首封 | n | 次日均值% | 胜率% |")
    L.append("|---|---|---|---|")
    if dd["first_seal"].notna().any():
        dd["early"] = dd["first_seal"].apply(lambda x: "早封(≤10:30)" if x and x <= "10:30" else "晚封")
        for q, g in dd.groupby("early"):
            L.append(f"| {q} | {len(g)} | {g['pnl'].mean():+.2f} | {(g['pnl']>0).mean()*100:.0f} |")
    L.append("")
    L.append(f"> 样本 {len(d)}，天数 {len(days)}，耗时 {time.time()-t0:.0f}s")
    outp = OUT / "p1b_formal.md"
    outp.write_text(chr(10).join(L), encoding="utf-8")
    print(chr(10).join(L[:14]))

if __name__ == "__main__":
    main()
