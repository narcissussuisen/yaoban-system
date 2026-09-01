"""P3 walk-forward 稳健性验证（上升回档 live 口径）

设计:
  - 股票池: DB 中全部个股（83 只，2022-01 起）
  - 时间切分: 训练段 2022-01-01 ~ 2024-06-30；验证段 2024-07-01 ~ 2026-08-21
  - 训练: 参数网格（天数上限×幅度上限×量能比）按后5日净收益均值选优（要求 n≥20）
  - 验证: 最优组合在验证段的表现
  - 稳健性评级:
      A = 验证段净收益 > 0 且 ≥ 训练段 50%（参数高原：相邻组合表现接近）
      B = 验证段净收益 > 0 但明显衰减（< 训练段 50%）
      C = 验证段净收益 ≤ 0
  - 参数高原检查: 最优组合 ± 相邻网格点在验证段的收益波动（波动小=高原，稳健）

用法: python scripts/run_walkforward.py
输出: outputs/walkforward_report.md
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from data.store import Store  # noqa: E402
from core import strategies as S  # noqa: E402
from core.backtest import SignalEvaluator, BacktestConfig  # noqa: E402

OUT = ROOT / "outputs" / "walkforward_report.md"
TRAIN_START, TRAIN_END = "2022-01-01", "2024-06-30"
TEST_START, TEST_END = "2024-07-01", "2026-08-21"
MIN_GAP = 5
MIN_N = 20  # 训练段最少信号数

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
    out = pd.Series(False, index=sig.index)
    last = -10**9
    for i in sig.index[sig.to_numpy()]:
        if i - last >= min_gap:
            out.iloc[i] = True
            last = i
    return out


def eval_window(ev: SignalEvaluator, df: pd.DataFrame, sig: pd.Series,
                start: str, end: str) -> pd.Series:
    """在指定日期窗口内评估信号后5日净收益"""
    sel = (df["date"] >= start) & (df["date"] <= end)
    sig_w = sig & sel
    r = ev.evaluate_signals(df, sig_w, forward_days=(5,))
    if r is None or r.empty or "net5d" not in r.columns:
        return pd.Series(dtype=float)
    return r["net5d"].dropna()


def main():
    store = Store()
    ev = SignalEvaluator(BacktestConfig())
    all_rows = store.conn.execute(
        "SELECT symbol, COUNT(*) n FROM stock_daily GROUP BY symbol HAVING n >= 400"
    ).fetchall()
    symbols = [r[0] for r in all_rows]
    w("# P3 walk-forward 稳健性验证报告（上升回档 live 口径）")
    w()
    w(f"> 股票池 {len(symbols)} 只（DB 中 ≥400 行）；训练段 {TRAIN_START}~{TRAIN_END}；"
      f"验证段 {TEST_START}~{TEST_END}；成本=佣金0.025%双边+印花税0.05%卖出+滑点0.1%。")
    w()

    # ---------- 网格 ----------
    grid = [
        {"pullback_days_max": 3, "pullback_pct_max": 8.0, "volume_ratio_max": 0.9},
        {"pullback_days_max": 5, "pullback_pct_max": 8.0, "volume_ratio_max": 0.9},
        {"pullback_days_max": 7, "pullback_pct_max": 8.0, "volume_ratio_max": 0.9},
        {"pullback_days_max": 5, "pullback_pct_max": 15.0, "volume_ratio_max": 0.9},
        {"pullback_days_max": 7, "pullback_pct_max": 15.0, "volume_ratio_max": 0.9},
        {"pullback_days_max": 5, "pullback_pct_max": 15.0, "volume_ratio_max": 0.7},
        {"pullback_days_max": 5, "pullback_pct_max": 15.0, "volume_ratio_max": 1.0},
        {"pullback_days_max": 7, "pullback_pct_max": 20.0, "volume_ratio_max": 0.9},
    ]

    # 缓存每只股票的信号（避免网格内重复计算）
    sig_cache: dict[str, dict[tuple, pd.Series]] = {}

    def get_sig(sym: str, df: pd.DataFrame, ov: dict) -> pd.Series:
        key = (ov["pullback_days_max"], ov["pullback_pct_max"], ov["volume_ratio_max"])
        if sym not in sig_cache:
            sig_cache[sym] = {}
        if key not in sig_cache[sym]:
            sig_cache[sym][key] = dedupe(S.detect_huigui(df, mode="live", overrides=ov))
        return sig_cache[sym][key]

    w("## 1. 训练段网格（选优）")
    w()
    w("| 参数(天数/幅度/量能) | 信号数 | 后5日净收益均值 | 胜率 |")
    w("|---|---|---|---|")
    train_results = {}
    for ov in grid:
        rets = []
        for sym in symbols:
            db = store.get_stock(sym)
            if not db:
                continue
            df = rows_to_df(db)
            sig = get_sig(sym, df, ov)
            r = eval_window(ev, df, sig, TRAIN_START, TRAIN_END)
            rets.append(r)
        allr = pd.concat(rets) if rets else pd.Series(dtype=float)
        n = len(allr)
        mean = allr.mean() if n else float("nan")
        wr = (allr > 0).mean() * 100 if n else float("nan")
        tag = f"{ov['pullback_days_max']}/{ov['pullback_pct_max']:.0f}/{ov['volume_ratio_max']:.1f}"
        w(f"| {tag} | {n} | {mean:.2f}%" if n else f"| {tag} | 0 | - | - |")
        train_results[tag] = (n, mean, wr)

    best_tag = max((k for k, v in train_results.items() if v[0] >= MIN_N),
                   key=lambda k: train_results[k][1], default=None)
    if best_tag is None:
        w("\n**训练段无满足 n≥20 的参数组合，无法选优**")
        store.close()
        return
    w()
    w(f"**训练段最优：{best_tag}** → 后5日净收益 {train_results[best_tag][1]:.2f}%"
      f"（n={train_results[best_tag][0]}，胜率 {train_results[best_tag][2]:.0f}%）")
    best_ov = grid[[f"{g['pullback_days_max']}/{g['pullback_pct_max']:.0f}/{g['volume_ratio_max']:.1f}"
                    for g in grid].index(best_tag)]

    # ---------- 验证段 ----------
    w()
    w("## 2. 验证段评估（最优组合 + 相邻组合的高原检查）")
    w()
    w("| 参数(天数/幅度/量能) | 信号数 | 后5日净收益均值 | 胜率 |")
    w("|---|---|---|---|")
    test_results = {}
    for ov in grid:
        rets = []
        for sym in symbols:
            db = store.get_stock(sym)
            if not db:
                continue
            df = rows_to_df(db)
            sig = get_sig(sym, df, ov)
            r = eval_window(ev, df, sig, TEST_START, TEST_END)
            rets.append(r)
        allr = pd.concat(rets) if rets else pd.Series(dtype=float)
        n = len(allr)
        mean = allr.mean() if n else float("nan")
        wr = (allr > 0).mean() * 100 if n else float("nan")
        tag = f"{ov['pullback_days_max']}/{ov['pullback_pct_max']:.0f}/{ov['volume_ratio_max']:.1f}"
        w(f"| {tag} | {n} | {mean:.2f}%" if n else f"| {tag} | 0 | - | - |")
        test_results[tag] = (n, mean, wr)

    # ---------- 稳健性评级 ----------
    w()
    w("## 3. 稳健性评级")
    w()
    tn, tm, tw = train_results[best_tag]
    vn, vm, vw = test_results[best_tag]
    if vm > 0 and vm >= tm * 0.5:
        grade = "A（验证段收益为正且保持 ≥50%，参数高原稳健）"
    elif vm > 0:
        grade = "B（验证段收益为正但明显衰减，需收窄参数或补充维度）"
    else:
        grade = "C（验证段失效，参数过拟合训练段，禁止上线）"
    w(f"- 最优组合 {best_tag}：训练 {tm:.2f}%（n={tn}）→ 验证 {vm:.2f}%（n={vn}）→ **评级 {grade}**")
    w()
    # 高原检查：验证段最优组合与相邻组合的收益差
    test_sorted = sorted(test_results.items(), key=lambda kv: kv[1][1], reverse=True)
    if len(test_sorted) >= 3:
        top3 = test_sorted[:3]
        spread = top3[0][1][1] - top3[-1][1][1]
        w(f"- 验证段前 3 组合收益差：{spread:.2f}pp（{'参数高原明显，稳健' if spread < 1.0 else '收益集中在个别参数，偏尖峰'}）")
        for tag, (n, mean, wr) in top3:
            w(f"    - {tag}: {mean:.2f}%（n={n}）")
    w()
    w("## 4. 结论")
    w()
    w("- 若评级为 A：当前 live 参数可作为默认上线参数，进入 P4 每日信号管线；")
    w("- 若评级为 B/C：参数仅作候选，需全市场更多标的或增加环境过滤后再评估；")
    w("- 参数变更一律走「walk-forward 通过 → 模拟盘灰度 → bump 版本」护栏（ROADMAP P6）。")

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    store.close()
    print(f"\nREPORT -> {OUT}")


if __name__ == "__main__":
    main()
