"""H7.4 两级回测三档对比：开盘执行 vs B 点执行 vs B 点+做T

直接回答: 「B 点 + 做T 能否让回测转正」——同一批日线信号（huigui live / zt_huicai /
fanbao / xianren 确认），三种执行口径的逐笔收益对比。

  档1 开盘执行: 信号日次日开盘买入（现有日线口径），日线兜底退出
  档2 B 点执行: 次日出现 B 点才买（默认仅 B2 回踩确认；--allow-b1 加 B1 且收紧至 ≤2%），
               无 B 点 / 开盘走弱 → 放弃；日线兜底退出
  档3 B 点+做T: 入场同档2，退出用盘中卖出管理引擎（做T/破均价线减半/破前低/止损/日线兜底）

可选过滤: --min-env N（信号日简版环境分 trend+volume ≥ N 才执行，N=0 关闭）
成本: 佣金 0.025% 双边、印花税 0.05% 卖出、滑点 0.1% 单边（含做T 每笔）
输出: outputs/backtest_dual.md
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.store import Store  # noqa: E402
from core import strategies as S  # noqa: E402
from core.env_score import env_score  # noqa: E402
from core.intraday import day_b_points, prev_close_of  # noqa: E402
from core.sell import simulate_hold  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "backtest_dual.md"
COMM = 0.00025
STAMP = 0.0005
SLIP = 0.001
MIN_INTERVAL = 5  # 同股信号去重：最小间隔交易日


def buy_net(px: float) -> float:
    return px * (1 + COMM + SLIP)


def sell_net(px: float) -> float:
    return px * (1 - COMM - SLIP - STAMP)


def fills_pnl(fills: list, entry_gross: float, entry_qty: int) -> float:
    buy_cash = sum(f["qty"] * buy_net(f["px"]) for f in fills if f["side"] == "buy")
    sell_cash = sum(f["qty"] * sell_net(f["px"]) for f in fills if f["side"] == "sell")
    return (sell_cash - buy_cash) / (entry_gross * entry_qty) * 100.0


def daily_df_of(store: Store, sym: str) -> pd.DataFrame:
    rows = store.get_stock(sym)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close",
                                     "volume", "amount"])
    for c in ("open", "high", "low", "close", "volume", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def main():
    ap = argparse.ArgumentParser(description="H7.4 三档执行对比")
    ap.add_argument("--symbols", default="002176,002355,002606,003026,300057,600030,"
                                         "600352,600379,600584,603078,603316,603738")
    ap.add_argument("--universe", action="store_true", help="全部有分钟数据的标的（5m 全池）")
    ap.add_argument("--start", default="2026-05-06")
    ap.add_argument("--end", default="2026-08-24")
    ap.add_argument("--min-env", type=int, default=0, help="信号日简版环境分门槛（0=关闭）")
    ap.add_argument("--allow-b1", action="store_true", help="允许 B1（收紧至涨幅≤2%）")
    ap.add_argument("--hold", type=int, default=3, help="最长持有交易日（含入场日）")
    ap.add_argument("--ten-oclock", action="store_true", help="档3 启用 10 点纪律（10:00 未走强则出）")
    ap.add_argument("--freq", default="1m", choices=["1m", "5m"],
                    help="分钟频率（5m 可扩至全池 84 只；B 点时间窗口参数按频率换算）")
    ap.add_argument("--min-zt", type=int, default=0, help="信号日全市场涨停家数下限（情绪过滤，0=关）")
    ap.add_argument("--max-zhaban", type=float, default=100.0, help="信号日炸板率上限%（情绪过滤，100=关）")
    ap.add_argument("--fanbao-gain", type=float, default=0.0,
                    help="fanbao 反包日收盘涨幅上限%（0=不过滤。注：全池 5353 信号验证显示高涨幅组后5日更好，过滤无依据）")
    args = ap.parse_args()

    store = Store()
    # 全市场情绪日数据（qfq 2026 计算，outputs/sentiment_daily.csv）
    _sent_path = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "sentiment_daily.csv"
    _sent = {}
    if _sent_path.exists():
        _sdf = pd.read_csv(_sent_path)
        _sent = {r["date"]: r for _, r in _sdf.iterrows()}
    def sent_ok(sig_day: str) -> bool:
        r = _sent.get(sig_day)
        if r is None:
            return True  # 无情绪数据不拦截
        if args.min_zt and int(r["zt"]) < args.min_zt:
            return False
        if args.max_zhaban < 100 and float(r["zhaban_rate"]) > args.max_zhaban:
            return False
        return True
    if args.universe:
        symbols = [r[0] for r in store.conn.execute(
            "SELECT DISTINCT symbol FROM stock_daily ORDER BY symbol").fetchall()]
    else:
        symbols = [s.strip().zfill(6) for s in args.symbols.split(",") if s.strip()]
    all_idx = [r[1] for r in store.get_index("sh000001")]
    dates = [r[1] for r in store.get_index("sh000001", start=args.start, end=args.end)]
    idx_df = pd.DataFrame(store.get_index("sh000001"),
                          columns=["symbol", "date", "open", "high", "low", "close", "volume"])

    def env_simple_asof(d: str) -> int:
        sub = idx_df[idx_df["date"] <= d]
        if len(sub) < 25:
            return 0
        r = env_score(sub)
        return int(r["dims"]["trend"] + r["dims"]["volume"])

    # ---------- 日线信号（含去重） ----------
    signals: dict[str, list[str]] = {}
    sig_src: dict[str, dict[str, str]] = {}   # sym -> {day: strategy}
    for sym in symbols:
        df = daily_df_of(store, sym)
        if df.empty:
            continue
        fb_o = {"max_gain_pct": args.fanbao_gain} if args.fanbao_gain else {}
        masks = {
            "huigui": S.detect_huigui(df, mode="live"),
            "zt_huicai": S.detect_zt_huicai(df),
            "fanbao": S.detect_fanbao(df, overrides=fb_o),
            "xianren": S.detect_xianren(df, with_confirm=True),
        }
        union = (masks["huigui"] | masks["zt_huicai"] | masks["fanbao"] | masks["xianren"])
        sig_days = df.loc[union, "date"].tolist()
        # 信号日必须在执行窗口内（次日有 1m 数据）
        sig_days = [d for d in sig_days if d in dates]
        # 去重：同股 ≥5 个交易日间隔
        kept = []
        last = None
        src_map: dict[str, str] = {}
        for d in sig_days:
            if last is None or all_idx.index(d) - all_idx.index(last) >= MIN_INTERVAL:
                kept.append(d)
                last = d
                for name, m in masks.items():
                    if bool(m.loc[df["date"] == d].iloc[0]) if (df["date"] == d).any() else False:
                        src_map[d] = name
                        break
        if kept:
            signals[sym] = kept
            sig_src[sym] = src_map

    L: list[str] = []
    L.append(f"# H7.4 两级回测：开盘执行 vs B 点执行 vs B 点+做T  {args.start} ~ {args.end}")
    L.append("")
    L.append(f"> 标的 {len(symbols)} 只（{args.freq} 数据）；日线信号：huigui(live)/zt_huicai/fanbao/xianren(确认) 并集，"
             f"同股最小间隔 {MIN_INTERVAL} 日；执行日=信号日次日；最长持有 {args.hold} 交易日。")
    L.append(f"> 过滤：B1={'允许(涨幅≤2%)' if args.allow_b1 else '禁用（仅 B2 回踩确认）'}；"
             f"环境分门槛={args.min_env or '无'}；开盘走弱（30 分钟破昨收-3%）放弃；"
             f"10点纪律={'开' if args.ten_oclock else '关'}（档3）；"
             f"情绪过滤=涨停≥{args.min_zt or '无'}、炸板率≤{args.max_zhaban if args.max_zhaban<100 else '无'}%。")
    L.append("> 成本：佣金 0.025% 双边、印花税 0.05% 卖出、滑点 0.1% 单边；等权逐笔口径（不建模持仓上限）。")
    L.append("")
    L.append(f"### 信号分布：共 {sum(len(v) for v in signals.values())} 个信号日")
    L.append("")
    for sym, days in signals.items():
        L.append(f"- {sym}: {len(days)} 个（{days[0] if days else '-'} ~ {days[-1] if days else '-'}）")
    L.append("")

    # ---------- 三档执行 ----------
    tiers = {"t1": [], "t2": [], "t3": []}
    exit_reasons: dict[str, dict] = {"t3": {}}
    for sym, sig_days in signals.items():
        mrows = store.get_minute(sym, args.freq)
        if not mrows:
            continue
        for d in sig_days:
            if not sent_ok(d):
                continue
            if args.min_env and env_simple_asof(d) < args.min_env:
                continue
            di = all_idx.index(d)
            if di + 1 >= len(all_idx):
                continue
            ed = all_idx[di + 1]
            # 执行日 B 点（仅 B2，或 B1 收紧）；参数按频率换算（时间窗口等价）
            bkw = {"1m": dict(lookback=20, cool_min=30, open_win=30, surge_win=60),
                   "5m": dict(lookback=4, cool_min=6, open_win=6, surge_win=12)}[args.freq]
            pts = day_b_points(store, sym, ed, freq=args.freq, **bkw)
            if not pts.empty:
                if not args.allow_b1:
                    pts = pts[pts["kind"] == "B2"]
                else:
                    pts = pts[(pts["kind"] == "B2") | ((pts["kind"] == "B1") & (pts["pct"] <= 2.0))]
            if pts.empty:
                # 无 B 点 → 档2/3 放弃；档1 仍执行（开盘买）
                pass
            else:
                r0 = pts.iloc[0]
                # 开盘走弱过滤
                day = pd.DataFrame(mrows, columns=["symbol", "freq", "ts", "open", "high",
                                                   "low", "close", "volume", "amount"])
                dd = day[day["ts"].str[:10] == ed]
                if not dd.empty:
                    pc = prev_close_of(day, ed)
                    if pc and float(dd["low"].iloc[:30].min()) < pc * 0.97:
                        continue
            qty = 1000
            daily = daily_df_of(store, sym)
            # 档1：开盘执行
            day = pd.DataFrame(mrows, columns=["symbol", "freq", "ts", "open", "high",
                                               "low", "close", "volume", "amount"])
            dd = day[day["ts"].str[:10] == ed]
            if dd.empty:
                continue
            entry_ts1 = str(dd["ts"].iloc[0])
            entry_px1 = float(dd["open"].iloc[0])
            p_off = {"t_enabled": False, "vwap_halve": False, "break_low_clear": False,
                     "ten_oclock": False, "profit_take_pct": 0.0}
            r1 = simulate_hold(store, sym, entry_ts1, entry_px1, qty, params=p_off,
                               horizon_days=args.hold, daily_df=daily)
            if len(r1["fills"]) <= 1:
                continue  # 未真正执行（数据缺口），不计入
            tiers["t1"].append({"symbol": sym, "date": d, "ed": ed,
                                "pnl": fills_pnl(r1["fills"], entry_px1, qty)})
            # 档2/3：B 点执行（无 B 点则放弃）
            if pts.empty:
                continue
            r0 = pts.iloc[0]
            entry_ts = str(r0["ts"])
            entry_px = float(r0["price"])
            r2 = simulate_hold(store, sym, entry_ts, entry_px, qty, params=p_off,
                               horizon_days=args.hold, daily_df=daily)
            if len(r2["fills"]) <= 1:
                continue
            tiers["t2"].append({"symbol": sym, "date": d, "ed": ed,
                                "pnl": fills_pnl(r2["fills"], entry_px, qty)})
            p3 = {"ten_oclock": args.ten_oclock}
            try:
                r3 = simulate_hold(store, sym, entry_ts, entry_px, qty, params=p3,
                                   horizon_days=args.hold, daily_df=daily)
            except Exception as ex:  # noqa: BLE001 - 报告后跳过该笔
                print(f"  [tier3 FAIL] {sym} {d}->{ed} {entry_ts}: {ex}")
                continue
            tiers["t3"].append({"symbol": sym, "date": d, "ed": ed,
                                "pnl": fills_pnl(r3["fills"], entry_px, qty),
                                "t_rounds": r3["t_rounds"], "exit": r3["exit_reason"],
                                "src": sig_src.get(sym, {}).get(d, "?")})
            exit_reasons["t3"][r3["exit_reason"]] = exit_reasons["t3"].get(r3["exit_reason"], 0) + 1

    L.append("## 三档对比")
    L.append("")
    L.append("| 档位 | 执行笔数 | 均值% | 中位% | 胜率% | 总收益%(等权) | 最差笔% | 最好笔% |")
    L.append("|---|---|---|---|---|---|---|---|")
    names = {"t1": "① 开盘执行", "t2": "② B 点执行", "t3": "③ B 点+做T"}
    stats = {}
    for k in ("t1", "t2", "t3"):
        tl = tiers[k]
        if not tl:
            continue
        s = pd.Series([t["pnl"] for t in tl])
        stats[k] = s
        L.append(f"| {names[k]} | {len(tl)} | {s.mean():+.2f} | {s.median():+.2f} | "
                 f"{(s>0).mean()*100:.0f} | {s.sum():+.1f} | {s.min():+.2f} | {s.max():+.2f} |")
    L.append("")
    if "t2" in stats and "t1" in stats:
        L.append(f"- B 点执行 vs 开盘执行：均值 {stats['t2'].mean()-stats['t1'].mean():+.2f}pp，"
                 f"总收益 {stats['t2'].sum()-stats['t1'].sum():+.1f}pp")
    if "t3" in stats and "t2" in stats:
        L.append(f"- B 点+做T vs B 点执行：均值 {stats['t3'].mean()-stats['t2'].mean():+.2f}pp，"
                 f"总收益 {stats['t3'].sum()-stats['t2'].sum():+.1f}pp")
    if "t3" in stats:
        s3 = stats["t3"]
        verdict = "✅ 转正" if s3.mean() > 0 and s3.sum() > 0 else "❌ 未转正"
        L.append(f"- **结论（等权逐笔口径）：B 点+做T 档均值 {s3.mean():+.2f}% / 总收益 {s3.sum():+.1f}pp → {verdict}**")
    L.append("")

    if tiers["t3"]:
        t3 = pd.DataFrame(tiers["t3"])
        L.append("## ③ 档明细拆解")
        L.append("")
        L.append("### 按月")
        L.append("")
        t3["month"] = t3["date"].str[:7]
        L.append("| 月份 | 笔数 | 均值% | 胜率% | 总收益% |")
        L.append("|---|---|---|---|---|")
        for m, g in t3.groupby("month"):
            L.append(f"| {m} | {len(g)} | {g['pnl'].mean():+.2f} | {(g['pnl']>0).mean()*100:.0f} | {g['pnl'].sum():+.1f} |")
        L.append("")
        L.append("### 退出原因分布")
        L.append("")
        L.append("| 原因 | 笔数 |")
        L.append("|---|---|")
        for k, v in sorted(exit_reasons["t3"].items(), key=lambda x: -x[1]):
            L.append(f"| {k} | {v} |")
        L.append("")
        L.append("### 做T 轮次")
        L.append("")
        ts = t3["t_rounds"]
        L.append(f"- 触发做T 笔数：{(ts>0).sum()}/{len(ts)}（{(ts>0).mean()*100:.0f}%），总轮次 {ts.sum()}")
        L.append("")
        L.append("### 按信号战法")
        L.append("")
        L.append("| 战法 | 笔数 | 均值% | 胜率% |")
        L.append("|---|---|---|---|")
        for sname, g in t3.groupby("src"):
            L.append(f"| {S.STRATEGY_NAMES.get(sname, sname)} | {len(g)} | {g['pnl'].mean():+.2f} | {(g['pnl']>0).mean()*100:.0f} |")
        L.append("")

    L.append("> 口径说明：档1 用信号日次日开盘价（含滑点）；档2/3 用次日首个 B 点触发价；"
             "无 B 点/开盘走弱 → 档2/3 放弃该信号（档1 照买）。退出=卖出管理引擎（档3）或日线兜底（档1/2）："
             "MA5 破减半、MA10 破清、止损 -5%、时间止损 5 日。等权逐笔，不含持仓上限与仓位管理。")
    L.append("> 仅供方法论研究，不构成投资建议。")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {OUT}")
    store.close()


if __name__ == "__main__":
    main()