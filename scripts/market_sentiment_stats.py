"""P1 情绪周期数据基础：全市场每日 涨停家数 / 最高连板 / 炸板率（qfq 分年目录直读）

涨停判定：当日收盘价 ≥ round(昨收 × (1+limit_pct), 2)（板块口径：300/301/688/689=20%，
北交所 8xx/4xx=30%，其余 10%）；昨收=前一日收盘（跨日计算）。
触及涨停：当日最高价 ≥ 涨停价；炸板 = 触及未封住。
连板高度：每标的连续涨停天数（当日涨停则 +1，否则清零）→ 每日市场最大连板。
正股过滤：排除指数（399xxx）、ETF/LOF/基金（5xx/16x/15x/51x 前缀）。
输出: outputs/market_sentiment_2026.md（含情绪状态分档）
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "market_sentiment_{year}.md"
ROOT = pathlib.Path(r"F:/WorkBuddyItem/a股分钟线/parquet_qfq_2026")


def is_stock(sym: str) -> bool:
    if sym.startswith(("399", "5", "15", "16")):
        return False  # 指数 / 基金 / LOF / 可转债等
    return True


def limit_pct_of(sym: str) -> float:
    if sym.startswith(("300", "301", "688", "689")):
        return 0.20
    if sym.startswith(("4", "8")):
        return 0.30
    return 0.10


def main():
    year = sys.argv[1] if len(sys.argv) > 1 else "2026"
    root = pathlib.Path(r"F:/WorkBuddyItem/a股分钟线") / f"parquet_qfq_{year}"
    files = sorted(root.glob("*.parquet"))
    stocks = [f.name[:-8] for f in files if is_stock(f.name[:-8])]
    print(f"正股标的：{len(stocks)} / {len(files)}")

    daily = {}   # date -> {close: {sym: px}, high: {sym: px}, prev_close: {sym: px}}
    streaks = {}  # sym -> 当前连板数
    all_dates = set()
    per_day = {}  # date -> dict

    import csv as _csv
    _csv_path = pathlib.Path(__file__).resolve().parent.parent / "outputs" / f"sentiment_daily_{year}.csv"
    _csv_rows = []
    n = 0
    for sym in stocks:
        fp = root / f"{sym}.parquet"
        try:
            df = pd.read_parquet(fp, columns=["datetime", "high", "close"])
        except Exception:
            continue
        df = df[(df["close"] > 0) & (df["high"] > 0)]
        if df.empty:
            continue
        dt = df["datetime"].astype(str)
        df["date"] = dt.str[:8]
        g = df.groupby("date")
        last = g.tail(1).set_index("date")
        hi = g["high"].max()
        for date, row in last.iterrows():
            rec = per_day.setdefault(date, {})
            rec.setdefault("close", {})[sym] = float(row["close"])
            rec.setdefault("high", {})[sym] = float(hi.loc[date])
            all_dates.add(date)
        n += 1
        if n % 500 == 0:
            print(f"  {n}/{len(stocks)}", flush=True)

    dates = sorted(all_dates)
    prev_close: dict[str, float] = {}
    rows = []
    for date in dates:
        rec = per_day[date]
        closes, highs = rec.get("close", {}), rec.get("high", {})
        zt, touch, zhaban = 0, 0, 0
        max_height = 0
        for sym in closes:
            pc = prev_close.get(sym)
            if not pc or pc <= 0:
                continue
            lpx = round(pc * (1 + limit_pct_of(sym)), 2)
            c = closes[sym]
            h = highs.get(sym, c)
            if h >= lpx - 0.01:
                touch += 1
                if c < lpx - 0.005:
                    zhaban += 1
            if c >= lpx - 0.005:
                zt += 1
                streaks[sym] = streaks.get(sym, 0) + 1
                max_height = max(max_height, streaks[sym])
            else:
                streaks[sym] = 0
        prev_close = closes
        zhaban_rate = zhaban / touch * 100 if touch else 0.0
        rows.append({"date": date, "zt": zt, "touch": touch, "zhaban": zhaban,
                     "zhaban_rate": round(zhaban_rate, 1), "max_height": max_height})
        _csv_rows.append({"date": f"{date[:4]}-{date[4:6]}-{date[6:]}", "zt": zt, "touch": touch,
                          "zhaban": zhaban, "zhaban_rate": round(zhaban_rate, 1), "max_height": max_height})
    d = pd.DataFrame(rows)
    d["date"] = pd.to_datetime(d["date"], format="%Y%m%d")

    L = ["# 全市场情绪统计 2026（qfq 直读，6000 只）", "",
         f"> 正股 {len(stocks)} 只；涨停口径=收盘≥涨停价；炸板=触及未封住；连板=连续涨停。", ""]
    L.append("| 日期 | 涨停 | 触及 | 炸板 | 炸板率% | 最高连板 |")
    L.append("|---|---|---|---|---|---|")
    for _, r in d.iterrows():
        L.append(f"| {r['date'].strftime('%Y-%m-%d')} | {r['zt']} | {r['touch']} | {r['zhaban']} "
                 f"| {r['zhaban_rate']} | {r['max_height']} |")
    # 情绪分档：冰点≤34 / 磨底≤64 / 普反≥99 / 活跃
    L.append("")
    L.append("## 月度汇总")
    L.append("")
    L.append("| 月份 | 日均涨停 | 日均炸板率% | 月内最高连板 |")
    L.append("|---|---|---|---|")
    d["month"] = d["date"].dt.strftime("%Y-%m")
    for m, g in d.groupby("month"):
        L.append(f"| {m} | {g['zt'].mean():.0f} | {g['zhaban_rate'].mean():.1f} | {g['max_height'].max()} |")
    L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    with open(_csv_path, "w", newline="") as _f:
        _w = _csv.DictWriter(_f, fieldnames=["date", "zt", "touch", "zhaban", "zhaban_rate", "max_h"])
        _w.writeheader()
        for _r in _csv_rows:
            _w.writerow({"date": _r["date"], "zt": _r["zt"], "touch": _r["touch"],
                         "zhaban": _r["zhaban"], "zhaban_rate": _r["zhaban_rate"],
                         "max_h": _r["max_height"]})
    print(f"CSV -> {_csv_path} ({len(_csv_rows)} 天)")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[-40:]))
    print(f"\nREPORT -> {OUT}")


if __name__ == "__main__":
    main()