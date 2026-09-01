"""TDX 历史日线回填：补全旧 L2 标的的 8/24-8/26 日线（P1-A 预验证前置）

旧 L2 三天（8/21/24/25）B2 样本的次日收益需要日线；F 盘只到 8/21。
用 TDX get_security_bars(9=日K) 拉历史日线补全到 daily/ 库。
用法: python -B scripts/fill_daily_history.py [--symbols ...] [--days 5]
"""
import argparse, pathlib, sys, time
import pandas as pd
from pytdx.hq import TdxHq_API

ROOT = pathlib.Path(r"F:/WorkBuddyItem/a股level2")
SERVERS = [("115.238.56.198", 7709), ("115.238.90.165", 7709)]

def market_of(sym): return 1 if sym[0] in ("6", "9", "5") else 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="逗号分隔；缺省=旧L2全部标的")
    ap.add_argument("--days", type=int, default=10, help="拉最近 N 个交易日")
    args = ap.parse_args()
    if args.symbols:
        syms = [s.strip().zfill(6) for s in args.symbols.split(",") if s.strip()]
    else:
        # 旧 L2 全部标的
        L2 = pathlib.Path(r"F:/WorkBuddyItem/a股 level2/parquet")
        syms = set()
        for d in L2.iterdir():
            if d.is_dir():
                for f in d.glob("*_行情.parquet"):
                    syms.add(f.name.split("_")[0])
        syms = sorted(syms)
    print(f"回填 {len(syms)} 只 × {args.days} 天", flush=True)
    api = TdxHq_API(heartbeat=False)
    ok = False
    for host, port in SERVERS:
        if api.connect(host, port, time_out=8):
            ok = True
            print(f"connected {host}:{port}")
            break
    if not ok:
        print("连接失败")
        return
    d_dir = ROOT / "daily"
    d_dir.mkdir(parents=True, exist_ok=True)
    n_ok = 0
    t0 = time.time()
    for k, sym in enumerate(syms):
        try:
            # 分页拉最近 N 天（每页 800）
            bars_all = []
            for start in range(0, args.days, 800):
                bars = api.get_security_bars(9, market_of(sym), sym, start, min(800, args.days - start))
                if not bars:
                    break
                bars_all.extend(bars)
                if len(bars) < 800:
                    break
            if not bars_all:
                continue
            rows = []
            for b in bars_all:
                dt = f"{b['year']:04d}-{b['month']:02d}-{b['day']:02d}"
                rows.append({"symbol": sym, "date": dt,
                             "open": b["open"], "high": b["high"], "low": b["low"],
                             "close": b["close"], "volume": b["vol"], "amount": b["amount"]})
            df = pd.DataFrame(rows).sort_values("date")
            p = d_dir / f"{sym}.parquet"
            if p.exists():
                old = pd.read_parquet(p)
                df = pd.concat([old[~old["date"].isin(df["date"])], df], ignore_index=True).sort_values("date")
            df.to_parquet(p)
            n_ok += 1
        except Exception as e:
            print(f"  {sym} ERR {e}")
        if (k + 1) % 50 == 0:
            print(f"  {k + 1}/{len(syms)}（ok {n_ok}）", flush=True)
        time.sleep(0.02)
    api.disconnect()
    print(f"完成: {n_ok} 只回填, {time.time()-t0:.0f}s; 目录 {d_dir}")


if __name__ == "__main__":
    main()
