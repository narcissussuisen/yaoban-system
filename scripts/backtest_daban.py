"""R3 打板接力模式回测（分钟自算涨停池 + 开盘近似执行 + 10 点纪律 + 一字板未成交建模）

涨停池自算（日线向量化）：close ≥ 涨停价(板块10/20/30%) → 涨停；high≥涨停价且close<涨停价 → 炸板
连板数：连续涨停计数（T 日连板数）
候选：T 日涨停 → T+1 执行：
  - 开盘买入（含成本）；一字板（open=涨停价 且 low=涨停价）→ 标记未成交（不计收益，统计比率）
  - 10 点纪律：T+1 10:00 涨幅<5% 且未封板 → 按 10:00 价卖（分钟判定）
  - 日线退出：-5% 止损 / 收盘
输出: outputs/backtest_daban_{year}.md
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
from core.sell import limit_pct_of  # noqa: E402

COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"


def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)


def is_stock(sym: str) -> bool:
    return not sym.startswith(("399", "5", "15", "16"))


def main():
    t0 = time.time()
    year = sys.argv[1] if len(sys.argv) > 1 else "2026"
    store = Store()
    qfq = QFQStore(year)
    idx_dates = [r[1] for r in store.get_index("sh000001")]
    symbols = [s for s in qfq.symbols() if is_stock(s)]
    print(f"[{time.time()-t0:.0f}s] 聚合日线…", flush=True)
    daily_map = build_daily_map(year, symbols, workers=6)
    daily_map = {k: v for k, v in daily_map.items() if v}

    # 涨停/连板/炸板序列 + 候选
    cands = []  # (sym, t_date, t1_date, lianban)
    for sym, rows in daily_map.items():
        if len(rows) < 30:
            continue
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low",
                                         "close", "volume", "amount"])
        c = df["close"].to_numpy()
        hi = df["high"].to_numpy()
        lo = df["low"].to_numpy()
        op = df["open"].to_numpy()
        prev = np.roll(c, 1)
        prev[0] = np.nan
        lpx = limit_pct_of(sym)
        limit_px = np.round(prev * (1 + lpx), 2)
        zt = c >= limit_px - 0.005
        zb = (hi >= limit_px - 0.01) & (c < limit_px - 0.005)
        # 连板数
        lb = np.zeros(len(df), dtype=int)
        for i in range(1, len(df)):
            lb[i] = lb[i - 1] + 1 if zt[i] else 0
        n = len(df)
        for i in range(1, n - 1):
            if not zt[i]:
                continue
            # 涨停日 T，T+1 执行
            t1 = rows[i + 1]
            # 一字板判定（T+1）：open==limit 且 low==limit（全天封死）
            lpx1 = round(float(rows[i][5]) * (1 + lpx), 2)
            yizi = abs(float(t1[2]) - lpx1) < 0.005 and abs(float(t1[3]) - lpx1) < 0.005
            cands.append({"sym": sym, "t": rows[i][1], "t1": t1[1], "lb": int(lb[i]),
                          "yizi": bool(yizi), "open_px": float(t1[2]),
                          "limit_px1": lpx1, "zb_t": bool(zb[i])})
    print(f"[{time.time()-t0:.0f}s] 打板候选 {len(cands)} 个（一字板 {sum(1 for c in cands if c['yizi'])}）", flush=True)

    # 情绪列（信号日 T）
    sent = {}
    sp = OUT_DIR / f"sentiment_daily_{year}.csv"
    if sp.exists():
        sent = {r["date"]: r for _, r in pd.read_csv(sp).iterrows()}
    # 执行：10 点纪律（分钟）+ 日线退出
    bkw = dict(lookback=20, cool_min=30, open_win=30, surge_win=60)
    from core.intraday import day_b_points  # noqa: E402
    rows_out = []
    by_sym: dict[str, list] = {}
    for c in cands:
        by_sym.setdefault(c["sym"], []).append(c)
    for sym, clist in by_sym.items():
        _ = qfq.get_minute(sym)
        daily = daily_map.get(sym, [])
        dates = [r[1] for r in daily]
        for c in clist:
            t1 = c["t1"]
            if t1 not in dates:
                continue
            i = dates.index(t1)
            if i + 1 >= len(daily):
                continue
            entry_px = c["open_px"]
            # 10:00 判定
            mrows = qfq.get_minute(sym, start=t1, end=t1)
            exit_px = None
            reason = "close"
            if mrows:
                day = pd.DataFrame(mrows, columns=["symbol", "freq", "ts", "open", "high",
                                                   "low", "close", "volume", "amount"])
                pc = float(daily[i - 1][5]) if i >= 1 else float(day["close"].iloc[0])
                row10 = day[day["ts"].str[11:16] >= "10:00"]
                if not row10.empty:
                    p10 = float(row10["close"].iloc[0])
                    # 10 点纪律：涨幅<5% 且未封板 → 走
                    lpx1 = round(pc * (1 + limit_pct_of(sym)), 2)
                    if p10 / pc - 1 < 0.05 and p10 < lpx1 - 0.005:
                        exit_px = p10
                        reason = "ten_oclock"
            if exit_px is None:
                r1 = daily[i + 1]
                stop_px = entry_px * 0.95
                # 修复（P3 评审阻断项）：LOW(r1[4]) 判定 + 开盘跳空按开盘价成交
                op1, lo1, cl1 = float(r1[2]), float(r1[4]), float(r1[5])
                if lo1 <= stop_px:
                    exit_px = op1 if op1 <= stop_px else stop_px
                    reason = "daily_exit"
                else:
                    exit_px = cl1
                    reason = "close"
            pnl = (sell_net(exit_px) / buy_net(entry_px) - 1) * 100
            # 无纪律对照：全部日线退出（次日收盘/止损，同修复）
            r1 = daily[i + 1]
            stop_px = entry_px * 0.95
            op1, lo1, cl1 = float(r1[2]), float(r1[4]), float(r1[5])
            exit_nt = (op1 if op1 <= stop_px else stop_px) if lo1 <= stop_px else cl1
            pnl_nt = (sell_net(exit_nt) / buy_net(entry_px) - 1) * 100
            sr = sent.get(c["t"])
            zt_n = int(sr["zt"]) if sr is not None else -1
            zb_r = float(sr["zhaban_rate"]) if sr is not None else -1
            rows_out.append({"sym": sym, "t": c["t"], "t1": t1, "lb": c["lb"],
                             "yizi": c["yizi"], "zb_t": c["zb_t"], "pnl": pnl, "reason": reason,
                             "pnl_nt": pnl_nt, "zt": zt_n, "zhaban_rate": zb_r})
    df = pd.DataFrame(rows_out)
    df.to_csv(OUT_DIR / f"backtest_daban_{year}_raw.csv", index=False)
    print(f"[{time.time()-t0:.0f}s] 有效 {len(df)} 个，汇总…", flush=True)

    # 报告
    L = [f"# R3 打板接力回测 {year}（候选 {len(cands)}，可成交 {len(df)}）", "",
         f"> 涨停池=日线自算（close≥涨停价）；一字板 {sum(1 for c in cands if c['yizi'])} 个标记未成交；"
         f"10 点纪律（10:00 涨幅<5% 且未封板则走）；含成本。", "",
         "## 总览", "",
         "| 指标 | 值 |", "|---|---|"]
    L.append(f"| 候选（T 日涨停） | {len(cands)} |")
    L.append(f"| 未成交（一字板） | {sum(1 for c in cands if c['yizi'])}（{sum(1 for c in cands if c['yizi'])/max(len(cands),1)*100:.1f}%） |")
    L.append(f"| 成交（可回测） | {len(df)} |")
    L.append(f"| 均值 | {df['pnl'].mean():+.2f}% |")
    L.append(f"| 中位 | {df['pnl'].median():+.2f}% |")
    L.append(f"| 胜率 | {(df['pnl'] > 0).mean() * 100:.0f}% |")
    L.append(f"| 盈亏比（均赢/均亏） | {df[df['pnl'] > 0]['pnl'].mean() / abs(df[df['pnl'] < 0]['pnl'].mean()) if len(df[df['pnl'] < 0]) else float('nan'):.2f} |")
    L.append(f"| 无纪律对照（全持有到收盘） | 均值 {df['pnl_nt'].mean():+.2f}% / 胜率 {(df['pnl_nt'] > 0).mean() * 100:.0f}% / 盈亏比 {df[df['pnl_nt'] > 0]['pnl_nt'].mean() / abs(df[df['pnl_nt'] < 0]['pnl_nt'].mean()) if len(df[df['pnl_nt'] < 0]) else float('nan'):.2f} |")
    L.append("")
    L.append("## 按连板数")
    L.append("")
    L.append("| 连板 | n | 均值% | 胜率% | 盈亏比 |")
    L.append("|---|---|---|---|---|")
    for lb in sorted(df["lb"].unique()):
        g = df[df["lb"] == lb]
        if len(g):
            w = g[g["pnl"] > 0]["pnl"].mean()
            ls = g[g["pnl"] < 0]["pnl"].mean()
            pr = w / abs(ls) if ls else float("nan")
            L.append(f"| {lb} 板 | {len(g)} | {g['pnl'].mean():+.2f} | {(g['pnl'] > 0).mean() * 100:.0f} | {pr:.2f} |")
    L.append("")
    L.append("## 按月")
    L.append("")
    L.append("| 月份 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    df["month"] = df["t"].str[:7]
    for m, g in df.groupby("month"):
        L.append(f"| {m} | {len(g)} | {g['pnl'].mean():+.2f} | {(g['pnl'] > 0).mean() * 100:.0f} |")
    L.append("")
    L.append("## 情绪过滤（无纪律对照）")
    L.append("")
    L.append("| 过滤 | n | 均值% | 胜率% | 盈亏比 |")
    L.append("|---|---|---|---|---|")
    for name, cond in (("全量", df["pnl_nt"].notna()),
                       ("炸板率≤35", df["zhaban_rate"] <= 35),
                       ("涨停≥71", df["zt"] >= 71),
                       ("炸板≤35+涨停≥71", (df["zhaban_rate"] <= 35) & (df["zt"] >= 71))):
        g = df[cond].dropna(subset=["pnl_nt"])
        if len(g) >= 30:
            w = g[g["pnl_nt"] > 0]["pnl_nt"].mean()
            ls = g[g["pnl_nt"] < 0]["pnl_nt"].mean()
            pr = w / abs(ls) if ls else float("nan")
            L.append(f"| {name} | {len(g)} | {g['pnl_nt'].mean():+.2f} | {(g['pnl_nt'] > 0).mean() * 100:.0f} | {pr:.2f} |")
    L.append("")
    L.append("## 高连板子模式（2+ / 5+ 板，无纪律对照）")
    L.append("")
    L.append("| 子模式 | n | 均值% | 胜率% | 盈亏比 |")
    L.append("|---|---|---|---|---|")
    for name, cond in (("2+ 板", df["lb"] >= 2), ("3+ 板", df["lb"] >= 3),
                       ("5+ 板", df["lb"] >= 5), ("5+ 板+情绪过滤", (df["lb"] >= 5) & (df["zhaban_rate"] <= 35))):
        g = df[cond].dropna(subset=["pnl_nt"])
        if len(g) >= 10:
            w = g[g["pnl_nt"] > 0]["pnl_nt"].mean()
            ls = g[g["pnl_nt"] < 0]["pnl_nt"].mean()
            pr = w / abs(ls) if ls else float("nan")
            L.append(f"| {name} | {len(g)} | {g['pnl_nt'].mean():+.2f} | {(g['pnl_nt'] > 0).mean() * 100:.0f} | {pr:.2f} |")
    L.append("")
    L.append("## 退出原因")
    L.append("")
    L.append("| 原因 | n | 均值% |")
    L.append("|---|---|---|")
    for r_, g in df.groupby("reason"):
        L.append(f"| {r_} | {len(g)} | {g['pnl'].mean():+.2f} |")
    L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    outp = OUT_DIR / f"backtest_daban_{year}.md"
    outp.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {outp}  （总耗时 {(time.time()-t0)/60:.1f} 分钟）")
    store.close()


if __name__ == "__main__":
    main()