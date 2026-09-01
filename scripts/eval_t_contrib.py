"""P2-A 做T 增量贡献评估（v2，复用 simulate_hold T+1 会计）

样本：形态池候选（pullback raw）随机抽 N 笔/年，ed 日开盘入场，持仓 3 日：
- 无管理：simulate_hold 全规则关闭（仅日线兜底）
- 管理：simulate_hold 默认规则（T 1/3仓 差价1.5% + 破线 + 止损）
增量 = 管理均值 − 无管理均值；gate: 增量 >= 0.9pp/3日
失败策略：变体一次（t_diff 1.5→2.5, t_frac 1/3→1/2）后归档。
用法: python -B scripts/eval_t_contrib.py --year 2025 --n 300 [--variant]
"""
import argparse, pathlib, random, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from data.qfq_store import QFQStore  # noqa: E402
from core.sell import simulate_hold  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001


def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)


def pnl_of(res, entry_px, entry_qty):
    """按现金流：Σ卖净 − Σ买净（含入场买），/入场毛额"""
    bc = sum(f["qty"] * buy_net(f["px"]) for f in res["fills"] if f["side"] == "buy")
    sc = sum(f["qty"] * sell_net(f["px"]) for f in res["fills"] if f["side"] == "sell")
    return (sc - bc) / (entry_px * entry_qty) * 100.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", required=True)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--variant", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    y = args.year
    t0 = time.time()
    cand = pd.read_csv(OUT / f"pullback_b2_{y}_raw.csv", dtype={"sym": str})
    sub = cand[cand["p_b2"].notna()].copy()
    if len(sub) > args.n:
        sub = sub.sample(n=args.n, random_state=args.seed)
    print(f"[{y}] 样本 {len(sub)} 笔（B2 可触发）", flush=True)

    store = QFQStore(y)
    rows = []
    for k, (_, r) in enumerate(sub.iterrows()):
        sym, ed = r["sym"], r["ed"]
        daily_rows = store.get_stock(sym)
        if not daily_rows:
            continue
        daily = pd.DataFrame(daily_rows, columns=["symbol", "date", "open", "high", "low",
                                                  "close", "volume", "amount"])
        all_dates = sorted(daily["date"].astype(str).unique().tolist())
        if ed not in all_dates:
            continue
        i = all_dates.index(ed)
        if i + 3 >= len(all_dates):
            continue
        entry_px = float(daily.iloc[i]["open"])
        if entry_px <= 0:
            continue
        entry_qty = 10000
        entry_ts = ed + " 09:31"
        # 无管理
        p_off = {"t_enabled": False, "vwap_halve": False, "break_low_clear": False,
                 "ten_oclock": False, "profit_take_pct": 0.0, "zhaban_sell": False,
                 "second_high_sell": False}
        r1 = simulate_hold(store, sym, entry_ts, entry_px, entry_qty, params=p_off,
                           horizon_days=3, daily_df=daily)
        # 管理（默认全开）或变体
        p_on = None
        if args.variant:
            from core.sell import DEFAULT_PARAMS
            p_on = dict(DEFAULT_PARAMS)
            p_on["t_diff_pct"] = 2.5
            p_on["t_frac"] = 0.5
        r2 = simulate_hold(store, sym, entry_ts, entry_px, entry_qty, params=p_on,
                           horizon_days=3, daily_df=daily)
        rows.append({"sym": sym, "ed": ed,
                     "no_mgmt": pnl_of(r1, entry_px, entry_qty),
                     "mgmt": pnl_of(r2, entry_px, entry_qty),
                     "t_rounds": r2["t_rounds"], "exit": r2["exit_reason"]})
        if (k + 1) % 50 == 0:
            print(f"  {k + 1}/{len(sub)}", flush=True)
    df = pd.DataFrame(rows).dropna()
    L = [f"# P2-A 做T 增量贡献（{y}，n={len(df)}，{'变体' if args.variant else '基线'}）", ""]
    L.append(f"> 形态候选随机抽样 {len(sub)} 笔，ed 开盘入场，持仓 3 日；simulate_hold T+1 会计。成本含。")
    L.append("")
    L.append("| 口径 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    L.append(f"| 无管理 | {len(df)} | {df['no_mgmt'].mean():+.2f} | {(df['no_mgmt']>0).mean()*100:.0f} |")
    L.append(f"| 管理 | {len(df)} | {df['mgmt'].mean():+.2f} | {(df['mgmt']>0).mean()*100:.0f} |")
    inc = (df["mgmt"] - df["no_mgmt"]).mean()
    L.append(f"| **增量** | {len(df)} | **{inc:+.2f}pp/3日**（{inc/3:+.2f}pp/日） | — |")
    L.append("")
    L.append(f"| 做T 触发笔数 | {(df['t_rounds']>0).sum()}（{(df['t_rounds']>0).mean()*100:.0f}%） |")
    has_t = df[df["t_rounds"] > 0]
    no_t = df[df["t_rounds"] == 0]
    if len(has_t) and len(no_t):
        L.append(f"| 触发T 笔均值 | {has_t['mgmt'].mean():+.2f}% vs 未触发 {no_t['mgmt'].mean():+.2f}% |")
    L.append("")
    L.append(f"gate: 增量 >= 0.9pp/3日 → {'✅ 达标' if inc >= 0.9 else '❌ 不达标'}")
    open(OUT / f"t_contribution_{y}.md", "w", encoding="utf-8").write(chr(10).join(L))
    print(chr(10).join(L), flush=True)
    print(f"[{time.time()-t0:.0f}s]", flush=True)
    store.close()


if __name__ == "__main__":
    main()