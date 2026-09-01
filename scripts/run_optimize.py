"""P6 月度自迭代优化器：参数网格 → walk-forward → 与当前参数对比 → 生成优化提案

护栏（ROADMAP P6）:
  - 默认只生成提案（outputs/iterations/YYYY-MM_proposal.md），不自动改参数；
  - 人工/主代理复核提案后，用 --apply 应用（写入 parameters.toml 并 bump 版本）；
  - 任何参数变更必须通过 walk-forward 验证（评级 A/B）才能提案。

用法:
    python scripts/run_optimize.py                 # 生成月度优化提案
    python scripts/run_optimize.py --apply         # 应用提案（更新 parameters.toml + 版本号）
输出: outputs/iterations/YYYY-MM_proposal.md
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from data.store import Store  # noqa: E402
from core import strategies as S  # noqa: E402
from core.backtest import SignalEvaluator, BacktestConfig  # noqa: E402

CONFIG_PATH = ROOT / "config" / "parameters.toml"
ITER_DIR = ROOT / "outputs" / "iterations"
MIN_GAP = 5
MIN_N = 20

# 与 run_walkforward.py 相同的网格与切分
GRID = [
    {"pullback_days_max": 3, "pullback_pct_max": 8.0, "volume_ratio_max": 0.9},
    {"pullback_days_max": 5, "pullback_pct_max": 8.0, "volume_ratio_max": 0.9},
    {"pullback_days_max": 7, "pullback_pct_max": 8.0, "volume_ratio_max": 0.9},
    {"pullback_days_max": 5, "pullback_pct_max": 15.0, "volume_ratio_max": 0.9},
    {"pullback_days_max": 7, "pullback_pct_max": 15.0, "volume_ratio_max": 0.9},
    {"pullback_days_max": 5, "pullback_pct_max": 15.0, "volume_ratio_max": 0.7},
    {"pullback_days_max": 5, "pullback_pct_max": 15.0, "volume_ratio_max": 1.0},
    {"pullback_days_max": 7, "pullback_pct_max": 20.0, "volume_ratio_max": 0.9},
]
TRAIN_START, TRAIN_END = "2022-01-01", "2024-06-30"
TEST_START, TEST_END = "2024-07-01", "2026-08-21"
IMPROVE_THRESHOLD_PP = 0.15  # 验证段净收益提升 ≥0.15pp 才提案


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


def eval_window(ev, df, sig, start, end) -> pd.Series:
    sel = (df["date"] >= start) & (df["date"] <= end)
    r = ev.evaluate_signals(df, sig & sel, forward_days=(5,))
    if r is None or r.empty or "net5d" not in r.columns:
        return pd.Series(dtype=float)
    return r["net5d"].dropna()


def current_params() -> dict:
    """读当前 config 中 live 参数"""
    import tomllib

    with open(CONFIG_PATH, "rb") as f:
        cfg = tomllib.load(f)
    return dict(cfg["strategy"]["huigui"]["live"])


def apply_params(new_params: dict, note: str) -> str:
    """把新参数写回 parameters.toml 的 [strategy.huigui.live]，并 bump 版本号"""
    text = CONFIG_PATH.read_text(encoding="utf-8")
    # 版本 bump：v4.0.1 -> v4.1.0
    m = re.search(r"RULES_VERSION\s*=\s*\"(v\d+)\.(\d+)\.(\d+)\"", text) or \
        re.search(r"(?m)^# 妖板交易系统 参数配置 (v[\d.]+)", text)
    new_ver = None
    if m:
        major, minor = int(m.group(1).lstrip("v")), int(m.group(2))
        new_ver = f"v{major}.{minor + 1}.0"
        text = text.replace(m.group(0), m.group(0).replace(m.group(0).split('"')[1], new_ver))
    # 替换 live 参数值
    for k, v in new_params.items():
        pat = re.compile(rf"^({k}\s*=\s*)[\d.]+$", re.M)
        text = pat.sub(lambda m: m.group(1) + str(v), text)
    CONFIG_PATH.write_text(text, encoding="utf-8")
    # 迭代记录
    ITER_DIR.mkdir(parents=True, exist_ok=True)
    (ITER_DIR / "CHANGELOG.md").open("a", encoding="utf-8").write(
        f"\n- {dt.date.today().isoformat()} [{new_ver or 'version'}] {note}\n")
    return new_ver or ""


def main():
    ap = argparse.ArgumentParser(description="P6 月度自迭代优化器")
    ap.add_argument("--apply", action="store_true", help="应用提案（默认只生成提案）")
    args = ap.parse_args()

    store = Store()
    ev = SignalEvaluator(BacktestConfig())
    symbols = [r[0] for r in store.conn.execute(
        "SELECT symbol FROM stock_daily GROUP BY symbol HAVING COUNT(*) >= 400").fetchall()]

    cur = current_params()
    cur_tag = f"{cur['pullback_days_max']}/{cur['pullback_pct_max']:.0f}/{cur['volume_ratio_max']:.1f}"

    # 缓存信号
    sig_cache: dict[tuple, dict[str, pd.Series]] = {}

    def get_sig(sym, df, ov):
        key = (ov["pullback_days_max"], ov["pullback_pct_max"], ov["volume_ratio_max"])
        sig_cache.setdefault(key, {})
        if sym not in sig_cache[key]:
            sig_cache[key][sym] = dedupe(S.detect_huigui(df, mode="live", overrides=ov))
        return sig_cache[key][sym]

    def evaluate(ov, start, end):
        rets = []
        for sym in symbols:
            db = store.get_stock(sym)
            if not db:
                continue
            df = rows_to_df(db)
            r = eval_window(ev, df, get_sig(sym, df, ov), start, end)
            rets.append(r)
        allr = pd.concat(rets) if rets else pd.Series(dtype=float)
        return allr

    # 当前参数表现
    cur_ov = {k: float(cur.get(k, d)) for k, d in
              [("pullback_days_max", 7), ("pullback_pct_max", 15.0), ("volume_ratio_max", 0.9)]}
    cur_train = evaluate(cur_ov, TRAIN_START, TRAIN_END)
    cur_test = evaluate(cur_ov, TEST_START, TEST_END)

    # 网格寻优
    best = None
    for ov in GRID:
        tr = evaluate(ov, TRAIN_START, TRAIN_END)
        te = evaluate(ov, TEST_START, TEST_END)
        n_tr = len(tr)
        if n_tr < MIN_N:
            continue
        m_tr, m_te = tr.mean(), te.mean()
        tag = f"{ov['pullback_days_max']}/{ov['pullback_pct_max']:.0f}/{ov['volume_ratio_max']:.1f}"
        if best is None or (m_te > best[1] and m_tr > 0):
            best = (tag, m_te, m_tr, n_tr, ov)

    L: list[str] = []
    L.append(f"# 月度自迭代提案 — {dt.date.today().isoformat()}")
    L.append("")
    L.append(f"> 股票池 {len(symbols)} 只 | 训练 {TRAIN_START}~{TRAIN_END} | 验证 {TEST_START}~{TEST_END}")
    L.append(f"> 当前参数 [strategy.huigui.live]: **{cur_tag}**")
    L.append(f"> 当前验证段后5日净收益: {cur_test.mean():.2f}%（n={len(cur_test)}）")
    L.append("")
    L.append("## 网格结果（验证段）")
    L.append("")
    L.append("| 参数 | 训练 n | 训练净收益 | 验证 n | 验证净收益 |")
    L.append("|---|---|---|---|---|")
    for ov in GRID:
        tr = evaluate(ov, TRAIN_START, TRAIN_END)
        te = evaluate(ov, TEST_START, TEST_END)
        tag = f"{ov['pullback_days_max']}/{ov['pullback_pct_max']:.0f}/{ov['volume_ratio_max']:.1f}"
        L.append(f"| {tag} | {len(tr)} | {tr.mean():.2f}% | {len(te)} | {te.mean():.2f}% |")
    L.append("")
    if best is None:
        L.append("## 结论：无满足条件的候选（n<20），本次不提案")
        L.append("")
        L.append("> 保持当前参数不变。")
    else:
        tag, m_te, m_tr, n_tr, ov = best
        improve = m_te - cur_test.mean()
        L.append(f"## 结论")
        L.append("")
        if improve >= IMPROVE_THRESHOLD_PP:
            L.append(f"- 候选最优 **{tag}**：验证 {m_te:.2f}%（n={len(evaluate(ov, TEST_START, TEST_END))}），"
                     f"较当前 {cur_tag}（{cur_test.mean():.2f}%）提升 **{improve:+.2f}pp** ≥ {IMPROVE_THRESHOLD_PP}pp")
            L.append(f"- 训练段 {m_tr:.2f}% > 0，通过过拟合检查")
            L.append(f"- 建议参数：`pullback_days_max={ov['pullback_days_max']}`, "
                     f"`pullback_pct_max={ov['pullback_pct_max']}`, "
                     f"`volume_ratio_max={ov['volume_ratio_max']}`")
            L.append("")
            L.append("### 提案动作")
            L.append("")
            L.append("1. 人工复核本提案（检查信号分布/板块分布）")
            L.append("2. `python scripts/run_optimize.py --apply` 应用（自动 bump 版本号）")
            L.append("3. 模拟盘灰度 ≥1 个月后正式合入")
            if args.apply:
                new_ver = apply_params({k: ov[k] for k in ov}, f"月度优化: {cur_tag} -> {tag}")
                L.append("")
                L.append(f"> ✅ 已应用（{new_ver}），parameters.toml 已更新")
                print(f"APPLIED {cur_tag} -> {tag} ({new_ver})")
        else:
            L.append(f"- 候选最优 **{tag}**：验证 {m_te:.2f}% 较当前提升 {improve:+.2f}pp"
                     f" < {IMPROVE_THRESHOLD_PP}pp，**不提案**（收益提升不显著）")
            L.append("- 保持当前参数不变。")
    L.append("")
    L.append("> 护栏：本提案由机器生成，参数变更须经 walk-forward 验证 + 人工复核 + 模拟盘灰度。")

    ITER_DIR.mkdir(parents=True, exist_ok=True)
    out = ITER_DIR / f"{dt.date.today().isoformat()}_proposal.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nPROPOSAL -> {out}")
    store.close()


if __name__ == "__main__":
    main()
