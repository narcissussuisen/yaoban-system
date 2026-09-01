"""P1-A 正式回测管线：恐慌衰竭低吸（TDX tick 口径）

数据积累 >=20 交易日后由 loop_engine 自动触发本脚本。

流程（对每个 (sym, date)）：
1. 从 tick/{date}/{sym}.parquet 读逐笔（已含方向）
2. 计算日内窗口因子（30min 窗口）：
   - big_buy_net：大单净买（30万+，窗口内）
   - exhaustion：抛压衰竭（卖盘大单枯竭 = 窗口末 10 分钟大单卖出额 < 阈值）
   - narrow：窄幅横盘（窗口内价格振幅 < 阈值）
3. B 点定义（恐慌衰竭）：big_buy_net 低 + exhaustion 触发 + narrow 成立
4. 触发后收益：r30/r60/当日收盘（用 tick 价格序列）
5. 汇总：分位/分组对比（低衰竭 vs 高衰竭）

用法: python -B scripts/p1a_pipeline.py --start 20260826 --end 20260826
"""
import argparse, pathlib, sys, time
import pandas as pd
import numpy as np

TICK = pathlib.Path(r"F:/WorkBuddyItem/a股level2/tick")

def load_day(date: str) -> pd.DataFrame:
    d = TICK / date
    if not d.exists():
        return pd.DataFrame()
    frames = []
    for p in d.glob("*.parquet"):
        try:
            df = pd.read_parquet(p)
            if df.empty:
                continue
            df["symbol"] = p.stem
            frames.append(df)
        except Exception as e:
            print(f"  skip {p.name}: {e}")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)

def minute_from_tick(df: pd.DataFrame) -> pd.DataFrame:
    """逐笔 → 1 分钟 OHLCV（竞价过滤 + 方向聚合）"""
    d = df[df["buyorsell"].isin((0, 1)) & (df["vol"] > 0)].copy()
    if d.empty:
        return pd.DataFrame()
    d["amt"] = d["price"] * d["vol"] * 100.0
    d["dir"] = np.where(d["buyorsell"] == 0, 1, -1)
    d["min"] = d["time"].astype(str).str.slice(0, 5)  # HH:MM
    d["ts"] = d["date"] + " " + d["min"]
    g = d.groupby("ts")
    out = pd.DataFrame({
        "open": g["price"].first(),
        "high": g["price"].max(),
        "low": g["price"].min(),
        "close": g["price"].last(),
        "volume": g["vol"].sum(),
        "amount": g["amt"].sum(),
        "big_net": g.apply(lambda x: (x.loc[x["amt"] >= 300000, "dir"] * x.loc[x["amt"] >= 300000, "amt"]).sum() / 1e6, include_groups=False),
    })
    out["ts"] = out.index
    return out.reset_index(drop=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    args = ap.parse_args()
    t0 = time.time()
    # 收集交易日目录
    days = sorted(p.name for p in TICK.iterdir() if p.is_dir())
    days = [d for d in days if args.start <= d <= args.end]
    if not days:
        print("no tick days in range")
        return
    print(f"days: {days}", flush=True)
    all_rows = []
    for date in days:
        df = load_day(date)
        if df.empty:
            continue
        df["date"] = date
        for sym, g in df.groupby("symbol"):
            # 逐笔级（保留精度）：竞价过滤 + 方向
            t = g[g["buyorsell"].isin((0, 1)) & (g["vol"] > 0)].copy()
            if len(t) < 500:
                continue
            t["amt"] = t["price"] * t["vol"] * 100.0
            t["dir"] = np.where(t["buyorsell"] == 0, 1, -1)
            t["big"] = t["amt"] >= 300000
            # 时间 → 分钟序号（09:30=0）
            hm = t["time"].astype(str).str.slice(0, 5)
            t["min_of_day"] = hm.str.slice(0, 2).astype(int) * 60 + hm.str.slice(3, 5).astype(int) - 570
            t = t[t["min_of_day"] >= 0]  # 09:30 起
            # 30 分钟窗口（wi=1..7 → 10:00 起每 30 分钟）
            for wi in range(1, 8):
                w0 = wi * 30
                w1 = w0 + 30
                w = t[(t["min_of_day"] >= w0) & (t["min_of_day"] < w1)]
                if len(w) < 100:
                    continue
                big_net = (w.loc[w["big"], "dir"] * w.loc[w["big"], "amt"]).sum() / 1e6
                # 窗口末 10 分钟大单卖出（抛压衰竭信号）
                last10 = w[(w["min_of_day"] >= w1 - 10)]
                sell_big_last = (last10.loc[last10["big"] & (last10["dir"] < 0), "amt"]).sum() / 1e6
                # 价格横盘：窗口内高点/低点相对起点
                base_px = float(w["price"].iloc[0])
                amp = (w["price"].max() - w["price"].min()) / base_px * 100
                # 触发后 60 分钟收益（tick 末价）
                fwd = t[(t["min_of_day"] >= w1) & (t["min_of_day"] < w1 + 60)]
                if len(fwd) == 0:
                    continue
                r60 = (float(fwd["price"].iloc[-1]) / float(w["price"].iloc[-1]) - 1) * 100
                all_rows.append({"sym": sym, "date": date, "win": wi,
                                 "big_net": big_net, "sell_big_last": sell_big_last,
                                 "amp": amp, "r60": r60})
    d = pd.DataFrame(all_rows)
    if d.empty:
        print("no samples")
        return
    # 分档
    d["q"] = pd.qcut(d["big_net"], 3, labels=["低净买", "中", "高净买"])
    print(f"samples: {len(d)} in {time.time()-t0:.0f}s")
    print(d.groupby("q", observed=True).agg(n=("r60", "size"), r60=("r60", "mean")).to_string())
    out = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "p1a_pipeline_test.md"
    L = ["# P1-A 管线冒烟测试（TDX tick）", ""]
    L.append(f"> 天数 {days}，样本 {len(d)}")
    L.append("")
    L.append(d.groupby("q", observed=True).agg(n=("r60", "size"), r60=("r60", "mean")).to_string())
    out.write_text(chr(10).join(L), encoding="utf-8")

if __name__ == "__main__":
    main()
