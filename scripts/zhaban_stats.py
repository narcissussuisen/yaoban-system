"""H7.5 炸板信号验证（v50/v55「封板不坚决=落袋」的分钟级检验）

对 B 点买入样本（450 笔）持仓期（含买入日）内：
  - 炸板日 = 当日触及涨停价但收盘未封住（封单失败）
  - 对比：炸板日收盘卖出 vs 持有到次日开盘/次日收盘；以及炸板日当天
    「首次回落后」卖出 vs 当日收盘卖出。
输出: outputs/zhaban_stats.md
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.store import Store  # noqa: E402
from core.intraday import day_b_points, prev_close_of  # noqa: E402
from core.sell import limit_price  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "zhaban_stats.md"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001


def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)


def main():
    store = Store()
    symbols = "002176,002355,002606,003026,300057,600030,600352,600379,600584,603078,603316,603738".split(",")
    dates = [r[1] for r in store.get_index("sh000001", start="2026-05-06", end="2026-08-24")]
    all_idx = [r[1] for r in store.get_index("sh000001")]

    rows = []
    for sym in symbols:
        mdf = pd.DataFrame(store.get_minute(sym, "1m"),
                           columns=["symbol", "freq", "ts", "open", "high", "low",
                                    "close", "volume", "amount"])
        if mdf.empty:
            continue
        for d in dates:
            pts = day_b_points(store, sym, d, freq="1m")
            if pts.empty:
                continue
            r0 = pts.iloc[0]
            buy_px = float(r0["price"])
            di = all_idx.index(d)
            for off in range(1, 4):  # 持仓日 +1..+3
                if di + off >= len(all_idx):
                    break
                hd = all_idx[di + off]
                day = mdf[mdf["ts"].str[:10] == hd]
                if day.empty:
                    continue
                pc = prev_close_of(mdf, hd)
                if not pc:
                    continue
                lp = limit_price(pc, sym)
                touched = float(day["high"].max()) >= lp - 0.01
                sealed = float(day["close"].iloc[-1]) >= lp - 0.01
                if not touched:
                    continue  # 只关心触及涨停的样本
                # 炸板时刻：触及涨停后，第一根收盘 < 涨停价×(1-0.5%) 的 bar
                at_limit = day["high"] >= lp - 0.01
                fell = (day["close"] < lp * (1 - 0.005)) & at_limit
                fell_idx = fell.idxmax() if fell.any() else None
                fall_px = float(day.loc[fell_idx, "close"]) if fell_idx is not None else None
                close_px = float(day["close"].iloc[-1])
                # 次日开盘
                nxt_open = None
                if di + off + 1 < len(all_idx):
                    nd = mdf[mdf["ts"].str[:10] == all_idx[di + off + 1]]
                    if not nd.empty:
                        nxt_open = float(nd["open"].iloc[0])
                rows.append({
                    "sym": sym, "buy_day": d, "day": hd, "buy_px": buy_px,
                    "zhaban": touched and not sealed,
                    "ret_close": (sell_net(close_px) / buy_net(buy_px) - 1) * 100,
                    "ret_fall": (sell_net(fall_px) / buy_net(buy_px) - 1) * 100 if fall_px else None,
                    "ret_next_open": (sell_net(nxt_open) / buy_net(buy_px) - 1) * 100 if nxt_open else None,
                })
    df = pd.DataFrame(rows)
    L = ["# 炸板信号验证（触及涨停 vs 封板失败）", "",
         f"> 样本：450 笔 B 点买入 × 持仓期 +1~+3 日，触及涨停价的 {len(df)} 个交易日。", ""]
    if df.empty:
        L.append("无样本")
        OUT.write_text("\n".join(L), encoding="utf-8")
        return
    zb = df[df["zhaban"]]
    ok = df[~df["zhaban"]]
    L.append("| 分组 | n | 当日收盘均值% | 次日开盘均值% |")
    L.append("|---|---|---|---|")
    for lab, g in (("封住涨停（持有）", ok), ("炸板（未封住）", zb)):
        L.append(f"| {lab} | {len(g)} | {g['ret_close'].mean():+.2f} | "
                 f"{g['ret_next_open'].dropna().mean():+.2f} |")
    L.append("")
    if len(zb):
        L.append("### 炸板日：炸板时刻卖出 vs 持有到收盘")
        L.append("")
        g = zb[zb["ret_fall"].notna()]
        if len(g):
            diff = (g["ret_fall"] - g["ret_close"]).mean()
            L.append(f"- n={len(g)}：炸板时刻卖出均值 {g['ret_fall'].mean():+.2f}% vs 持有到收盘 "
                     f"{g['ret_close'].mean():+.2f}%，差值 {diff:+.2f}pp，"
                     f"卖出更优占比 {(g['ret_fall']>g['ret_close']).mean()*100:.0f}%")
        L.append("")
        L.append("### 炸板日次日")
        L.append("")
        nxt = zb["ret_next_open"].dropna()
        okn = ok["ret_next_open"].dropna()
        L.append(f"- 炸板次日开盘均值 {nxt.mean():+.2f}%（n={len(nxt)}）vs 封住次日 {okn.mean():+.2f}%（n={len(okn)}）")
    L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {OUT}")
    store.close()


if __name__ == "__main__":
    main()