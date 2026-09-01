"""情绪周期择时验证（2026-01~08，全市场涨停家数/炸板率 × 次日指数收益）

1) 涨停家数分档 → 次日上证收益（冰点修复假设）
2) 炸板率高档 → 次日上证收益（退潮风险假设）
3) 输出: outputs/sentiment_verification.md
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.store import Store  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "sentiment_verification.md"


def main():
    store = Store()
    # 情绪日数据（market_sentiment_stats 输出重新计算，避免依赖报告）
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
    # 直接重算一次（复用脚本逻辑的轻量版：仅涨停家数/炸板率）
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "mss", str(pathlib.Path(__file__).resolve().parent.parent / "scripts" / "market_sentiment_stats.py"))
    mss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mss)

    import pathlib as _p
    ROOT = _p.Path(r"F:/WorkBuddyItem/a股分钟线/parquet_qfq_2026")
    files = sorted(ROOT.glob("*.parquet"))
    stocks = [f.name[:-8] for f in files if mss.is_stock(f.name[:-8])]
    per_day = {}
    n = 0
    for sym in stocks:
        try:
            df = pd.read_parquet(ROOT / f"{sym}.parquet", columns=["datetime", "high", "close"])
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
        n += 1
    dates = sorted(per_day)
    prev_close = {}
    daily_sent = {}
    streaks = {}
    for date in dates:
        rec = per_day[date]
        closes, highs = rec.get("close", {}), rec.get("high", {})
        zt = touch = zhaban = 0
        max_h = 0
        for sym in closes:
            pc = prev_close.get(sym)
            if not pc or pc <= 0:
                continue
            lpx = round(pc * (1 + mss.limit_pct_of(sym)), 2)
            c = closes[sym]
            h = highs.get(sym, c)
            if h >= lpx - 0.01:
                touch += 1
                if c < lpx - 0.005:
                    zhaban += 1
            if c >= lpx - 0.005:
                zt += 1
                streaks[sym] = streaks.get(sym, 0) + 1
                max_h = max(max_h, streaks[sym])
            else:
                streaks[sym] = 0
        prev_close = closes
        daily_sent[date] = {"zt": zt, "zhaban_rate": zhaban / touch * 100 if touch else 0.0,
                            "max_h": max_h}

    # 指数次日收益
    idx = pd.DataFrame(store.get_index("sh000001"),
                       columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    idx["date"] = idx["date"].astype(str)
    idx["ret"] = idx["close"].pct_change() * 100
    idx_map = dict(zip(idx["date"], idx["ret"]))

    rows = []
    for date, s in daily_sent.items():
        dstr = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        rows.append({"date": dstr, **s, "next_ret": idx_map.get(dstr)})
    d = pd.DataFrame(rows).dropna(subset=["next_ret"])
    L = ["# 情绪周期择时验证（2026-01~08，全市场）", "",
         f"> 样本 {len(d)} 个交易日；next_ret=次日上证涨跌幅%。", ""]

    L.append("## 1) 涨停家数分档 × 次日收益")
    L.append("")
    L.append("| 分档 | n | 次日均值% | 次日中位% | 次日胜率% |")
    L.append("|---|---|---|---|---|")
    for lo, hi, lab in [(0, 40, "冰点 ≤40"), (41, 70, "磨底 41~70"), (71, 100, "普反 71~100"),
                        (101, 999, "活跃 ≥101")]:
        g = d[(d["zt"] >= lo) & (d["zt"] <= hi)]
        if len(g):
            L.append(f"| {lab} | {len(g)} | {g['next_ret'].mean():+.2f} | {g['next_ret'].median():+.2f} "
                     f"| {(g['next_ret']>0).mean()*100:.0f} |")
    L.append("")
    L.append("## 2) 炸板率分档 × 次日收益（退潮风险）")
    L.append("")
    L.append("| 分档 | n | 次日均值% | 次日中位% | 次日胜率% |")
    L.append("|---|---|---|---|---|")
    for lo, hi, lab in [(0, 25, "低炸板 <25%"), (25, 35, "中 25~35%"), (35, 100, "高炸板 ≥35%")]:
        g = d[(d["zhaban_rate"] >= lo) & (d["zhaban_rate"] < hi)]
        if len(g):
            L.append(f"| {lab} | {len(g)} | {g['next_ret'].mean():+.2f} | {g['next_ret'].median():+.2f} "
                     f"| {(g['next_ret']>0).mean()*100:.0f} |")
    L.append("")
    L.append("## 3) 冰点日次日（涨停 ≤40 的修复行情）")
    L.append("")
    ice = d[d["zt"] <= 40]
    L.append(f"- 冰点日 {len(ice)} 个；次日指数均值 {ice['next_ret'].mean():+.2f}%，"
             f"其中 8/19（涨停 40）次日 +{d[d['date']=='2026-08-20']['next_ret'].iloc[0]:.2f}%")
    L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {OUT}")
    store.close()


if __name__ == "__main__":
    main()