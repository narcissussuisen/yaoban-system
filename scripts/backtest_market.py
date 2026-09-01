"""全市场三档回测（F 盘 qfq 直读，2025/2026 分年）

档1 开盘执行 | 档2 B点执行（B2-only）| 档3 B点+做T（卖出管理引擎）
过滤：环境分（简版）≥ --min-env；情绪（涨停≥--min-zt / 炸板率≤--max-zhaban，读 sentiment_daily_{year}.csv）
信号：huigui(live)/zt_huicai/fanbao/xianren(确认) 并集，同股最小间隔 5 交易日
成本：佣金 0.025% 双边、印花税 0.05% 卖出、滑点 0.1% 单边；等权逐笔
用法: python scripts/backtest_market.py --year 2026 [--hold 1] [--max-zhaban 35] [--min-env 2] [--ten-oclock]
输出: outputs/backtest_market_{year}.md
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.store import Store  # noqa: E402
from data.qfq_store import QFQStore, build_daily_map  # noqa: E402
from core import strategies as S  # noqa: E402
from core.env_score import env_score  # noqa: E402
from core.intraday import day_b_points  # noqa: E402
from core.sell import simulate_hold  # noqa: E402

COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001
MIN_INTERVAL = 5
OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"


def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)
def fills_pnl(fills, eg, q):
    bc = sum(f["qty"] * buy_net(f["px"]) for f in fills if f["side"] == "buy")
    sc = sum(f["qty"] * sell_net(f["px"]) for f in fills if f["side"] == "sell")
    return (sc - bc) / (eg * q) * 100.0


def daily_exit_pnl(daily_rows: list, entry_date: str, entry_px: float, qty: int,
                     hold: int = 1, stop_pct: float = 0.05) -> float | None:
    """档1/档2 轻量退出（无盘中管理时与 simulate_hold(p_off) 等价）：
    hold=1 下 = 次日收盘清仓，除非当日低点触及 -5% 止损（按止损价）。
    返回净收益%（含成本）；数据不足返回 None。"""
    dates = [r[1] for r in daily_rows]
    if entry_date not in dates:
        return None
    i = dates.index(entry_date)
    if i + hold >= len(daily_rows):
        return None
    r = daily_rows[i + hold]
    stop_px = entry_px * (1 - stop_pct)
    # 修复（P3 评审阻断项）：止损用 LOW(r[4]) 判定（原 HIGH(r[3]) 几乎不触发）；
    # 开盘即破止损价 → 按开盘价成交（真实可达价）
    op, lo, cl = float(r[2]), float(r[4]), float(r[5])
    if lo <= stop_px:
        exit_px = op if op <= stop_px else stop_px
    else:
        exit_px = cl
    return (sell_net(exit_px) / buy_net(entry_px) - 1) * 100.0


def light_t3_pnl(qfq, sym: str, daily_rows: list, ed: str, entry_px: float,
                   hold: int = 1, stop_pct: float = 0.05,
                   ten_pct: float = 5.0) -> float | None:
    """轻量档3（全市场基线）：B2 入场 + 首个管理日 10 点纪律（10:00 涨幅<ten_pct 则按 10:00 价卖）
    + 否则日线退出（收盘/止损）。丢弃做T/炸板/次高点（小样本已验证 +0.2~0.5pp，方向不变）。"""
    dates = [r[1] for r in daily_rows]
    if ed not in dates:
        return None
    i = dates.index(ed)
    if i + hold >= len(daily_rows):
        return None
    d1 = dates[i + hold]
    # 10:00 判定（分钟读取当天）
    rows = qfq.get_minute(sym, start=d1, end=d1)
    if not rows:
        return None
    day = pd.DataFrame(rows, columns=["symbol", "freq", "ts", "open", "high",
                                      "low", "close", "volume", "amount"])
    # 昨收：d1 前一交易日收盘（day 单日无昨收，从日线取）
    prev_close = float(daily_rows[i + hold - 1][5]) if i + hold - 1 >= 0 else None
    row10 = day[day["ts"].str[11:16] >= "10:00"]
    if prev_close and not row10.empty:
        p10 = float(row10["close"].iloc[0])
        if p10 / prev_close - 1 < ten_pct / 100.0:
            return (sell_net(p10) / buy_net(entry_px) - 1) * 100.0
    # 日线退出
    r = daily_rows[i + hold]
    stop_px = entry_px * (1 - stop_pct)
    # 修复（P3 评审阻断项）：止损用 LOW(r[4]) 判定（原 HIGH(r[3]) 几乎不触发）；
    # 开盘即破止损价 → 按开盘价成交（真实可达价）
    op, lo, cl = float(r[2]), float(r[4]), float(r[5])
    if lo <= stop_px:
        exit_px = op if op <= stop_px else stop_px
    else:
        exit_px = cl
    return (sell_net(exit_px) / buy_net(entry_px) - 1) * 100.0


def is_stock(sym: str) -> bool:
    if sym.startswith(("399", "5", "15", "16")):
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default="2026")
    ap.add_argument("--hold", type=int, default=1)
    ap.add_argument("--min-env", type=int, default=0)
    ap.add_argument("--min-zt", type=int, default=0)
    ap.add_argument("--max-zhaban", type=float, default=100.0)
    ap.add_argument("--ten-oclock", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-symbols", type=int, default=0, help="调试用：只跑前 N 只")
    ap.add_argument("--symbols", default="", help="逗号分隔指定标的（与 --max-symbols 互斥）")
    ap.add_argument("--min-mom60", type=float, default=0.0, help="选股池：60日涨幅下限%（0=关）")
    ap.add_argument("--max-mom60", type=float, default=0.0, help="选股池：60日涨幅上限%（0=关）")
    ap.add_argument("--min-amt", type=float, default=0.0, help="选股池：20日均成交额下限（亿元，0=关）")
    ap.add_argument("--entry-open", action="store_true",
                    help="档3 改为开盘入场+10点纪律（动量池验证：B2 追高负贡献，开盘最优）")
    args = ap.parse_args()

    t_start = time.time()
    store = Store()
    # 指数（环境分）
    idx_df = pd.DataFrame(store.get_index("sh000001"),
                          columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    idx_dates = [r[1] for r in store.get_index("sh000001")]
    # 情绪
    sent = {}
    sp = OUT_DIR / f"sentiment_daily_{args.year}.csv"
    if sp.exists():
        sdf = pd.read_csv(sp)
        sent = {r["date"]: r for _, r in sdf.iterrows()}

    def env_simple_asof(d: str) -> int:
        sub = idx_df[idx_df["date"] <= d]
        if len(sub) < 25:
            return 0
        r = env_score(sub)
        return int(r["dims"]["trend"] + r["dims"]["volume"])

    # 1) 正股列表 + 并行日线聚合
    qfq = QFQStore(args.year)
    if args.symbols:
        # zfill(6)：防御 PowerShell 把 002176 数值化为 2176（逗号参数数字字面量解析）
        symbols = [s.strip().zfill(6) for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = qfq.symbols()
        symbols = [s for s in symbols if is_stock(s)]
        if args.max_symbols:
            symbols = symbols[:args.max_symbols]
    print(f"[{time.time()-t_start:.0f}s] 正股 {len(symbols)} 只，聚合日线…", flush=True)
    daily_map = build_daily_map(args.year, symbols, workers=args.workers)
    daily_map = {k: v for k, v in daily_map.items() if v}
    print(f"[{time.time()-t_start:.0f}s] 日线就绪 {len(daily_map)} 只，检测信号…", flush=True)

    # 2) 四战法信号
    signals: dict[str, list[str]] = {}
    sig_src: dict[str, dict[str, str]] = {}
    for sym, rows in daily_map.items():
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low",
                                         "close", "volume", "amount"])
        masks = {"huigui": S.detect_huigui(df, mode="live"),
                 "zt_huicai": S.detect_zt_huicai(df),
                 "fanbao": S.detect_fanbao(df),
                 "xianren": S.detect_xianren(df, with_confirm=True)}
        union = masks["huigui"] | masks["zt_huicai"] | masks["fanbao"] | masks["xianren"]
        days = df.loc[union, "date"].tolist()
        kept, last, src = [], None, {}
        for d in days:
            if last is None or idx_dates.index(d) - idx_dates.index(last) >= MIN_INTERVAL:
                kept.append(d)
                last = d
                for name, m in masks.items():
                    if bool(m.loc[df["date"] == d].iloc[0]) if (df["date"] == d).any() else False:
                        src[d] = name
                        break
        if kept:
            signals[sym] = kept
            sig_src[sym] = src
    n_sig = sum(len(v) for v in signals.values())
    print(f"[{time.time()-t_start:.0f}s] 信号 {n_sig} 个", flush=True)

    # 3) 执行候选（过滤后；含选股池因子过滤）
    # 预计算每只股票的 mom60/amt20 日序列（信号日截面）
    pool_f: dict[str, pd.DataFrame] = {}
    if args.min_mom60 or args.max_mom60 or args.min_amt:
        for sym, rows in daily_map.items():
            df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low",
                                             "close", "volume", "amount"])
            c = df["close"]
            f = pd.DataFrame(index=df.index)
            f["mom60"] = (c / c.shift(60) - 1) * 100
            f["amt20"] = df["amount"].rolling(20).mean() / 1e8
            f["date"] = df["date"].values
            pool_f[sym] = f
    cands = []  # (sym, sig_day, exec_day, src)
    for sym, days in signals.items():
        for d in days:
            if args.min_env and env_simple_asof(d) < args.min_env:
                continue
            sr = sent.get(d)
            if sr is not None:
                if args.min_zt and int(sr["zt"]) < args.min_zt:
                    continue
                if args.max_zhaban < 100 and float(sr["zhaban_rate"]) > args.max_zhaban:
                    continue
            if pool_f:
                f = pool_f.get(sym)
                if f is None:
                    continue
                fr = f[f["date"] == d]
                if fr.empty:
                    continue
                mom = float(fr["mom60"].iloc[0])
                amt = float(fr["amt20"].iloc[0])
                if pd.isna(mom):
                    continue
                if args.min_mom60 and mom < args.min_mom60:
                    continue
                if args.max_mom60 and mom > args.max_mom60:
                    continue
                if args.min_amt and amt < args.min_amt:
                    continue
            di = idx_dates.index(d)
            if di + 1 >= len(idx_dates):
                continue
            cands.append((sym, d, idx_dates[di + 1], sig_src.get(sym, {}).get(d, "?")))
    print(f"[{time.time()-t_start:.0f}s] 过滤后候选 {len(cands)} 个（env≥{args.min_env} "
          f"zt≥{args.min_zt} 炸板≤{args.max_zhaban}）", flush=True)

    # 4) 三档执行（按股票分组：每只股票只读一次 1m，避免 HDD 重复 IO）
    from collections import defaultdict
    from core.intraday import prev_close_of
    p_off = {"t_enabled": False, "vwap_halve": False, "break_low_clear": False,
             "ten_oclock": False, "profit_take_pct": 0.0, "zhaban_sell": False,
             "second_high_sell": False}
    bkw = dict(lookback=20, cool_min=30, open_win=30, surge_win=60)
    tiers = {"t1": [], "t2": [], "t3": []}
    ex = {}
    n_b2 = 0
    by_sym: dict[str, list] = defaultdict(list)
    for c in cands:
        by_sym[c[0]].append(c)
    done = 0
    for sym, clist in by_sym.items():
        # 预热：读一次 1m（进入缓存），日线已在 daily_map（模拟时走缓存）
        _ = qfq.get_minute(sym)
        for (sym2, d, ed, src) in clist:
            pts = day_b_points(qfq, sym, ed, freq="1m", **bkw)
            if not pts.empty:
                pts = pts[pts["kind"] == "B2"]
            if not pts.empty:
                mrows = qfq.get_minute(sym, start=ed, end=ed)
                if mrows:
                    day0 = pd.DataFrame(mrows, columns=["symbol", "freq", "ts", "open", "high",
                                                        "low", "close", "volume", "amount"])
                    pc = prev_close_of(day0, ed)
                    if pc and float(day0["low"].iloc[:30].min()) < pc * 0.97:
                        pts = pts.iloc[0:0]
            mrows = qfq.get_minute(sym, start=ed, end=ed)
            if not mrows:
                continue
            day = pd.DataFrame(mrows, columns=["symbol", "freq", "ts", "open", "high",
                                               "low", "close", "volume", "amount"])
            entry_ts1 = str(day["ts"].iloc[0])
            entry_px1 = float(day["open"].iloc[0])
            p1 = daily_exit_pnl(daily_map.get(sym, []), ed, entry_px1, 1000, hold=args.hold)
            if p1 is None:
                continue
            tiers["t1"].append({"sym": sym, "d": d, "ed": ed, "src": src, "pnl": p1})
            if args.entry_open:
                # 档3=开盘入场+10点纪律（动量池适配）：对所有候选执行
                p3 = light_t3_pnl(qfq, sym, daily_map.get(sym, []), ed, entry_px1,
                                  hold=args.hold, ten_pct=5.0 if args.ten_oclock else 100.0)
                if p3 is not None:
                    tiers["t3"].append({"sym": sym, "d": d, "ed": ed, "src": src, "pnl": p3,
                                        "exit": "ten_oclock", "t_rounds": 0})
                    ex["ten_oclock"] = ex.get("ten_oclock", 0) + 1
                continue
            if pts.empty:
                continue
            r0 = pts.iloc[0]
            entry_ts = str(r0["ts"])
            entry_px = float(r0["price"])
            n_b2 += 1
            p2 = daily_exit_pnl(daily_map.get(sym, []), ed, entry_px, 1000, hold=args.hold)
            if p2 is None:
                continue
            tiers["t2"].append({"sym": sym, "d": d, "ed": ed, "src": src, "pnl": p2})
            p3 = light_t3_pnl(qfq, sym, daily_map.get(sym, []), ed, entry_px,
                               hold=args.hold, ten_pct=5.0 if args.ten_oclock else 100.0)
            if p3 is None:
                continue
            tiers["t3"].append({"sym": sym, "d": d, "ed": ed, "src": src, "pnl": p3,
                                "exit": "ten_oclock", "t_rounds": 0})
            ex["ten_oclock"] = ex.get("ten_oclock", 0) + 1
            done += 1
            if done % 5000 == 0:
                print(f"[{time.time()-t_start:.0f}s] 执行 {done}/{len(cands)}", flush=True)

    # raw 输出（组合模拟用）
    if tiers["t3"]:
        pd.DataFrame(tiers["t3"]).to_csv(OUT_DIR / f"backtest_market_{args.year}_t3raw.csv", index=False)
    if tiers["t1"]:
        pd.DataFrame(tiers["t1"]).to_csv(OUT_DIR / f"backtest_market_{args.year}_t1raw.csv", index=False)
    # 5) 报告
    L = [f"# 全市场三档回测 {args.year}（qfq 直读）", "",
         f"> 正股 {len(daily_map)} 只，信号 {n_sig}，候选 {len(cands)}，B2 命中 {n_b2}；"
         f"hold={args.hold}，env≥{args.min_env or '无'}，zt≥{args.min_zt or '无'}，"
         f"炸板≤{args.max_zhaban if args.max_zhaban < 100 else '无'}%，"
         f"10点纪律={'开' if args.ten_oclock else '关'}；"
         f"选股池=mom60 {'%s~%s%%' % (args.min_mom60 or 0, args.max_mom60 or '∞')} + amt≥{args.min_amt or 0}亿。", "",
         "| 档位 | 笔数 | 均值% | 中位% | 胜率% | 总收益% | 最差% | 最好% |",
         "|---|---|---|---|---|---|---|---|"]
    names = {"t1": "① 开盘执行", "t2": "② B 点执行", "t3": "③ B 点+做T"}
    stats = {}
    for k in ("t1", "t2", "t3"):
        tl = tiers[k]
        if not tl:
            continue
        s = pd.Series([t["pnl"] for t in tl])
        stats[k] = s
        L.append(f"| {names[k]} | {len(tl)} | {s.mean():+.2f} | {s.median():+.2f} | "
                 f"{(s > 0).mean() * 100:.0f} | {s.sum():+.1f} | {s.min():+.2f} | {s.max():+.2f} |")
    L.append("")
    if "t2" in stats and "t1" in stats:
        L.append(f"- B 点过滤增量：均值 {stats['t2'].mean() - stats['t1'].mean():+.2f}pp")
    if "t3" in stats and "t2" in stats:
        L.append(f"- 管理增量：均值 {stats['t3'].mean() - stats['t2'].mean():+.2f}pp")
    if "t3" in stats:
        s3 = stats["t3"]
        L.append(f"- **结论：档3 均值 {s3.mean():+.2f}% / 总收益 {s3.sum():+.1f}pp → "
                 f"{'✅ 转正' if s3.mean() > 0 else '❌ 未转正'}**")
    L.append("")
    if tiers["t3"]:
        t3 = pd.DataFrame(tiers["t3"])
        t3["month"] = t3["d"].str[:7]
        L.append("### 档3 按月")
        L.append("")
        L.append("| 月份 | 笔数 | 均值% | 胜率% | 总收益% |")
        L.append("|---|---|---|---|---|")
        for m, g in t3.groupby("month"):
            L.append(f"| {m} | {len(g)} | {g['pnl'].mean():+.2f} | {(g['pnl'] > 0).mean() * 100:.0f} | {g['pnl'].sum():+.1f} |")
        L.append("")
        L.append("### 档3 按战法")
        L.append("")
        L.append("| 战法 | 笔数 | 均值% | 胜率% |")
        L.append("|---|---|---|---|")
        for sname, g in t3.groupby("src"):
            L.append(f"| {S.STRATEGY_NAMES.get(sname, sname)} | {len(g)} | {g['pnl'].mean():+.2f} | {(g['pnl'] > 0).mean() * 100:.0f} |")
        L.append("")
        L.append("### 档3 退出原因")
        L.append("")
        L.append("| 原因 | 笔数 |")
        L.append("|---|---|")
        for k, v in sorted(ex.items(), key=lambda x: -x[1]):
            L.append(f"| {k} | {v} |")
    L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    outp = OUT_DIR / f"backtest_market_{args.year}.md"
    outp.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[-50:]))
    print(f"\nREPORT -> {outp}  （总耗时 {(time.time()-t_start)/60:.1f} 分钟）")
    store.close()


if __name__ == "__main__":
    main()