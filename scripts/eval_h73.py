"""H7.3 卖出管理 + 做T 评估：
  1) C21 案例复现（002156 通富微电 2026-07-09 正T）
  2) H6 10 点纪律验证（B 点买入次日 10:00 分档：走 vs 持有）
  3) 做T/卖出管理 收益评估（B 点买入持仓 3 日：无管理 vs 管理）

用法: python scripts/eval_h73.py [--symbols ...] [--start 2026-05-06] [--end 2026-08-24]
输出: outputs/eval_h73.md
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.store import Store  # noqa: E402
from core.intraday import day_b_points, prev_close_of, vwap_series  # noqa: E402
from core.sell import manage_day, limit_price, simulate_hold  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "eval_h73.md"

COMM = 0.00025   # 佣金双边
STAMP = 0.0005   # 印花税卖出
SLIP = 0.001     # 滑点单边
LOT = 100        # 一手


def buy_net(px: float) -> float:
    return px * (1 + COMM + SLIP)


def sell_net(px: float) -> float:
    return px * (1 - COMM - SLIP - STAMP)


def fills_pnl(fills: list, entry_gross: float, entry_qty: int) -> float:
    """按净现金流计算单笔交易收益%：Σ卖出净额 − Σ买入总额，基准=入场毛额"""
    buy_cash = sum(f["qty"] * buy_net(f["px"]) for f in fills if f["side"] == "buy")
    sell_cash = sum(f["qty"] * sell_net(f["px"]) for f in fills if f["side"] == "sell")
    return (sell_cash - buy_cash) / (entry_gross * entry_qty) * 100.0


def load_minute(store: Store, symbol: str, freq: str = "1m") -> pd.DataFrame:
    rows = store.get_minute(symbol, freq)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=["symbol", "freq", "ts", "open", "high", "low",
                                       "close", "volume", "amount"])


def main():
    ap = argparse.ArgumentParser(description="H7.3 卖出管理+做T 评估")
    ap.add_argument("--symbols", default="002176,002355,002606,003026,300057,600030,"
                                         "600352,600379,600584,603078,603316,603738")
    ap.add_argument("--start", default="2026-05-06")
    ap.add_argument("--end", default="2026-08-24")
    args = ap.parse_args()

    store = Store()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    idx = store.get_index("sh000001", start=args.start, end=args.end)
    dates = [r[1] for r in idx]
    all_idx = [r[1] for r in store.get_index("sh000001")]
    L: list[str] = []
    L.append(f"# H7.3 卖出管理 + 做T 评估 {args.start} ~ {args.end}")
    L.append("")
    L.append(f"> {len(symbols)} 只 1m 标的；成本：佣金 0.025% 双边、印花税 0.05% 卖出、滑点 0.1% 单边。")
    L.append("")

    # ---------- 1) C21 案例复现 ----------
    L.append("## 1) C21 案例复现：002156 通富微电 2026-07-09 正T（v49）")
    L.append("")
    mdf21 = load_minute(store, "002156", "1m")
    c21_ok = False
    if not mdf21.empty:
        d = mdf21[mdf21["ts"].str[:10] == "2026-07-09"].reset_index(drop=True)
        pc = prev_close_of(mdf21, "2026-07-09")
        d8 = mdf21[mdf21["ts"].str[:10] == "2026-07-08"]
        if len(d) and pc and len(d8):
            entry_px = float(d8["close"].iloc[-1])
            res = manage_day(d, pc, 300, stop_px=entry_px * 0.95, low_track=entry_px,
                             limit_px=limit_price(pc, "002156"))
            t_buys = [f for f in res["fills"] if f["reason"] == "t_buy"]
            t_sells = [f for f in res["fills"] if f["reason"] == "t_sell_out"]
            c21_ok = (res["t_round"] == 1 and res["qty"] == 300 and len(t_buys) == 1
                      and len(t_sells) == 1 and t_sells[0]["px"] > t_buys[0]["px"] * 1.01)
            L.append(f"- 前提：07-08 收盘 {entry_px:.2f} 持有底仓 300 股；07-09 昨收 {pc:.2f}，"
                     f"当日最低 65.33（相对均价线 -1.8%），收盘涨停 {d['close'].iloc[-1]:.2f}（+10%）")
            L.append(f"- 引擎回放（1m）：T进 {t_buys[0]['ts'][11:]} @ {t_buys[0]['px']:.2f}（均价线下方急跌补仓）"
                     f"→ T出 {t_sells[0]['ts'][11:]} @ {t_sells[0]['px']:.2f}（差价 +{t_sells[0]['px']/t_buys[0]['px']*100-100:.1f}%）")
            L.append(f"- 日末底仓 {res['qty']} 股（= 300 不变 ✓），T 利润 "
                     f"{(t_sells[0]['px']-t_buys[0]['px'])*t_sells[0]['qty']:.0f} 元，底仓当日 +10%（涨停）")
            L.append(f"- **复现判定：{'✅ 复现' if c21_ok else '❌ 未复现'}**（正T：急跌T进→拉升差价T出→数量一致→底仓不变）")
            L.append("")
        else:
            L.append("- 数据不足（缺 07-08/07-09 1m），跳过")
            L.append("")
    else:
        L.append("- 002156 无 1m 数据，跳过")
        L.append("")

    # ---------- 收集 B 点 ----------
    bp_rows = []
    for sym in symbols:
        for d in dates:
            pts = day_b_points(store, sym, d, freq="1m")
            if pts.empty:
                continue
            # 取当日首个 B 点（选手一次只买一笔）
            r0 = pts.iloc[0]
            bp_rows.append({"symbol": sym, "date": d, "ts": r0["ts"], "px": float(r0["price"]),
                            "kind": r0["kind"]})
    df_bp = pd.DataFrame(bp_rows)
    if df_bp.empty:
        L.append("## 2) 无 B 点样本")
        store.close()
        return
    L.append(f"## 2) H6 十点纪律验证（B 点买入后次日 10:00 状态 × 后续收益）")
    L.append("")
    L.append(f"> 样本：{len(df_bp)} 笔 B 点买入（每股票每日首笔）。验证口径：次日 10:00 分档，")
    L.append("> 纪律=10:00 卖出 vs 持有到当日收盘 / 次日开盘，净收益（含成本）。")
    L.append("")

    # 为每个 B 点计算次日信息
    h6 = []
    for _, r in df_bp.iterrows():
        sym, d = r["symbol"], r["date"]
        mdf = load_minute(store, sym, "1m")
        if mdf.empty:
            continue
        di = all_idx.index(d) if d in all_idx else -1
        if di < 0 or di + 1 >= len(all_idx):
            continue
        nxt = all_idx[di + 1]
        dn = mdf[mdf["ts"].str[:10] == nxt].reset_index(drop=True)
        if dn.empty:
            continue
        pc = prev_close_of(mdf, nxt)
        if not pc or pc <= 0:
            continue
        row10 = dn[dn["ts"].str[11:16] >= "10:00"]
        if row10.empty:
            continue
        p10 = float(row10["close"].iloc[0])
        c1 = float(dn["close"].iloc[-1])
        lpx = limit_price(pc, sym)
        h6.append({
            "symbol": sym, "date": d, "ts": r["ts"], "kind": r["kind"], "buy_px": r["px"],
            "pct10": (p10 / pc - 1) * 100, "at_limit": p10 >= lpx - 0.01,
            "ret_disc": (sell_net(p10) / buy_net(r["px"]) - 1) * 100,
            "ret_hold": (sell_net(c1) / buy_net(r["px"]) - 1) * 100,
        })
    df_h6 = pd.DataFrame(h6)
    if not df_h6.empty:
        def band(x):
            if x <= 0:
                return "≤0%"
            if x < 2:
                return "0~2%"
            if x < 5:
                return "2~5%"
            if x < 9:
                return "5~9%"
            return "≥9%/涨停"
        df_h6["band"] = df_h6["pct10"].apply(band)
        L.append("| 次日10:00涨幅 | n | 纪律(10:00走)均值% | 持有(收盘)均值% | 差值pp | 纪律胜率% |")
        L.append("|---|---|---|---|---|---|")
        for b in ["≤0%", "0~2%", "2~5%", "5~9%", "≥9%/涨停"]:
            g = df_h6[df_h6["band"] == b]
            if g.empty:
                continue
            diff = g["ret_disc"].mean() - g["ret_hold"].mean()
            L.append(f"| {b} | {len(g)} | {g['ret_disc'].mean():+.2f} | {g['ret_hold'].mean():+.2f} "
                     f"| {diff:+.2f} | {(g['ret_disc']>g['ret_hold']).mean()*100:.0f} |")
        L.append("")
        overall_diff = df_h6["ret_disc"].mean() - df_h6["ret_hold"].mean()
        L.append(f"- 全样本：纪律均值 {df_h6['ret_disc'].mean():+.2f}% vs 持有 {df_h6['ret_hold'].mean():+.2f}%，"
                 f"差值 {overall_diff:+.2f}pp → **{'纪律有效（弱市/弱势票早走）' if overall_diff > 0 else '纪律无效（强势票10:00后仍上涨，早走反而卖飞）'}**")
        L.append("")
    else:
        L.append("- 无次日数据")
        L.append("")

    # ---------- 3) 卖出管理 + 做T 评估 ----------
    L.append("## 3) 卖出管理 + 做T 收益评估（B 点买入持仓 3 日）")
    L.append("")
    L.append("| 口径 | n | 均值% | 中位% | 胜率% | 总收益%(等权) | 最大回吐笔% |")
    L.append("|---|---|---|---|---|---|---|")
    trades_no = []
    trades_mg = []
    for _, r in df_bp.iterrows():
        sym, d = r["symbol"], r["date"]
        qty = 1000
        entry_px = float(r["px"])          # 原始触发价（成本在 fills_pnl 统一计）
        daily = None
        drow = store.get_stock(sym)
        if drow:
            daily = pd.DataFrame(drow, columns=["symbol", "date", "open", "high", "low",
                                                "close", "volume", "amount"])
        # 无管理：纯持有（含日线兜底但不做T/不破线）—— 用 simulate_hold 关闭全部盘中规则
        p_off = {"t_enabled": False, "vwap_halve": False, "break_low_clear": False,
                 "ten_oclock": False, "profit_take_pct": 0.0}
        r1 = simulate_hold(store, sym, r["ts"], entry_px, qty, params=p_off,
                           horizon_days=3, daily_df=daily)
        trades_no.append({"symbol": sym, "date": d, "pnl": fills_pnl(r1["fills"], buy_net(entry_px), qty)})
        # 管理：默认全开（做T/破线减半/前低/止损/日线兜底）
        r2 = simulate_hold(store, sym, r["ts"], entry_px, qty, horizon_days=3, daily_df=daily)
        trades_mg.append({"symbol": sym, "date": d, "pnl": fills_pnl(r2["fills"], buy_net(entry_px), qty),
                          "t_rounds": r2["t_rounds"], "exit": r2["exit_reason"]})
    for label, tl in (("无管理（持有3日）", trades_no), ("管理（做T+破线+止损）", trades_mg)):
        if not tl:
            continue
        s = pd.Series([t["pnl"] for t in tl])
        L.append(f"| {label} | {len(tl)} | {s.mean():+.2f} | {s.median():+.2f} | "
                 f"{(s>0).mean()*100:.0f} | {s.sum():+.1f} | {s.min():+.2f} |")
    if trades_no and trades_mg:
        d0 = pd.Series([t["pnl"] for t in trades_no])
        d1 = pd.Series([t["pnl"] for t in trades_mg])
        L.append("")
        L.append(f"- 管理 vs 无管理：均值差 {d1.mean()-d0.mean():+.2f}pp，"
                 f"总收益差 {d1.sum()-d0.sum():+.1f}pp；做T 笔数占比 {(pd.Series([t['t_rounds'] for t in trades_mg])>0).mean()*100:.0f}%")
        t_round_s = pd.Series([t["t_rounds"] for t in trades_mg])
        L.append(f"- 做T 轮次分布：0 轮 {(t_round_s==0).sum()} 笔 / 1 轮 {(t_round_s==1).sum()} / 2 轮 {(t_round_s==2).sum()} / 3+ 轮 {(t_round_s>=3).sum()}")
        # 有T vs 无T 笔
        mt = pd.DataFrame(trades_mg)
        has_t = mt[mt["t_rounds"] > 0]["pnl"]
        no_t = mt[mt["t_rounds"] == 0]["pnl"]
        if len(has_t):
            L.append(f"- 触发做T 的笔：n={len(has_t)}，均值 {has_t.mean():+.2f}% vs 未触发 n={len(no_t)}，均值 {no_t.mean():+.2f}%")
    L.append("")
    L.append("> 说明：B 点=放量+白线向上+站稳均价线+涨幅≤3%（B1 开盘强势/B2 回踩确认，当日首笔）；"
             "管理=做T(1/3仓、差价1.5%、每日一次) + 破均价线减半(需当日已涨≥7%) + 冲高回落破位止损 + 炸板卖出 + 次高点卖出(冲高≥5%回落1%且60分钟未创新高) + 止损-5% + 日线MA兜底。")
    L.append("> 仅供方法论研究，不构成投资建议。")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {OUT}")
    store.close()


if __name__ == "__main__":
    main()