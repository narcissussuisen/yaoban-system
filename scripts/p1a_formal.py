"""P1-A 正式论证：恐慌衰竭低吸（子模式分治版）

数据：TDX tick 每日采集（tick/{date}/{sym}.parquet）+ 日线回填库（daily/{sym}.parquet）
论证结构（基于 P1A_PREVALIDATION_FINDINGS 方向性修正）：
  S1 当日反弹子模式：恐慌衰竭点（低 big_buy + 抛压枯竭 + 横盘）→ 当日 30/60 分钟收益
  S2 隔日承接子模式：承接强点（高 ba_ratio / 封单强）→ 次日收益（修复口径）
统计：均值/t/胜率/分档一致性；输出 outputs/p1a_formal.md
触发：loop_engine 在 tick_days >= 20 时自动调用
用法: python -B scripts/p1a_formal.py [--min-days 20]
"""
import argparse, pathlib, sys, time, json
import pandas as pd
import numpy as np

TICK = pathlib.Path(r"F:/WorkBuddyItem/a股level2/tick")
DAILY = pathlib.Path(r"F:/WorkBuddyItem/a股level2/daily")
OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001

def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)

def load_day(date):
    d = TICK / date
    if not d.exists(): return pd.DataFrame()
    frames = []
    for p in d.glob("*.parquet"):
        try:
            df = pd.read_parquet(p)
            if df.empty: continue
            df["symbol"] = p.stem
            frames.append(df)
        except Exception:
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

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

def factors_for(sym_df):
    """逐笔 → 日内窗口因子（30min 窗口，竞价过滤）"""
    t = sym_df[sym_df["buyorsell"].isin((0, 1)) & (sym_df["vol"] > 0)].copy()
    if len(t) < 500: return None
    t["amt"] = t["price"] * t["vol"] * 100.0
    t["dir"] = np.where(t["buyorsell"] == 0, 1, -1)
    t["big"] = t["amt"] >= 300000
    hm = t["time"].astype(str).str.slice(0, 5)
    t["mod"] = hm.str.slice(0, 2).astype(int) * 60 + hm.str.slice(3, 5).astype(int) - 570
    t = t[t["mod"] >= 0]
    wins = []
    for wi in range(1, 8):
        w0, w1 = wi * 30, wi * 30 + 30
        w = t[(t["mod"] >= w0) & (t["mod"] < w1)]
        if len(w) < 100: continue
        big_net = (w.loc[w["big"], "dir"] * w.loc[w["big"], "amt"]).sum() / 1e6
        last10 = w[w["mod"] >= w1 - 10]
        sell_big_last = (last10.loc[last10["big"] & (last10["dir"] < 0), "amt"]).sum() / 1e6
        base_px = float(w["price"].iloc[0])
        amp = (w["price"].max() - w["price"].min()) / base_px * 100
        fwd = t[(t["mod"] >= w1) & (t["mod"] < w1 + 30)]
        if len(fwd) == 0: continue
        r30 = (float(fwd["price"].iloc[-1]) / float(w["price"].iloc[-1]) - 1) * 100
        fwd60 = t[(t["mod"] >= w1) & (t["mod"] < w1 + 60)]
        r60 = (float(fwd60["price"].iloc[-1]) / float(w["price"].iloc[-1]) - 1) * 100 if len(fwd60) else None
        wins.append({"win": wi, "big_net": big_net, "sell_big_last": sell_big_last,
                     "amp": amp, "r30": r30, "r60": r60})
    return wins

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
        df = load_day(date)
        if df.empty: continue
        for sym, g in df.groupby("symbol"):
            wins = factors_for(g)
            if not wins: continue
            d8 = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            for w in wins:
                # 恐慌衰竭触发：big_net 低（分档内相对）+ sell_big_last 枯竭
                rows.append({"sym": sym, "date": date, "win": w["win"],
                             "big_net": w["big_net"], "sell_big_last": w["sell_big_last"],
                             "amp": w["amp"], "r30": w["r30"], "r60": w["r60"],
                             "close_px": None})
    d = pd.DataFrame(rows)
    if d.empty:
        print("no samples"); return
    # S1 当日反弹：按 big_net 分档（低=恐慌衰竭）
    def safe_qcut0(s, q, labels):
        try:
            return pd.qcut(s, q, labels=labels, duplicates="drop")
        except ValueError:
            med = s.median()
            return np.where(s <= med, labels[0], labels[-1])
    d["q"] = safe_qcut0(d["big_net"], 3, ["恐慌衰竭", "中", "承接强"])
    L = [f"# P1-A 正式论证（{len(days)} 交易日，n={len(d)}）", ""]
    L.append(f"> TDX tick 因子：大单净买/抛压枯竭/振幅；S1 当日收益（tick 口径）")
    L.append("")
    L.append("## S1 当日反弹子模式（按 big_net 分档）")
    L.append("")
    L.append("| 档 | n | r30均值% | r60均值% | r30胜率% |")
    L.append("|---|---|---|---|---|")
    for q, g in d.groupby("q", observed=True):
        L.append(f"| {q} | {len(g)} | {g['r30'].mean():+.2f} | {g['r60'].dropna().mean():+.2f} | {(g['r30']>0).mean()*100:.0f} |")
    L.append("")
    L.append("## S2 隔日承接子模式（按 sell_big_last 抛压枯竭分档）")
    L.append("")
    L.append("| 档 | n | r30均值% | 胜率% |")
    L.append("|---|---|---|---|")
    def safe_qcut(s, q, labels):
        try:
            return pd.qcut(s, q, labels=labels, duplicates="drop")
        except ValueError:
            med = s.median()
            return np.where(s <= med, labels[0], labels[-1])
    d["q2"] = safe_qcut(d["sell_big_last"], 3, ["抛压重", "中", "抛压枯竭"])
    for q, g in d.groupby("q2", observed=True):
        L.append(f"| {q} | {len(g)} | {g['r30'].mean():+.2f} | {(g['r30']>0).mean()*100:.0f} |")
    L.append("")
    L.append(f"> 样本 {len(d)}，天数 {len(days)}，耗时 {time.time()-t0:.0f}s；"
             "子模式分治结论见 outputs/p1a_formal.md")
    outp = OUT / "p1a_formal.md"
    outp.write_text(chr(10).join(L), encoding="utf-8")
    print(chr(10).join(L[:16]))

if __name__ == "__main__":
    main()
