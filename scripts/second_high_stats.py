"""P2 次高点卖出规则独立验证（v54/v58/v62：冲高无力突破前高就走）

规则：日内创出新高（相对昨收 ≥ min_surge%）后回落 ≥ pull%，且 confirm_n 根未再创新高 → 卖。
验证：450 笔 B 点买入持仓期（+1~+3 日），触发时点卖出 vs 持有收盘/次日开盘（含成本）。
输出: outputs/second_high_stats.md
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.store import Store  # noqa: E402
from core.intraday import day_b_points, prev_close_of  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "second_high_stats.md"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001


def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)


def scan_second_high(day, prev_close, buy_px, pull_pct=1.0, confirm_n=30, min_surge=2.0):
    high = day["high"].to_numpy()
    close = day["close"].to_numpy()
    ts = day["ts"].tolist()
    n = len(day)
    peak = 0.0
    age = 10 ** 9
    for i in range(n):
        if high[i] > peak:
            peak = high[i]
            age = 0
        else:
            age += 1
        if peak >= prev_close * (1 + min_surge / 100.0) and age >= confirm_n \
                and close[i] < peak * (1 - pull_pct / 100.0):
            return (ts[i], float(close[i]), (float(close[i]) / buy_px - 1) * 100)
    return None


def main():
    store = Store()
    symbols = "002176,002355,002606,003026,300057,600030,600352,600379,600584,603078,603316,603738".split(",")
    all_idx = [r[1] for r in store.get_index("sh000001")]
    dates = [r[1] for r in store.get_index("sh000001", start="2026-05-06", end="2026-08-24")]

    # 预载 1m 数据（内存缓存，避免重复读库）
    mdfs = {}
    for sym in symbols:
        mdf = pd.DataFrame(store.get_minute(sym, "1m"),
                           columns=["symbol", "freq", "ts", "open", "high", "low",
                                    "close", "volume", "amount"])
        if not mdf.empty:
            mdfs[sym] = mdf

    # 构造全部持仓交易日记录（与 surge 无关的部分）
    base_rows = []
    for sym, mdf in mdfs.items():
        for d in dates:
            pts = day_b_points(store, sym, d, freq="1m")
            if pts.empty:
                continue
            r0 = pts.iloc[0]
            buy_px = float(r0["price"])
            di = all_idx.index(d)
            for off in range(1, 4):
                if di + off >= len(all_idx):
                    break
                hd = all_idx[di + off]
                day = mdf[mdf["ts"].str[:10] == hd]
                if day.empty:
                    continue
                pc = prev_close_of(mdf, hd)
                if not pc:
                    continue
                close_px = float(day["close"].iloc[-1])
                nxt = None
                if di + off + 1 < len(all_idx):
                    nd = mdf[mdf["ts"].str[:10] == all_idx[di + off + 1]]
                    if not nd.empty:
                        nxt = float(nd["open"].iloc[0])
                base_rows.append({"sym": sym, "day": hd, "buy_px": buy_px, "day_df": day,
                                  "pc": pc,
                                  "ret_close": (sell_net(close_px) / buy_net(buy_px) - 1) * 100,
                                  "ret_next": (sell_net(nxt) / buy_net(buy_px) - 1) * 100 if nxt else None})

    L = ["# 次高点卖出规则独立验证（v54/v58/v62 口径，布防门槛扫描）", "",
         "> 规则：日内创出新高（相对昨收 ≥5%）后回落 ≥1%，且 confirm_n 根未再创新高 → 触发价卖出。",
         f"> 样本：{len(base_rows)} 个持仓交易日（450 笔 B 点 × +1~+3 日）。", "",
         "### 确认窗口 confirm_n 灵敏度（min_surge=5%）", "",
         "| confirm_n | 触发天数 | 触发卖出均值% | 持有收盘均值% | 差值pp | 卖出更优% | 持有次日均值% | 差值pp |",
         "|---|---|---|---|---|---|---|---|"]
    detail = None
    for confirm_n in (30, 60, 90):
        hits = []
        for b in base_rows:
            h = scan_second_high(b["day_df"], b["pc"], b["buy_px"], min_surge=5.0, confirm_n=confirm_n)
            if h is not None:
                rec = dict(b)
                rec.update({"trigger_ts": h[0], "trigger_px": h[1], "ret_trigger": h[2]})
                hits.append(rec)
        if not hits:
            L.append(f"| {confirm_n} 根 | 0 | - | - | - | - | - | - |")
            continue
        hd = pd.DataFrame(hits)
        d1 = (hd["ret_trigger"] - hd["ret_close"]).dropna()
        dn = (hd["ret_trigger"] - hd["ret_next"]).dropna()
        L.append(f"| {confirm_n} 根 | {len(hd)} | {hd['ret_trigger'].mean():+.2f} | "
                 f"{hd['ret_close'].mean():+.2f} | {d1.mean():+.2f} | {(d1>0).mean()*100:.0f} "
                 f"| {hd['ret_next'].dropna().mean():+.2f} | {dn.mean():+.2f} |")
        if confirm_n == 60:
            detail = hd
    L.append("")
    if detail is not None and len(detail):
        L.append("### 触发后 5/10 分钟走势（confirm_n=60 档）")
        L.append("")
        L.append("| 窗口 | 均值% | 胜率% |")
        L.append("|---|---|---|")
        for off, col in ((5, "r5"), (10, "r10")):
            vals = []
            for _, r in detail.iterrows():
                day = r["day_df"]
                pos = day.index[day["ts"] == r["trigger_ts"]]
                if pos.empty:
                    continue
                i = day.index.get_loc(pos[0])
                if i + off < len(day):
                    vals.append((float(day["close"].iloc[i + off]) / float(r["trigger_px"]) - 1) * 100)
            if vals:
                s = pd.Series(vals)
                L.append(f"| 触发后{off}分钟 | {s.mean():+.2f} | {(s>0).mean()*100:.0f} |")
    L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {OUT}")
    store.close()


if __name__ == "__main__":
    main()