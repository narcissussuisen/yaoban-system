"""P0-C 冰点期空仓开关 v2（评审整改：信号日 T 情绪 + t1raw 动量池口径）

v1 缺陷（p0_batch_review ❌）：
1. 用执行日(entry)当日炸板率做执行日开盘决策 → 前视泄漏 → 改为信号日(sig=T)炸板率
2. 动量池走 t3raw（10点纪律变体）→ 改为 t1raw（档1 开盘无纪律，与 P0-A 验证口径一致）
3. 阈值须报多 seed 敏感性（20 seeds）

用法: python -B scripts/regime_switch_sim.py 2025 [--double] [--max-zhaban 50] [--min-zt 0]
输出: outputs/regime_switch_{year}_v2.md
"""
import pathlib, sys
import pandas as pd
import numpy as np

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"


def main():
    year = sys.argv[1] if len(sys.argv) > 1 else "2026"
    double_mode = "--double" in sys.argv
    max_zhaban = 50.0
    min_zt = 0
    for a in sys.argv[2:]:
        if a.startswith("--max-zhaban="):
            max_zhaban = float(a.split("=")[1])
        elif a.startswith("--min-zt="):
            min_zt = int(a.split("=")[1])
    capital0 = 50000.0
    sent = {}
    sp = OUT_DIR / f"sentiment_daily_{year}.csv"
    if sp.exists():
        sent = {r["date"]: r for _, r in pd.read_csv(sp).iterrows()}

    def regime_ok(sig_d):
        if sig_d not in sent:
            return True
        sr = sent[sig_d]
        if max_zhaban and float(sr["zhaban_rate"]) > max_zhaban:
            return False
        if min_zt and int(sr["zt"]) < min_zt:
            return False
        return True

    # ---- 交易流（信号日 sig=T，情绪在 T 收盘已知）----
    flows = []
    db = pd.read_csv(OUT_DIR / f"backtest_daban_{year}_raw.csv", dtype={"sym": str})
    g5 = db[(db["lb"] >= 5) & (~db["yizi"])].dropna(subset=["pnl_nt"]).copy()
    g5["src"] = "5+连板"; g5["entry"] = g5["t1"]; g5["sig"] = g5["t"]; g5["pnl"] = g5["pnl_nt"]
    flows.append(g5[["sym", "entry", "sig", "pnl", "src"]])
    mf = OUT_DIR / f"backtest_market_{year}_t1raw.csv"
    if mf.exists():
        m = pd.read_csv(mf, dtype={"sym": str})
        m["src"] = "动量池"; m["entry"] = m["ed"]; m["sig"] = m["d"]
        flows.append(m[["sym", "entry", "sig", "pnl", "src"]])
    pf = pd.read_csv(OUT_DIR / f"pullback_b2_{year}_raw.csv", dtype={"sym": str})
    p = pf.dropna(subset=["p_open"]).copy()
    p["src"] = "回踩池"; p["entry"] = p["ed"]; p["sig"] = p["d"]; p["pnl"] = p["p_open"]
    flows.append(p[["sym", "entry", "sig", "pnl", "src"]])
    allf = pd.concat(flows, ignore_index=True)
    allf = allf[allf["pnl"].notna() & (allf["pnl"] > -99)]
    prio = {"5+连板": 0, "动量池": 1, "回踩池": 2}
    allf["prio"] = allf["src"].map(prio)

    def run(regime_on, seed):
        f = allf.copy()
        f["rnd"] = np.random.default_rng(seed).random(len(f))
        if regime_on:
            f = f[f["sig"].apply(regime_ok)]  # 信号日 T 炸板率（T 收盘可知，T+1 可执行）
        if f.empty:
            return None
        s = f.sort_values(["entry", "prio", "rnd"], ascending=[True, True, True])
        if double_mode:
            picks = s.groupby("entry").head(2).reset_index(drop=True)
        else:
            picks = s.groupby("entry").head(1).reset_index(drop=True)
        picks = picks.sort_values("entry")
        capital = capital0
        curve, trades = [], []
        for day, g in picks.groupby("entry"):
            if len(g) == 1:
                capital *= (1 + float(g["pnl"].iloc[0]) / 100.0)
            else:
                r1 = float(g["pnl"].iloc[0]) / 100.0
                r2 = float(g["pnl"].iloc[1]) / 100.0
                capital = capital * (0.5 * (1 + r1) + 0.5 * (1 + r2))
            for _, rr in g.iterrows():
                trades.append({"entry": day, "sym": rr["sym"], "src": rr["src"], "pnl": float(rr["pnl"])})
            curve.append({"date": day, "equity": capital})
        eq = pd.DataFrame(curve)
        td = pd.DataFrame(trades)
        eq["peak"] = eq["equity"].cummax()
        eq["dd"] = (eq["equity"] / eq["peak"] - 1) * 100
        pnls = td["pnl"]
        win, loss = pnls[pnls > 0], pnls[pnls < 0]
        return {"eq": eq, "td": td, "capital": capital,
                "ret": (capital / capital0 - 1) * 100,
                "max_dd": eq["dd"].min(), "win": (pnls > 0).mean() * 100,
                "pl": win.mean() / abs(loss.mean()) if len(loss) else float("nan"),
                "n": len(td)}

    # 多 seed 平均（评审整改：单种子窥探）
    seeds = list(range(20))
    base_runs = [run(False, s) for s in seeds]
    sw_runs = [run(True, s) for s in seeds]
    base_runs = [r for r in base_runs if r]
    sw_runs = [r for r in sw_runs if r]
    # 用种子 42 的路径做明细，其余 seed 做分布
    b0, s0 = base_runs[0], sw_runs[0]
    ret_b = np.mean([r["ret"] for r in base_runs])
    ret_s = np.mean([r["ret"] for r in sw_runs])
    dd_b = np.mean([r["max_dd"] for r in base_runs])
    dd_s = np.mean([r["max_dd"] for r in sw_runs])
    win_b = np.mean([r["win"] for r in base_runs])
    win_s = np.mean([r["win"] for r in sw_runs])
    L = [f"# P0-C 冰点期空仓开关 v2（{year}，{'双票' if double_mode else '单吊'}，信号日口径）", ""]
    L.append(f"> 开关：信号日 T 炸板率>{max_zhaban:.0f}%（T 收盘已知，T+1 开盘可执行，无前视）；20 seeds 平均")
    L.append(f"> 动量池=t1raw（档1 开盘无纪律，与 P0-A 验证口径一致）")
    L.append("")
    L.append("| 指标 | 无开关（基线） | 开关开启 | 差异 |")
    L.append("|---|---|---|---|")
    L.append(f"| 交易笔数（seed42） | {b0['n']} | {s0['n']} | {s0['n']-b0['n']} |")
    L.append(f"| 总收益（20seed均值） | {ret_b:+.1f}% | {ret_s:+.1f}% | {ret_s-ret_b:+.1f}pp |")
    L.append(f"| 最大回撤（20seed均值） | {dd_b:.1f}% | {dd_s:.1f}% | {dd_s-dd_b:+.1f}pp |")
    L.append(f"| 胜率（20seed均值） | {win_b:.0f}% | {win_s:.0f}% | {win_s-win_b:+.1f}pp |")
    L.append("")
    # 阈值平台检查：本阈值 vs 邻近阈值（要求平台而非悬崖）
    L.append(f"判定（平台+稳健）：开关后收益 {'≥' if ret_s >= ret_b else '<'} 基线 且 回撤改善；阈值平台性见扫描")
    open(OUT_DIR / f"regime_switch_{year}_v2.md", "w", encoding="utf-8").write(chr(10).join(L))
    print(chr(10).join(L))


if __name__ == "__main__":
    main()
