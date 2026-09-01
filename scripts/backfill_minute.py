"""H7.1 分钟数据回填：pytdx → minute_kline 表（增量，重复回填幂等）

用法:
    python scripts/backfill_minute.py --symbols 600584,002606 --freq 5m --since 2026-04-01
    python scripts/backfill_minute.py --universe --freq 5m --since 2026-04-01   # 全部股票池
    python scripts/backfill_minute.py --symbols 002606 --freq 1m --verify       # 校验 vs 日线
    python scripts/backfill_minute.py --symbols 600584 --freq 5m --tencent      # 腾讯补近期缺口

输出: 每只股票拉取范围 + 库统计；--verify 时输出日线一致性校验结果。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data.store import Store  # noqa: E402
from data.minute import fetch_minute_kline, tencent_minute, verify_vs_daily  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="分钟数据回填（H7.1）")
    ap.add_argument("--symbols", default="", help="逗号分隔股票代码（优先）")
    ap.add_argument("--universe", action="store_true", help="全部股票池（stock_daily 全部标的）")
    ap.add_argument("--freq", default="5m", choices=["1m", "5m", "15m", "30m", "60m"])
    ap.add_argument("--since", default="2026-04-01", help="回填起点（默认 2026-04-01）")
    ap.add_argument("--verify", action="store_true", help="分钟聚合 vs 日线一致性校验")
    ap.add_argument("--tencent", action="store_true", help="腾讯备胎补充近期（缺口兜底）")
    args = ap.parse_args()

    store = Store()
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.universe:
        symbols = [r[0] for r in store.conn.execute(
            "SELECT DISTINCT symbol FROM stock_daily ORDER BY symbol").fetchall()]
    else:
        print("请指定 --symbols 或 --universe")
        return

    total = 0
    for sym in symbols:
        df = fetch_minute_kline(sym, freq=args.freq, since=args.since)
        if args.tencent and (df.empty or df["ts"].iloc[-1][:10] < "2026-08-20"):
            t = tencent_minute(sym, args.freq)
            if not t.empty:
                df = df[df["ts"] < t["ts"].iloc[0]] if not df.empty else df
                df = df._append(t, ignore_index=True).drop_duplicates("ts").sort_values("ts")
        n = store.upsert_minute(df, sym, args.freq)
        total += n
        if df.empty:
            print(f"  {sym}: 无数据")
            continue
        print(f"  {sym}: +{n} 根  {df['ts'].iloc[0]} ~ {df['ts'].iloc[-1]}")

        if args.verify:
            daily = store.get_stock(sym)
            issues = verify_vs_daily(df, daily, sym)
            for it in issues[:10]:
                print(f"    [校验] {it}")
            if not issues:
                print(f"    [校验] {sym} 分钟 vs 日线 一致 ✓")

    print(f"\n完成: {len(symbols)} 只，共 {total} 根")
    st = store.minute_stats()
    print(f"minute_kline: {len(st)} 个 (symbol,freq) 组合")
    store.close()


if __name__ == "__main__":
    main()
