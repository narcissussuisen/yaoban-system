"""H7.2 批量 B 点检测：对给定股票 × 日期范围输出 B 点表（盘中买点可复现性验证）

用法:
    python scripts/scan_bpoints.py --symbols 002606,600584 --start 2026-08-18 --end 2026-08-24
    python scripts/scan_bpoints.py --universe --start 2026-05-06 --end 2026-08-24 --freq 1m
输出: 每个 (股票, 日期) 的 B 点列表；汇总表（日期、代码、B点时间、触发价、涨幅、收盘涨幅）
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.store import Store  # noqa: E402
from core.intraday import day_b_points  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "bpoint_scan.md"


def main():
    ap = argparse.ArgumentParser(description="批量 B 点扫描（H7.2）")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--universe", action="store_true")
    ap.add_argument("--start", default="2026-08-17")
    ap.add_argument("--end", default="2026-08-24")
    ap.add_argument("--freq", default="1m", choices=["1m", "5m"])
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

    # 交易日历（用指数）
    idx = store.get_index("sh000001", start=args.start, end=args.end)
    dates = [r[1] for r in idx]

    all_rows = []
    per_symbol = {}
    for sym in symbols:
        hits = []
        for d in dates:
            pts = day_b_points(store, sym, d, freq=args.freq)
            for _, r in pts.iterrows():
                hits.append({"date": d, "ts": r["ts"], "price": r["price"],
                             "vwap": r["vwap"], "pct": r["pct"], "kind": r["kind"]})
                all_rows.append({"symbol": sym, "date": d, "ts": r["ts"],
                                 "price": r["price"], "pct": r["pct"], "kind": r["kind"]})
        if hits:
            per_symbol[sym] = hits
        print(f"  {sym}: {len(hits)} 个 B 点"
              + (f"（{hits[0]['ts'][11:]} 首）" if hits else ""))

    L = [f"# B 点扫描 {args.start} ~ {args.end}（freq={args.freq}）", ""]
    L.append(f"> 共 {len(symbols)} 只股票，{len(all_rows)} 个 B 点，覆盖 {len(dates)} 个交易日")
    L.append("")
    if all_rows:
        L.append("| 日期 | 代码 | B点时间 | 类型 | 触发价 | 触发涨幅% |")
        L.append("|---|---|---|---|---|---|")
        for r in sorted(all_rows, key=lambda x: (x["date"], x["symbol"])):
            L.append(f"| {r['date']} | {r['symbol']} | {r['ts'][11:]} | {r['kind']} "
                     f"| {r['price']:.2f} | {r['pct']:.2f} |")
    L.append("")
    L.append("> B1=开盘强势（放量+白线向上+不破均价线+涨幅≤3%）；B2=回踩确认（放量拉升后回踩"
             "均价线不破重新收上）；触发后 30 分钟冷却，14:45 后不触发。需人工复核板块与分时细节。")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"\nREPORT -> {OUT}")
    store.close()


if __name__ == "__main__":
    main()
