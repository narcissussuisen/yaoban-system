"""R1.2 回踩形态池 × B2 低吸执行验证（选手 B 点模式规则化核心验证）

形态候选（R1.1 扫描）：60日内≥1涨停 + 20日线上方 + 距20日高点回撤2~20% + 回调1~8天 + 成交额≥1亿
执行对照（含成本）：
  A 开盘执行（次日开盘买入，日线退出）
  B B2 任意（次日首个 B2 触发价）
  C B2 真低吸（B2 触发价 ≤ 开盘×1.005 —— 回踩到开盘下方才做）
  D B2 真低吸 + 次日低开（gap ≤ -0.5%，低开回踩强化）
分档：次日 gap × 回撤幅度 × 回调天数（找 B2 真低吸的正期望组合）
输出: outputs/pullback_b2_{year}.md
"""
from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd  # noqa: E402

from data.store import Store  # noqa: E402
from data.qfq_store import QFQStore, build_daily_map  # noqa: E402
from core.intraday import day_b_points  # noqa: E402
from core.sell import limit_pct_of  # noqa: E402

COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"


def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)


def is_stock(sym: str) -> bool:
    return not sym.startswith(("399", "5", "15", "16"))


def daily_exit(daily_rows, ed, entry_px):
    dates = [r[1] for r in daily_rows]
    if ed not in dates:
        return None
    i = dates.index(ed)
    if i + 1 >= len(daily_rows):
        return None
    r = daily_rows[i + 1]
    stop_px = entry_px * 0.95
    # 修复（P3 阻断项）：LOW(r[4]) 判定 + 开盘跳空按开盘价成交
    op, lo, cl = float(r[2]), float(r[4]), float(r[5])
    exit_px = (op if op <= stop_px else stop_px) if lo <= stop_px else cl
    return (sell_net(exit_px) / buy_net(entry_px) - 1) * 100.0


