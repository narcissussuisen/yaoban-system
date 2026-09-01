"""R6 每日管线：盘后生成次日候选池（5+ 连板 + 动量池 + 回踩池 + 情绪过滤 + 板块归属）

用法: python -B scripts/daily_candidates.py [--date 2026-08-20] [--year 2026]
输出: outputs/candidates_{date}.md（次日候选清单，供模拟盘/人工执行）
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

from data.store import Store  # noqa: E402
from data.qfq_store import QFQStore, build_daily_map  # noqa: E402
from core.sell import limit_pct_of  # noqa: E402

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"
SW = pathlib.Path(__file__).resolve().parent.parent / "data" / "sw_industry_history.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--year", default="2026")
    args = ap.parse_args()
    import datetime
    date = args.date or datetime.date.today().strftime("%Y-%m-%d")
    year = date[:4] if args.year == "2026" and date[:4] == "2026" else args.year

    store = Store()
    qfq = QFQStore(year)
    idx_dates = [r[1] for r in store.get_index("sh000001")]
    if date not in idx_dates:
        print(f"{date} 非交易日或超出数据范围")
        return
    di = idx_dates.index(date)

    symbols = [s for s in qfq.symbols() if not s.startswith(("399", "5", "15", "16"))]
    daily_map = build_daily_map(year, symbols, workers=6)
    daily_map = {k: v for k, v in daily_map.items() if v}

    # 情绪状态
    sent = {}
    sp = OUT_DIR / f"sentiment_daily_{year}.csv"
    if sp.exists():
        sent = {r["date"]: r for _, r in pd.read_csv(sp).iterrows()}
    sr = sent.get(date, {})

    # 申万归属
    sw = pd.read_csv(SW, dtype={"code": str, "l1_code": str})
    sw["start_date"] = pd.to_datetime(sw["start_date"], errors="coerce")
    sw_by = {c: g.sort_values("start_date") for c, g in sw.groupby("code")}
    def l1_of(sym, d):
        g = sw_by.get(sym)
        if g is None or not len(g):
            return "?"
        sub = g[g["start_date"] <= pd.Timestamp(d)]
        return str(sub.iloc[-1]["l1_code"]) if len(sub) else "?"

    cands = []
    for sym, rows in daily_map.items():
        if len(rows) < 70:
            continue
        dates = [r[1] for r in rows]
        if date not in dates:
            continue
        i = dates.index(date)
        if i < 1:
            continue
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low",
                                         "close", "volume", "amount"])
        c = df["close"]
        lpx = limit_pct_of(sym)
        zt = (c / c.shift(1) - 1 >= lpx - 0.005).astype(int)
        # 连板数（截至 T 日）
        lb = 0
        for k in range(i, -1, -1):
            if zt.iloc[k]:
                lb += 1
            else:
                break
        hh20 = df["high"].iloc[max(0, i - 19):i + 1].max()
        dd = (c.iloc[i] / hh20 - 1) * 100
        amt20 = df["amount"].iloc[max(0, i - 19):i + 1].mean() / 1e8
        mom60 = (c.iloc[i] / c.iloc[i - 60] - 1) * 100 if i >= 60 else None
        # 模式判定
        modes = []
        if lb >= 5:
            modes.append(("5+连板", 0))
        if mom60 is not None and 60 <= mom60 <= 120 and amt20 >= 5:
            modes.append(("动量池", 1))
        if lb >= 1 and dd >= 2 and dd <= 20 and amt20 >= 1:
            # 回踩池需要 60 日涨停历史——简化用当日连板≥1 或近期涨停
            modes.append(("回踩池", 2))
        if modes:
            prio = min(m[1] for m in modes)
            cands.append({"sym": sym, "date": date, "l1": l1_of(sym, date),
                          "lb": lb, "dd": round(dd, 1), "mom60": round(mom60, 0) if mom60 else None,
                          "amt20": round(amt20, 1), "modes": "+".join(m[0] for m in modes),
                          "prio": prio, "close": round(c.iloc[i], 2)})
    cd = pd.DataFrame(cands)
    if len(cd):
        cd = cd.sort_values(["prio", "lb", "amt20"], ascending=[True, False, False])

    L = [f"# 次日候选池 {date}（生成于盘后）", "",
         f"> 情绪：涨停 {sr.get('zt', '?')} 家 / 炸板率 {sr.get('zhaban_rate', '?')}% "
         f"/ 连板高度 {sr.get('max_h', '?')}；环境过滤=炸板率≤35。", "",
         "| 优先级 | 代码 | 连板 | 模式 | 回撤% | 动量60 | 成交额(亿) | 行业 | 收盘价 |",
         "|---|---|---|---|---|---|---|---|---|"]
    prio_name = {0: "★5+连板", 1: "动量池", 2: "回踩池"}
    for _, r in cd.head(30).iterrows():
        L.append(f"| {prio_name.get(r['prio'], '?')} | {r['sym']} | {int(r['lb'])} | {r['modes']} "
                 f"| {r['dd']} | {r['mom60'] if r['mom60'] is not None else '-'} | {r['amt20']} "
                 f"| {r['l1']} | {r['close']} |")
    L.append("")
    L.append("> 执行规则（双票口径）：优先 5+ 连板（一字板跳过），最多 2 只各 50% 仓，T+1 收盘卖出；"
             "仅供方法论研究，不构成投资建议。")
    L.append("")
    L.append("> **W2 执行纪律（评审后保留的规避信号）**：次日开盘 30 分钟内跌破前日低点 → 当日放弃买入。"
             "触发判定：9:31-10:00 分钟线 low < 前日 low（须盘中盯盘，10:00 后确认）；"
             "确认后以 10:00 后价格买入（不可按开盘价成交——T+1 约束 + 前视规避）。")
    L.append("> 依据：docs/reviews/w2_daban5_review.md（W2 作为规避层，keep-drop 各年 +7.4~+8.8pp，t=3.2~5.2）。")
    outp = OUT_DIR / f"candidates_{date}.md"
    outp.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {outp}")
    store.close()


if __name__ == "__main__":
    main()
