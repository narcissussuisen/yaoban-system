"""H7.5 参数敏感性网格：转正结论是否落在参数高原上

在 12 只 1m 标的、hold=1、env≥2、10点纪律、B2-only 口径上，
单参数扫描（其余取基线）：ten_strong_pct / t_diff_pct / t_buy_dev / B点涨幅上限 max_pct / t_frac
输出: outputs/grid_sensitivity.md
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.store import Store  # noqa: E402
from core import strategies as S  # noqa: E402
from core.env_score import env_score  # noqa: E402
from core.intraday import day_b_points  # noqa: E402
from core.sell import simulate_hold  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "grid_sensitivity.md"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001
MIN_INTERVAL = 5


def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)
def fills_pnl(fills, eg, q):
    bc = sum(f["qty"] * buy_net(f["px"]) for f in fills if f["side"] == "buy")
    sc = sum(f["qty"] * sell_net(f["px"]) for f in fills if f["side"] == "sell")
    return (sc - bc) / (eg * q) * 100.0


def build_pool(store, symbols, dates, all_idx, idx_df, min_env):
    """返回候选执行单: (sym, signal_day, exec_day, bpoint_ts, bpoint_px, daily_df)"""
    cands = []
    for sym in symbols:
        rows = store.get_stock(sym)
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low",
                                         "close", "volume", "amount"])
        masks = {"huigui": S.detect_huigui(df, mode="live"),
                 "zt": S.detect_zt_huicai(df),
                 "fb": S.detect_fanbao(df),
                 "xr": S.detect_xianren(df, with_confirm=True)}
        union = masks["huigui"] | masks["zt"] | masks["fb"] | masks["xr"]
        sig_days = [d for d in df.loc[union, "date"] if d in dates]
        kept, last = [], None
        for d in sig_days:
            if last is None or all_idx.index(d) - all_idx.index(last) >= MIN_INTERVAL:
                kept.append(d)
                last = d
        for d in kept:
            if min_env:
                sub = idx_df[idx_df["date"] <= d]
                if len(sub) >= 25:
                    r = env_score(sub)
                    if r["dims"]["trend"] + r["dims"]["volume"] < min_env:
                        continue
            di = all_idx.index(d)
            if di + 1 >= len(all_idx):
                continue
            ed = all_idx[di + 1]
            cands.append({"sym": sym, "d": d, "ed": ed, "daily": df})
    return cands


def run_tier3(store, cand, max_pct, params_extra, freq="1m", bkw=None):
    """返回 tier3 单笔 pnl；无 B2/开盘走弱 → None"""
    sym, ed = cand["sym"], cand["ed"]
    bkw = bkw or dict(lookback=20, cool_min=30, open_win=30, surge_win=60)
    pts = day_b_points(store, sym, ed, freq=freq, max_pct=max_pct, **bkw)
    if pts.empty:
        return None
    pts = pts[pts["kind"] == "B2"]
    if pts.empty:
        return None
    r0 = pts.iloc[0]
    # 开盘走弱
    mrows = store.get_minute(sym, freq)
    if not mrows:
        return None
    day = pd.DataFrame(mrows, columns=["symbol", "freq", "ts", "open", "high", "low",
                                       "close", "volume", "amount"])
    dd = day[day["ts"].str[:10] == ed]
    if dd.empty:
        return None
    from core.intraday import prev_close_of
    pc = prev_close_of(day, ed)
    if pc and float(dd["low"].iloc[:30].min()) < pc * 0.97:
        return None
    entry_px = float(r0["price"])
    r3 = simulate_hold(store, sym, str(r0["ts"]), entry_px, 1000, params=params_extra,
                       horizon_days=1, daily_df=cand["daily"], freq=freq)
    if len(r3["fills"]) <= 1:
        return None
    return fills_pnl(r3["fills"], entry_px, 1000)


def main():
    store = Store()
    symbols = "002176,002355,002606,003026,300057,600030,600352,600379,600584,603078,603316,603738".split(",")
    all_idx = [r[1] for r in store.get_index("sh000001")]
    dates = set(r[1] for r in store.get_index("sh000001", start="2026-05-06", end="2026-08-24"))
    idx_df = pd.DataFrame(store.get_index("sh000001"),
                          columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    cands = build_pool(store, symbols, dates, all_idx, idx_df, min_env=2)
    base_params = {"ten_oclock": True}

    L = ["# 参数敏感性网格（12 只 1m，hold=1，env≥2，10点纪律，B2-only）", "",
         "> 基线参数：ten_strong_pct=5 / t_diff_pct=1.5 / t_buy_dev=1.5 / max_pct=3% / t_frac=1/3",
         "> 单参数扫描，其余取基线；每格给出 档3 均值/胜率/笔数，检验「转正」结论是否在参数高原上。", ""]

    grids = [
        ("ten_strong_pct（10点走强阈值%）", "ten_strong_pct", [3.0, 5.0, 7.0]),
        ("t_diff_pct（做T差价%）", "t_diff_pct", [1.0, 1.5, 2.0, 2.5]),
        ("t_buy_dev（正T急跌触发%）", "t_buy_dev", [1.0, 1.5, 2.0]),
        ("max_pct（B点涨幅上限%）", "max_pct", [0.02, 0.03, 0.05]),
        ("t_frac（做T仓位）", "t_frac", [0.25, 0.3333, 0.5]),
    ]
    for title, key, vals in grids:
        L.append(f"### {title}")
        L.append("")
        L.append("| 参数值 | 均值% | 中位% | 胜率% | 总收益% | 笔数 |")
        L.append("|---|---|---|---|---|---|")
        for v in vals:
            extra = dict(base_params)
            if key == "max_pct":
                mp = v
                extra = base_params
            else:
                mp = 0.03
                extra[key] = v
            pnls = []
            for c in cands:
                p = run_tier3(store, c, mp, extra)
                if p is not None:
                    pnls.append(p)
            s = pd.Series(pnls)
            L.append(f"| {v} | {s.mean():+.2f} | {s.median():+.2f} | {(s>0).mean()*100:.0f} "
                     f"| {s.sum():+.1f} | {len(s)} |")
        L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {OUT}")
    store.close()


if __name__ == "__main__":
    main()