def main():
    t0 = time.time()
    year = sys.argv[1] if len(sys.argv) > 1 else "2026"
    store = Store()
    qfq = QFQStore(year)
    idx_dates = [r[1] for r in store.get_index("sh000001")]
    symbols = [s for s in qfq.symbols() if is_stock(s)]
    print(f"[{time.time()-t0:.0f}s] 聚合日线 {len(symbols)} 只…", flush=True)
    daily_map = build_daily_map(year, symbols, workers=6)
    daily_map = {k: v for k, v in daily_map.items() if v}
    print(f"[{time.time()-t0:.0f}s] 日线就绪 {len(daily_map)} 只，扫描形态候选…", flush=True)

    # 形态候选扫描（与 R1.1 相同条件）
    cands = []  # (sym, sig_date, exec_date, gap, dd, dpk)
    for sym, rows in daily_map.items():
        if len(rows) < 70:
            continue
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low",
                                         "close", "volume", "amount"])
        c = df["close"]
        hi = df["high"]
        lpx = limit_pct_of(sym)
        zt = (c / c.shift(1) - 1 >= lpx - 0.005).astype(int)
        zt60 = zt.rolling(60).sum().shift(1)
        hh20 = hi.rolling(20).max().shift(1)
        drawdown = (c / hh20 - 1) * 100
        peak_pos = hi.rolling(20).apply(lambda x: x.argmax(), raw=True).shift(1)
        days_from_peak = (20 - peak_pos.fillna(0)).astype(int)
        ma20 = c.rolling(20).mean()
        amt20 = df["amount"].rolling(20).mean() / 1e8
        above_ma20 = c > ma20
        n = len(df)
        for i in range(70, n - 1):
            if not above_ma20.iloc[i]:
                continue
            dd = drawdown.iloc[i]
            if pd.isna(dd) or not (2 <= dd <= 20):
                continue
            dpk = days_from_peak.iloc[i]
            if not (1 <= dpk <= 8):
                continue
            if pd.isna(zt60.iloc[i]) or zt60.iloc[i] < 1:
                continue
            amt = amt20.iloc[i]
            if pd.isna(amt) or amt < 1:
                continue
            # 执行日 = 次日
            r1 = rows[i + 1]
            gap = (float(r1[2]) / float(rows[i][5]) - 1) * 100
            cands.append({"sym": sym, "d": rows[i][1], "ed": r1[1],
                          "gap": gap, "dd": float(dd), "dpk": int(dpk)})
    print(f"[{time.time()-t0:.0f}s] 形态候选 {len(cands)} 个，B2 检测…", flush=True)

    # B2 检测（按 sym 分组缓存）
    bkw = dict(lookback=20, cool_min=30, open_win=30, surge_win=60)
    by_sym: dict[str, list] = {}
    for c in cands:
        by_sym.setdefault(c["sym"], []).append(c)
    rows_out = []
    for sym, clist in by_sym.items():
        _ = qfq.get_minute(sym)  # 预热缓存
        daily = daily_map.get(sym, [])
        for c in clist:
            ed = c["ed"]
            p_open = daily_exit(daily, ed, float(daily[[r[1] for r in daily].index(ed)][2])) if ed in [r[1] for r in daily] else None
            if p_open is None:
                continue
            pts = day_b_points(qfq, sym, ed, freq="1m", **bkw)
            b2 = pts[pts["kind"] == "B2"] if not pts.empty else pts
            # 开盘价（分钟首根）
            mrows = qfq.get_minute(sym, start=ed, end=ed)
            if not mrows:
                continue
            day = pd.DataFrame(mrows, columns=["symbol", "freq", "ts", "open", "high",
                                               "low", "close", "volume", "amount"])
            open_px = float(day["open"].iloc[0])
            rec = {"sym": sym, "d": c["d"], "ed": ed, "gap": c["gap"], "dd": c["dd"],
                   "dpk": c["dpk"], "p_open": p_open,
                   "p_b2": None, "p_b2_low": None, "p_b2_lowgap": None}
            if not b2.empty:
                b2px = float(b2.iloc[0]["price"])
                rec["p_b2"] = daily_exit(daily, ed, b2px)
                if b2px <= open_px * 1.005:
                    rec["p_b2_low"] = daily_exit(daily, ed, b2px)
                    if c["gap"] <= -0.5:
                        rec["p_b2_lowgap"] = daily_exit(daily, ed, b2px)
            rows_out.append(rec)
    df = pd.DataFrame(rows_out)
    print(f"[{time.time()-t0:.0f}s] 有效 {len(df)} 个，汇总…", flush=True)

    # 保存中间结果（网格搜索用）
    df.to_csv(OUT_DIR / f"pullback_b2_{year}_raw.csv", index=False)
    L = [f"# 回踩形态池 × B2 低吸验证 {year}（n={len(df)}）", "",
         "> 候选=60日内≥1涨停+20日线上方+回撤2~20%+回调1~8天+成交额≥1亿；成本含佣金/印花/滑点。", "",
         "## 执行方式对照", "",
         "| 执行 | n | 均值% | 中位% | 胜率% |",
         "|---|---|---|---|---|"]
    for col, name in (("p_open", "A 开盘执行"), ("p_b2", "B B2任意"), ("p_b2_low", "C B2真低吸(≤开盘+0.5%)"),
                      ("p_b2_lowgap", "D B2真低吸+低开≤-0.5%")):
        g = df[col].dropna()
        if len(g):
            L.append(f"| {name} | {len(g)} | {g.mean():+.2f} | {g.median():+.2f} | {(g > 0).mean() * 100:.0f} |")
    L.append("")

    def band(col, cuts, labels, pnl_col="p_b2_low"):
        L.append(f"### {col} × B2真低吸")
        L.append("")
        L.append("| 分档 | n | B2低吸均值% | 开盘均值% | 胜率% |")
        L.append("|---|---|---|---|---|")
        g = df.dropna(subset=[pnl_col]).copy()
        g["b"] = pd.cut(g[col], bins=cuts, labels=labels, include_lowest=True)
        for b in labels:
            gg = g[g["b"] == b]
            if len(gg):
                L.append(f"| {b} | {len(gg)} | {gg[pnl_col].mean():+.2f} | {gg['p_open'].mean():+.2f} "
                         f"| {(gg[pnl_col] > 0).mean() * 100:.0f} |")
        L.append("")

    band("gap", [-100, -2, 0, 1, 3, 100], ["低开<-2%", "-2~0%", "0~1%", "1~3%", "高开>3%"])
    band("dd", [-1, 3, 6, 10, 15, 100], ["2~3%", "3~6%", "6~10%", "10~15%", "15~20%"])
    band("dpk", [-1, 2, 3, 5, 8, 100], ["1~2天", "3天", "4~5天", "6~8天", "8天+"])
    L.append("> 仅供方法论研究，不构成投资建议。")
    outp = OUT_DIR / f"pullback_b2_{year}.md"
    outp.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {outp}  （总耗时 {(time.time()-t0)/60:.1f} 分钟）")
    store.close()


if __name__ == "__main__":
    main()