"""选股层研究：全市场信号的选股因子区分度（档1 开盘执行为代理，无需分钟数据）

对 2026 全市场每个候选（信号日次日开盘买入、日线退出）计算选股因子并分档统计：
  trend    趋势质量：close>MA20 且 MA20 向上（0/1/2）
  ma_bull  多头排列：MA5>MA10>MA20（0/1）
  ma60_up  close>MA60 且 MA60 向上（0/1）
  amt_pct  20 日均成交额全市场分位（0~1）
  zt_hist  近 60 日涨停次数
  mom60    60 日涨幅%
  amt20    20 日均成交额（亿元）
输出: outputs/pool_research_2026.md
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
from core import strategies as S  # noqa: E402
from core.env_score import env_score  # noqa: E402
from core.sell import limit_pct_of  # noqa: E402

OUT = pathlib.Path(__file__).resolve().parent.parent / "outputs" / "pool_research_2026.md"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001
MIN_INTERVAL = 5


def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)


def is_stock(sym: str) -> bool:
    return not sym.startswith(("399", "5", "15", "16"))


def main():
    t0 = time.time()
    year = sys.argv[1] if len(sys.argv) > 1 else "2026"
    store = Store()
    qfq = QFQStore(year)
    idx_df = pd.DataFrame(store.get_index("sh000001"),
                          columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    idx_dates = [r[1] for r in store.get_index("sh000001")]
    # 情绪过滤（与 backtest_market 一致）
    sent = {}
    sp = pathlib.Path(__file__).resolve().parent.parent / "outputs" / f"sentiment_daily_{year}.csv"
    if sp.exists():
        sent = {r["date"]: r for _, r in pd.read_csv(sp).iterrows()}

    def env_simple_asof(d: str) -> int:
        sub = idx_df[idx_df["date"] <= d]
        if len(sub) < 25:
            return 0
        r = env_score(sub)
        return int(r["dims"]["trend"] + r["dims"]["volume"])

    symbols = [s for s in qfq.symbols() if is_stock(s)]
    print(f"[{time.time()-t0:.0f}s] 聚合日线 {len(symbols)} 只…", flush=True)
    daily_map = build_daily_map(year, symbols, workers=6)
    daily_map = {k: v for k, v in daily_map.items() if v}
    print(f"[{time.time()-t0:.0f}s] 日线就绪 {len(daily_map)} 只，检测信号+计算因子…", flush=True)

    # 每只股票的因子序列（向量化）
    factor_cache: dict[str, pd.DataFrame] = {}
    for sym, rows in daily_map.items():
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low",
                                         "close", "volume", "amount"])
        c = df["close"]
        ma5 = c.rolling(5).mean()
        ma10 = c.rolling(10).mean()
        ma20 = c.rolling(20).mean()
        ma60 = c.rolling(60).mean()
        f = pd.DataFrame(index=df.index)
        f["trend"] = ((c > ma20) & (ma20 > ma20.shift(3))).astype(int) + (c > ma20).astype(int)
        f["ma_bull"] = ((ma5 > ma10) & (ma10 > ma20)).astype(int)
        f["ma60_up"] = ((c > ma60) & (ma60 > ma60.shift(5))).astype(int)
        f["mom60"] = (c / c.shift(60) - 1) * 100
        f["amt20"] = df["amount"].rolling(20).mean() / 1e8
        # 近 60 日涨停次数
        lpx = limit_pct_of(sym)
        zt = (c / c.shift(1) - 1 >= lpx - 0.005).astype(int)
        f["zt_hist"] = zt.rolling(60).sum().shift(1).fillna(0)
        f["date"] = df["date"].values
        factor_cache[sym] = f

    # 候选：信号日次日（与 backtest_market 同过滤）
    cands = []
    for sym, rows in daily_map.items():
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low",
                                         "close", "volume", "amount"])
        masks = {"huigui": S.detect_huigui(df, mode="live"),
                 "zt_huicai": S.detect_zt_huicai(df),
                 "fanbao": S.detect_fanbao(df),
                 "xianren": S.detect_xianren(df, with_confirm=True)}
        union = masks["huigui"] | masks["zt_huicai"] | masks["fanbao"] | masks["xianren"]
        days = df.loc[union, "date"].tolist()
        kept, last = [], None
        for d in days:
            if last is None or idx_dates.index(d) - idx_dates.index(last) >= MIN_INTERVAL:
                kept.append(d)
                last = d
        for d in kept:
            if env_simple_asof(d) < 2:
                continue
            sr = sent.get(d)
            if sr is not None and (int(sr["zt"]) < 71 or float(sr["zhaban_rate"]) > 35):
                continue
            di = idx_dates.index(d)
            if di + 1 >= len(idx_dates):
                continue
            cands.append((sym, d, idx_dates[di + 1]))
    print(f"[{time.time()-t0:.0f}s] 候选 {len(cands)} 个，计算收益+分档…", flush=True)

    # 档1 pnl + 因子
    rows_out = []
    for sym, d, ed in cands:
        rows = daily_map[sym]
        if len(rows) < 65:
            continue
        dates = [r[1] for r in rows]
        if ed not in dates:
            continue
        i = dates.index(ed)
        if i + 1 >= len(rows):
            continue
        entry_px = float(rows[i][2])
        r1 = rows[i + 1]
        stop_px = entry_px * 0.95
        exit_px = stop_px if float(r1[3]) <= stop_px else float(r1[5])
        pnl = (sell_net(exit_px) / buy_net(entry_px) - 1) * 100
        f = factor_cache[sym]
        fr = f[f["date"] == d]
        if fr.empty:
            continue
        fr = fr.iloc[0]
        rows_out.append({"sym": sym, "d": d, "pnl": pnl,
                         "trend": int(fr["trend"]), "ma_bull": int(fr["ma_bull"]),
                         "ma60_up": int(fr["ma60_up"]), "mom60": float(fr["mom60"]) if pd.notna(fr["mom60"]) else np.nan,
                         "amt20": float(fr["amt20"]) if pd.notna(fr["amt20"]) else 0.0,
                         "zt_hist": int(fr["zt_hist"])})
    df = pd.DataFrame(rows_out).dropna(subset=["mom60"])
    print(f"[{time.time()-t0:.0f}s] 有效样本 {len(df)}，汇总…", flush=True)

    # 全市场当日成交额分位
    amt_med = df.groupby("d")["amt20"].transform("median")
    df["amt_pct"] = (df["amt20"] > amt_med).astype(int)

    L = [f"# 选股因子区分度研究 {year}（档1 开盘执行代理，n={len(df)}）", "",
         "> 候选=四战法信号日次日开盘买入、日线退出（含成本）；因子=信号日截面值。", ""]

    def band(col, cuts, labels):
        L.append(f"### {col}")
        L.append("")
        L.append("| 分档 | n | 均值% | 中位% | 胜率% |")
        L.append("|---|---|---|---|---|")
        g = df.copy()
        if set(cuts) <= {0, 1, 2, 3, 4} and len(cuts) == len(labels):
            # 整数分档：直接映射（cuts 即取值列表）
            g["b"] = g[col].map({v: labels[i] for i, v in enumerate(cuts)})
        elif max(cuts) == 1 and len(labels) == 2:
            g["b"] = g[col].map({0: labels[0], 1: labels[1]})
        else:
            g["b"] = pd.cut(g[col], bins=cuts, labels=labels, include_lowest=True)
        for b in labels:
            gg = g[g["b"] == b]
            if len(gg):
                L.append(f"| {b} | {len(gg)} | {gg['pnl'].mean():+.2f} | {gg['pnl'].median():+.2f} "
                         f"| {(gg['pnl'] > 0).mean() * 100:.0f} |")
        L.append("")

    band("trend", [0, 1, 2], ["0(破20日线)", "1(站上)", "2(站上+20日线向上)"])
    band("ma_bull", [0, 1], ["否", "是"])
    band("ma60_up", [0, 1], ["否", "是"])
    band("mom60", [-1e9, 0, 30, 60, 120, 1e9], ["<0%", "0~30%", "30~60%", "60~120%", ">120%"])
    band("zt_hist", [-1, 0, 1, 3, 6, 1e9], ["0次", "1次", "2~3次", "4~6次", "7次+"])
    band("amt_pct", [0, 1, 2], ["低（低于当日中位）", "高"])
    band("amt20", [0, 1, 2, 5, 10, 1e9], ["<1亿", "1~2亿", "2~5亿", "5~10亿", ">10亿"])
    # 组合：趋势+多头+量
    df["quality"] = (df["trend"] == 2).astype(int) + df["ma_bull"] + df["ma60_up"]
    band("quality", [0, 1, 2, 3, 4], ["0", "1", "2", "3(全满足)"])
    # 组合规则验证
    L.append("## 组合规则（动量 × 成交额 × 涨停历史）")
    L.append("")
    L.append("| 规则 | n | 均值% | 中位% | 胜率% |")
    L.append("|---|---|---|---|---|")
    rules = [
        ("基线（全部候选）", df),
        ("mom60 30~120%", df[(df["mom60"] >= 30) & (df["mom60"] <= 120)]),
        ("+ amt20 ≥5亿", df[(df["mom60"] >= 30) & (df["mom60"] <= 120) & (df["amt20"] >= 5)]),
        ("+ zt_hist ≥1", df[(df["mom60"] >= 30) & (df["mom60"] <= 120) & (df["amt20"] >= 5) & (df["zt_hist"] >= 1)]),
        ("+ zt_hist ≥3", df[(df["mom60"] >= 30) & (df["mom60"] <= 120) & (df["amt20"] >= 5) & (df["zt_hist"] >= 3)]),
        ("mom60 60~120%", df[(df["mom60"] >= 60) & (df["mom60"] <= 120)]),
        ("mom60 60~120% + amt≥5亿", df[(df["mom60"] >= 60) & (df["mom60"] <= 120) & (df["amt20"] >= 5)]),
        ("mom60 60~120% + amt≥5亿 + zt≥1", df[(df["mom60"] >= 60) & (df["mom60"] <= 120) & (df["amt20"] >= 5) & (df["zt_hist"] >= 1)]),
        ("mom60 ≥30% + amt≥10亿", df[(df["mom60"] >= 30) & (df["amt20"] >= 10)]),
    ]
    for name, gg in rules:
        if len(gg):
            L.append(f"| {name} | {len(gg)} | {gg['pnl'].mean():+.2f} | {gg['pnl'].median():+.2f} "
                     f"| {(gg['pnl'] > 0).mean() * 100:.0f} |")
    L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[-60:]))
    print(f"\nREPORT -> {OUT}  （总耗时 {(time.time()-t0)/60:.1f} 分钟）")
    store.close()


if __name__ == "__main__":
    main()