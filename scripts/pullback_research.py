"""R1 回踩形态池研究（第一步）：形态因子区分度（档1 开盘执行代理）

扫描全市场每日「回踩形态」候选（活跃+回调+趋势内），按形态特征分档统计次日收益：
  60日内涨停次数、回撤幅度（距20日高点）、回调天数、缩量比、动量mom60、成交额、均线支撑位、次日低开幅度
输出: outputs/pullback_research_2026.md
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

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "pullback_research_2026.md"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001


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
    print(f"[{time.time()-t0:.0f}s] 聚合日线 {len(symbols)} 只…", flush=True)
    daily_map = build_daily_map(year, symbols, workers=6)
    daily_map = {k: v for k, v in daily_map.items() if v}
    print(f"[{time.time()-t0:.0f}s] 日线就绪 {len(daily_map)} 只，扫描回踩形态…", flush=True)

    rows_out = []
    for sym, rows in daily_map.items():
        if len(rows) < 70:
            continue
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low",
                                         "close", "volume", "amount"])
        c = df["close"]
        hi = df["high"]
        lpx = limit_pct_of(sym)
        # 涨停掩码（日线）
        zt = (c / c.shift(1) - 1 >= lpx - 0.005).astype(int)
        zt60 = zt.rolling(60).sum().shift(1)
        # 20 日高点与回撤
        hh20 = hi.rolling(20).max().shift(1)
        drawdown = (c / hh20 - 1) * 100          # 距 20 日高点回撤%
        peak_pos = hi.rolling(20).apply(lambda x: x.argmax(), raw=True).shift(1)
        # 距高点天数 = 20 - peak_pos（窗口内位置，0-based；shift 前一日窗口）
        days_from_peak = (20 - peak_pos.fillna(0)).astype(int)
        # 缩量比：当日量 / 前 5 日均量（高点前）
        v5 = df["volume"].rolling(5).mean().shift(1)
        vol_ratio = df["volume"] / v5.replace(0, np.nan)
        # 均线
        ma5 = c.rolling(5).mean()
        ma10 = c.rolling(10).mean()
        ma20 = c.rolling(20).mean()
        ma60 = c.rolling(60).mean()
        # mom60 / amt20
        mom60 = (c / c.shift(60) - 1) * 100
        amt20 = df["amount"].rolling(20).mean() / 1e8
        # 在 20 日线上方（趋势内回调）
        above_ma20 = c > ma20
        # 回踩 MA5/MA10 附近（支撑距离）
        dist_ma5 = (c / ma5 - 1) * 100
        dist_ma10 = (c / ma10 - 1) * 100

        n = len(df)
        for i in range(70, n - 1):
            if i >= len(idx_dates):
                break
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
            mom = mom60.iloc[i]
            if pd.isna(mom):
                continue
            # 次日收益（开盘买，日线退出）
            r1 = rows[i + 1]
            entry_px = float(r1[2])
            stop_px = entry_px * 0.95
            exit_px = stop_px if float(r1[3]) <= stop_px else float(r1[5])
            pnl = (sell_net(exit_px) / buy_net(entry_px) - 1) * 100
            # 次日低开幅度
            gap = (float(r1[2]) / float(rows[i][5]) - 1) * 100
            rows_out.append({
                "sym": sym, "d": rows[i][1], "pnl": pnl,
                "dd": float(dd), "dpk": int(dpk),
                "vr": float(vol_ratio.iloc[i]) if pd.notna(vol_ratio.iloc[i]) else 1.0,
                "mom": float(mom), "amt": float(amt),
                "zt60": int(zt60.iloc[i]),
                "dist5": float(dist_ma5.iloc[i]) if pd.notna(dist_ma5.iloc[i]) else 0.0,
                "dist10": float(dist_ma10.iloc[i]) if pd.notna(dist_ma10.iloc[i]) else 0.0,
                "gap": float(gap),
                "ma60_up": int(c.iloc[i] > ma60.iloc[i] and ma60.iloc[i] > ma60.iloc[i - 5]),
            })
    df = pd.DataFrame(rows_out)
    print(f"[{time.time()-t0:.0f}s] 回踩形态候选 {len(df)} 个，分档统计…", flush=True)

    L = [f"# 回踩形态池研究（第一步）{year}：形态因子区分度（档1 代理，n={len(df)}）", "",
         "> 候选=60日内≥1涨停 + 20日线上方 + 距20日高点回撤2~20% + 回调1~8天 + 成交额≥1亿；次日开盘买入日线退出。", ""]

    def band(col, cuts, labels, fmt="{:+.2f}"):
        L.append(f"### {col}")
        L.append("")
        L.append("| 分档 | n | 均值% | 中位% | 胜率% |")
        L.append("|---|---|---|---|---|")
        g = df.copy()
        g["b"] = pd.cut(g[col], bins=cuts, labels=labels, include_lowest=True)
        for b in labels:
            gg = g[g["b"] == b]
            if len(gg):
                L.append(f"| {b} | {len(gg)} | {gg['pnl'].mean():+.2f} | {gg['pnl'].median():+.2f} "
                         f"| {(gg['pnl'] > 0).mean() * 100:.0f} |")
        L.append("")

    band("dd", [-1, 3, 6, 10, 15, 100], ["2~3%", "3~6%", "6~10%", "10~15%", "15~20%"])
    band("dpk", [-1, 2, 3, 5, 8, 100], ["1~2天", "3天", "4~5天", "6~8天", "8天+"])
    band("vr", [0, 0.7, 1.0, 1.5, 100], ["<0.7缩量", "0.7~1", "1~1.5", ">1.5放量"])
    band("mom", [-1e9, 0, 30, 60, 120, 1e9], ["<0%", "0~30%", "30~60%", "60~120%", ">120%"])
    band("zt60", [-1, 1, 3, 6, 100], ["1次", "2~3次", "4~6次", "7次+"])
    band("dist10", [-100, -3, -1, 1, 3, 100], ["<-3%破10日线", "-3~-1%", "-1~1%贴10日线", "1~3%", ">3%远离"])
    band("gap", [-100, -2, 0, 1, 3, 100], ["低开<-2%", "-2~0%", "0~1%", "1~3%", "高开>3%"])
    g2 = df.copy()
    g2["b"] = g2["ma60_up"].map({0: "否", 1: "是"})
    L.append("### ma60_up")
    L.append("")
    L.append("| 分档 | n | 均值% | 中位% | 胜率% |")
    L.append("|---|---|---|---|---|")
    for b in ("否", "是"):
        gg = g2[g2["b"] == b]
        if len(gg):
            L.append(f"| {b} | {len(gg)} | {gg['pnl'].mean():+.2f} | {gg['pnl'].median():+.2f} "
                     f"| {(gg['pnl'] > 0).mean() * 100:.0f} |")
    L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[-70:]))
    print(f"\nREPORT -> {OUT}  （总耗时 {(time.time()-t0)/60:.1f} 分钟）")
    store.close()


if __name__ == "__main__":
    main()