"""H7.2 B 点质量评估：触发后 30/60 分钟、当日收盘、次日开盘收益

用法:
    python scripts/eval_bpoints.py --symbols 002606,600584 --start 2026-05-06 --end 2026-08-24
输出: outputs/bpoint_eval.md（含分档统计：全部 / B1 / B2）
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

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "bpoint_eval.md"


def main():
    ap = argparse.ArgumentParser(description="B 点质量评估（H7.2）")
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--start", default="2026-05-06")
    ap.add_argument("--end", default="2026-08-24")
    args = ap.parse_args()

    store = Store()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    idx = store.get_index("sh000001", start=args.start, end=args.end)
    dates = [r[1] for r in idx]
    all_dates = [r[1] for r in store.get_index("sh000001")]

    rows = []
    for sym in symbols:
        mrows = store.get_minute(sym, "1m")
        if not mrows:
            continue
        mdf = pd.DataFrame(mrows, columns=["symbol", "freq", "ts", "open", "high", "low",
                                           "close", "volume", "amount"])
        for d in dates:
            pts = day_b_points(store, sym, d, freq="1m")
            for _, r in pts.iterrows():
                i = mdf.index[mdf["ts"] == r["ts"]]
                if i.empty:
                    continue
                i = i[0]
                px = float(r["price"])
                rec = {"symbol": sym, "date": d, "ts": r["ts"], "kind": r["kind"],
                       "px": px}
                # +30 / +60 分钟
                for off, col in ((30, "r30"), (60, "r60")):
                    j = i + off
                    if j < len(mdf) and mdf["ts"].iloc[j][:10] == d:
                        rec[col] = round((float(mdf["close"].iloc[j]) / px - 1) * 100, 2)
                # 当日收盘
                day = mdf[mdf["ts"].str[:10] == d]
                if len(day):
                    rec["r_day"] = round((float(day["close"].iloc[-1]) / px - 1) * 100, 2)
                # 次日开盘
                di = all_dates.index(d) if d in all_dates else -1
                if di >= 0 and di + 1 < len(all_dates):
                    nxt = mdf[mdf["ts"].str[:10] == all_dates[di + 1]]
                    if len(nxt):
                        rec["r_next"] = round((float(nxt["open"].iloc[0]) / px - 1) * 100, 2)
                rows.append(rec)

    df = pd.DataFrame(rows)
    L = [f"# B 点质量评估 {args.start} ~ {args.end}", "",
         f"> {len(symbols)} 只股票，{len(df)} 个 B 点。收益=相对触发价的百分比；"
         "r30/r60=触发后30/60分钟，r_day=当日收盘，r_next=次日开盘", ""]

    def stat(d, label):
        if d.empty:
            L.append(f"### {label}: 无数据")
            L.append("")
            return
        L.append(f"### {label}（n={len(d)}）")
        L.append("")
        L.append("| 窗口 | 均值% | 中位数% | 胜率% |")
        L.append("|---|---|---|---|")
        for col, name in (("r30", "+30分钟"), ("r60", "+60分钟"),
                          ("r_day", "当日收盘"), ("r_next", "次日开盘")):
            s = d[col].dropna()
            if len(s):
                L.append(f"| {name} | {s.mean():+.2f} | {s.median():+.2f} | {(s > 0).mean()*100:.0f} |")
        L.append("")

    stat(df, "全部 B 点")
    if not df.empty:
        for kind in ("B1", "B2"):
            stat(df[df["kind"] == kind], f"B1 开盘强势（n={int((df['kind']==kind).sum())}）"
                 if (df["kind"] == kind).any() else kind)

    if len(df):
        L.append("## 明细（仅列出触发后当日收益为负的样本，供人工复核）")
        L.append("")
        bad = df[df["r_day"] < 0] if "r_day" in df else df
        if len(bad):
            L.append("| 日期 | 代码 | 时间 | 类型 | 触发价 | r30% | r60% | 当日% | 次日开盘% |")
            L.append("|---|---|---|---|---|---|---|---|---|")
            for _, r in bad.sort_values("r_day").iterrows():
                L.append(f"| {r['date']} | {r['symbol']} | {r['ts'][11:]} | {r['kind']} "
                         f"| {r['px']:.2f} | {r.get('r30','-')} | {r.get('r60','-')} "
                         f"| {r.get('r_day','-'):.2f} | {r.get('r_next','-')} |")
        L.append("")
    L.append("> 说明：B 点=放量+白线向上+站稳均价线+涨幅≤3%；统计含次日跳空风险，"
             "仅供方法论研究。")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {OUT}")
    store.close()


if __name__ == "__main__":
    main()
