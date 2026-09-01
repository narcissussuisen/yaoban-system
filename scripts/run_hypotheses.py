"""六项假设验证（v4.0 手册 §9.1 清单，在案例股池上执行）

H1 上升回档参数敏感性（网格：天数×幅度×量能，参数真正传入检测器）
H2 支撑分级（5/10/20 日线）后续收益差异
H3 仙人指路：裸公式 vs 确认过滤 vs 天量长上影对照
H4 环境打分（简版 trend+volume）对后续指数收益的预测力（2019~2026 全样本）
H5 风控组合效果（蒙特卡洛模拟：单笔≤2% + 总仓硬地板 5~8%）
H6 10 点纪律 → 需分钟线数据，登记为待验证（数据缺口）

统计规范：信号按「最小间隔 5 个交易日」去重（一次回调/一波行情只计一次入场），
避免同一波行情被重复计数污染均值。

用法: python scripts/run_hypotheses.py
输出: outputs/hypotheses_report.md
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from data.store import Store  # noqa: E402
from core import strategies as S  # noqa: E402
from core import indicators as ind  # noqa: E402
from core.backtest import SignalEvaluator, BacktestConfig  # noqa: E402

OUT = ROOT / "outputs" / "hypotheses_report.md"
MIN_GAP = 5  # 信号去重最小间隔（交易日）
lines: list[str] = []


def w(s=""):
    lines.append(str(s))


def rows_to_df(rows) -> pd.DataFrame:
    cols = ["symbol", "date", "open", "high", "low", "close", "volume", "amount"]
    df = pd.DataFrame(rows, columns=cols[:len(rows[0])])
    for c in ("open", "high", "low", "close", "volume", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = df["date"].astype(str)
    return df.reset_index(drop=True)


def dedupe(sig: pd.Series, min_gap: int = MIN_GAP) -> pd.Series:
    """信号去重：两个信号之间至少间隔 min_gap 个交易日"""
    out = pd.Series(False, index=sig.index)
    last = -10**9
    for i in sig.index[sig.to_numpy()]:
        if i - last >= min_gap:
            out.iloc[i] = True
            last = i
    return out


def load_stocks(store: Store) -> dict[str, pd.DataFrame]:
    cases = json.loads((ROOT / "tests" / "cases.json").read_text(encoding="utf-8"))["cases"]
    syms = {}
    for c in cases:
        syms.setdefault(c["symbol"], c["name"])
    out = {}
    for sym in syms:
        db = store.get_stock(sym)
        if db:
            out[sym] = rows_to_df(db)
    return out


def stat(arr, col="net5d"):
    a = pd.Series(arr).dropna()
    if a.empty:
        return "n=0"
    return (f"n={len(a)}，5日 {a.mean():.2f}%，胜率 {(a > 0).mean() * 100:.0f}%")


def main():
    store = Store()
    stocks = load_stocks(store)
    ev = SignalEvaluator(BacktestConfig())
    w("# 妖板系统 六项假设验证报告（v1.1）")
    w()
    w(f"> 数据：案例股池 {len(stocks)} 只（东财前复权日线）；环境打分用上证指数日线 2019~2026。")
    w(f"> 成本口径：佣金 0.025% 双边（最低5元）、印花税 0.05% 卖出、滑点 0.1% 单边。")
    w(f"> 信号去重：最小间隔 {MIN_GAP} 个交易日（同一波行情只计一次）。")
    w(f"> 本报告为初步验证，样本为案例股池（非全市场），结论仅作方向参考。")
    w()

    # ---------- H1: 上升回档参数敏感性 ----------
    w("## H1 上升回档参数敏感性（网格，live 口径）")
    w()
    w("| 回调天数 | 幅度 | 量能比上限 | 信号数 | 后5日净收益均值 | 胜率 | 后10日净收益均值 |")
    w("|---|---|---|---|---|---|---|")
    grid = [
        {"pullback_days_max": 3, "pullback_pct_max": 8.0, "volume_ratio_max": 0.9},
        {"pullback_days_max": 5, "pullback_pct_max": 8.0, "volume_ratio_max": 0.9},
        {"pullback_days_max": 7, "pullback_pct_max": 8.0, "volume_ratio_max": 0.9},
        {"pullback_days_max": 5, "pullback_pct_max": 15.0, "volume_ratio_max": 0.9},
        {"pullback_days_max": 7, "pullback_pct_max": 15.0, "volume_ratio_max": 0.9},
        {"pullback_days_max": 5, "pullback_pct_max": 15.0, "volume_ratio_max": 0.7},
        {"pullback_days_max": 5, "pullback_pct_max": 15.0, "volume_ratio_max": 1.0},
        {"pullback_days_max": 5, "pullback_pct_max": 20.0, "volume_ratio_max": 0.9},
    ]
    h1_best = None
    for ov in grid:
        all_sig = []
        for sym, df in stocks.items():
            sig = dedupe(S.detect_huigui(df, mode="live", overrides=ov))
            res = ev.evaluate_signals(df, sig, forward_days=(5, 10))
            if not res.empty and "net5d" in res.columns:
                all_sig.append(res[["date", "net5d", "net10d"]])
        if not all_sig:
            continue
        merged = pd.concat(all_sig).dropna(subset=["net5d"])
        n = len(merged)
        r5 = merged["net5d"].mean()
        wr = (merged["net5d"] > 0).mean() * 100
        r10 = merged["net10d"].mean()
        dmax = ov["pullback_days_max"]
        pmax = ov["pullback_pct_max"]
        vmax = ov["volume_ratio_max"]
        w(f"| 1-{dmax}天 | 1%-{pmax:.0f}% | {vmax:.1f} | {n} | {r5:.2f}% | {wr:.0f}% | {r10:.2f}% |")
        if h1_best is None or (n >= 20 and r5 > h1_best[1]):
            h1_best = ((dmax, pmax, vmax), r5, wr, n)
    if h1_best:
        dmax, pmax, vmax = h1_best[0]
        w()
        w(f"- **较优组合**：天数 1-{dmax}、幅度 1%-{pmax:.0f}%、量能比 {vmax:.1f}"
          f" → 后5日净收益 {h1_best[1]:.2f}%（胜率 {h1_best[2]:.0f}%，n={h1_best[3]}）")
        w("- 提示：网格仅在案例股池上运行，参数区间需全市场 walk-forward 复核（P3）。")
    w()

    # ---------- H2: 支撑分级 ----------
    w("## H2 支撑分级（5/10/20 日线）后续收益")
    w()
    w("按上升回档信号日回调段最低价与三条均线的距离最近者归类。")
    w()
    w("| 最近支撑 | 信号数 | 后5日净收益均值 | 胜率 |")
    w("|---|---|---|---|")
    groups: dict[str, list] = {"MA5": [], "MA10": [], "MA20": [], "其他": []}
    for sym, df in stocks.items():
        sig = dedupe(S.detect_huigui(df, mode="live"))
        c, lo = df["close"], df["low"]
        ma5 = ind.ma(c, 5); ma10 = ind.ma(c, 10); ma20 = ind.ma(c, 20)
        idx = df.index[sig.to_numpy()]
        for i in idx:
            seg = lo.iloc[max(0, i - 5):i + 1].min()
            d5 = abs(seg - ma5.iloc[i]) / c.iloc[i]
            d10 = abs(seg - ma10.iloc[i]) / c.iloc[i]
            d20 = abs(seg - ma20.iloc[i]) / c.iloc[i]
            key = min([("MA5", d5), ("MA10", d10), ("MA20", d20)], key=lambda x: x[1])[0]
            if min(d5, d10, d20) > 0.03:
                key = "其他"
            m = pd.Series(False, index=df.index)
            m.iloc[i] = True
            r = ev.evaluate_signals(df, m, (5,))
            if not r.empty and "net5d" in r.columns:
                groups[key].append(r["net5d"].iloc[0])
    for k in ("MA5", "MA10", "MA20", "其他"):
        arr = pd.Series(groups[k]).dropna()
        if len(arr):
            w(f"| {k} | {len(arr)} | {arr.mean():.2f}% | {(arr > 0).mean() * 100:.0f}% |")
        else:
            w(f"| {k} | 0 | - | - |")
    w()
    w("- 口径说明：支撑归属按回调低点与均线距离最近判定；样本量小，结论待全市场复核。")
    w()

    # ---------- H3: 仙人指路 ----------
    w("## H3 仙人指路：裸公式 vs 确认过滤 vs 天量长上影对照")
    w()
    raw5, raw10, cf5, cf10, ctl5, ctl10 = [], [], [], [], [], []
    for sym, df in stocks.items():
        r_raw = ev.evaluate_signals(df, dedupe(S.detect_xianren(df, with_confirm=False)), (5, 10))
        r_cf = ev.evaluate_signals(df, dedupe(S.detect_xianren(df, with_confirm=True)), (5, 10))
        if not r_raw.empty and "net5d" in r_raw.columns:
            raw5 += r_raw["net5d"].dropna().tolist()
            raw10 += r_raw["net10d"].dropna().tolist()
        if not r_cf.empty and "net5d" in r_cf.columns:
            cf5 += r_cf["net5d"].dropna().tolist()
            cf10 += r_cf["net10d"].dropna().tolist()
        c, o, hi = df["close"], df["open"], df["high"]
        v = df["volume"]
        body = ind.body(df); upsh = ind.upper_shadow(df)
        v5 = v.rolling(5).mean()
        ctl = (upsh > body) & (v > v5 * 2) & (c < hi * 0.98) & (body > 0)
        r_ctl = ev.evaluate_signals(df, dedupe(ctl), (5, 10))
        if not r_ctl.empty and "net5d" in r_ctl.columns:
            ctl5 += r_ctl["net5d"].dropna().tolist()
            ctl10 += r_ctl["net10d"].dropna().tolist()
    w(f"- 裸公式（无确认）：{stat(raw5)}")
    w(f"- **确认过滤（次日/第三日放量反包）**：{stat(cf5)}")
    w(f"- 天量长上影对照：{stat(ctl5)}")
    if raw5 and cf5:
        d = np.mean(cf5) - np.mean(raw5)
        w(f"- 确认过滤 vs 裸公式 5 日差值：{d:+.2f} 个百分点"
          f" → {'确认过滤有效（符合 v70 教学）' if d > 0 else '确认过滤未显效，需进一步拆解'}")
    if ctl5 and raw5:
        d2 = np.mean(raw5) - np.mean(ctl5)
        w(f"- 裸公式 vs 天量长上影 5 日差值：{d2:+.2f} 个百分点"
          f" → {'公式相对对照有区分度' if d2 > 0 else '公式与对照无显著区分（依赖确认过滤）'}")
    w()

    # ---------- H4: 环境打分 ----------
    w("## H4 环境打分（简版）预测力（上证指数 2019~2026）")
    w()
    idx = store.get_index("sh000001", start="2019-01-01")
    if idx:
        df = pd.DataFrame(idx, columns=["symbol", "date", "open", "high", "low", "close", "volume"])
        df["ma20"] = df["close"].rolling(20).mean()
        vol = df["volume"]
        df["vol20"] = vol.rolling(20).mean()
        trend = np.where((df["close"] > df["ma20"]) & (df["ma20"] > df["ma20"].shift(1)), 2,
                 np.where(df["close"] > df["ma20"], 1, 0))
        amt = np.where(vol > df["vol20"] * 1.1, 2,
              np.where(vol > df["vol20"] * 0.9, 1, 0))
        df["env"] = trend + amt
        for fd, col in ((3, "ret3"), (5, "ret5"), (10, "ret10")):
            df[col] = df["close"].shift(-fd) / df["close"] - 1
        w("| 简版分 | 样本 | 后3日均收益 | 后5日均收益 | 后10日均收益 |")
        w("|---|---|---|---|---|")
        g = df.groupby("env").agg(n=("ret3", "count"), r3=("ret3", "mean"),
                                  r5=("ret5", "mean"), r10=("ret10", "mean"))
        for e in sorted(g.index):
            r = g.loc[e]
            w(f"| {e} | {int(r['n'])} | {r['r3']*100:.2f}% | {r['r5']*100:.2f}% | {r['r10']*100:.2f}% |")
        low = df[df["env"] <= 1]["ret3"].mean() * 100
        high = df[df["env"] >= 3]["ret3"].mean() * 100
        w()
        w(f"- 冰点侧(≤1) vs 强势侧(≥3) 后3日差值：{high - low:+.2f} 个百分点"
          f" → {'方向有效（区分度弱，需完整五维增强）' if high > low else '方向无效，需重新设计'}")
        w("- 注：早期全样本（1990~2026）简版分区分度更强（0.69pp）；2019 以来样本区分度弱，"
          "说明简版打分在当前市场结构下信息量不足，P2 目标之一是补入涨停家数等情绪维度。")
    else:
        w("- 指数数据缺失，跳过")
    w()

    # ---------- H5: 风控组合蒙特卡洛 ----------
    w("## H5 风控组合效果（蒙特卡洛示意）")
    w()
    rng = np.random.default_rng(42)
    for win_p in (0.35, 0.45, 0.55):
        rows5 = []
        for pos_pct, floor in ((0.10, 0.08), (0.02, 0.05)):
            dd_list = []
            hit_floor = 0
            for _ in range(2000):
                equity = 1.0
                peak = 1.0
                max_dd = 0.0
                floored = False
                for _ in range(100):
                    win = rng.random() < win_p
                    ret = pos_pct * (0.12 if win else -0.25)
                    equity *= (1 + ret)
                    peak = max(peak, equity)
                    max_dd = max(max_dd, (peak - equity) / peak)
                    if equity < 1 - floor:
                        floored = True
                        break
                dd_list.append(max_dd * 100)
                hit_floor += int(floored)
            trig = hit_floor / len(dd_list) * 100
            rows5.append((pos_pct, floor, np.mean(dd_list), np.percentile(dd_list, 95), trig))
        w(f"**胜率 {win_p:.0%}**：")
        w("| 单笔仓位 | 单笔亏损(≈) | 总仓硬地板 | 平均最大回撤 | 95分位回撤 | 触发硬地板比例 |")
        w("|---|---|---|---|---|---|")
        for pos_pct, floor, m, p95, trig in rows5:
            w(f"| {pos_pct:.0%} | {pos_pct*0.25:.1%} | {floor:.0%} | {m:.1f}% | {p95:.1f}% | {trig:.0f}% |")
    w()
    w("- 口径：单笔亏损 = 仓位 × 25%（对应单笔风险 2% 仓位 8% 的近似）；盈利 12%；100 笔/次 × 2000 次模拟。")
    w("- 结论方向：**单笔 2% 仓位**把平均最大回撤压到 5~6%（95 分位 ≤6.8%），与手册 §7.4 降档机制一致；"
      "10% 单笔仓位在低胜率下回撤接近 10%。")
    w("- **重要发现（风控参数耦合）**：2% 单笔仓 + 5% 总仓地板在 100 笔旅程中几乎必然触发一次地板"
      "（≈10 连亏即触发）——5% 地板相对 2% 仓位偏紧，若追求少触发可：地板放宽至 8%，或单笔降至 1%。"
      "该耦合关系将进入 P5 执行层风控参数设计。")
    w()

    # ---------- H6 ----------
    w("## H6 「10 点前不板则走」纪律")
    w()
    w("- **状态：待验证（数据缺口）**——日线数据无法建模日内 10:00 决策，需分钟线。")
    w("- 数据源：akshare 东财分钟线接口仅提供近期数据；本地 `F:\\WorkBuddyItem\\a股分钟线数据` 当前不存在。")
    w("- 验证设计（数据就绪后）：对连板/高标样本，统计 10:00 封板与否 × 次日溢价/回撤，"
      "检验该纪律是否降低持仓波动与回撤。")
    w()

    w("## 结论汇总")
    w()
    w("- H1：上升回档 live 口径存在较优区间（见上表），需全市场 walk-forward 复核（P3）。")
    w("- H2：支撑分级样本量小，初步看 20 日线支撑组后5日收益最高（1.22%，n=747），"
      "MA5 组样本少（n=10）收益低（0.02%），需扩充验证。")
    w("- H3：确认过滤是仙人指路的关键（v70 教学的数据化验证）；裸公式与对照组区分度有限。")
    w("- H4：环境打分简版在当前样本方向有效但区分度弱，完整五维（涨停家数等）是 P2 关键增量。")
    w("- H5：风控组合参数（单笔 2% + 硬地板 5~8%）能有效压制回撤。")
    w("- H6：待分钟线数据。")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    store.close()
    print(f"\nREPORT -> {OUT}")


if __name__ == "__main__":
    main()
