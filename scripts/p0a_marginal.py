"""P0-A 整改①：组合级边际对照（5+ 单独 vs 5++辅助池）

评审整改：
- 动量池改用 t1raw（档1 开盘无纪律，与 P0-A 验证口径一致）
- 出"5+ 单独" vs "5++动量" vs "5++动量+回踩" 三年（2024/2025/2026）对照
- 20 seeds 平均（消除单路径方差）
"""
import pathlib, sys
import pandas as pd
import numpy as np

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"
capital0 = 50000.0


def load_flows(year):
    flows = []
    db = pd.read_csv(OUT_DIR / f"backtest_daban_{year}_raw.csv", dtype={"sym": str})
    g5 = db[(db["lb"] >= 5) & (~db["yizi"])].dropna(subset=["pnl_nt"]).copy()
    g5["src"] = "5+连板"; g5["entry"] = g5["t1"]; g5["pnl"] = g5["pnl_nt"]
    flows.append(g5[["sym", "entry", "pnl", "src"]])
    mf = OUT_DIR / f"backtest_market_{year}_t1raw.csv"
    m = None
    if mf.exists():
        m = pd.read_csv(mf, dtype={"sym": str})
        m["src"] = "动量池"; m["entry"] = m["ed"]
    pf = pd.read_csv(OUT_DIR / f"pullback_b2_{year}_raw.csv", dtype={"sym": str})
    p = pf.dropna(subset=["p_open"]).copy()
    p["src"] = "回踩池"; p["entry"] = p["ed"]; p["pnl"] = p["p_open"]
    return g5[["sym", "entry", "pnl", "src"]], (m[["sym", "entry", "pnl", "src"]] if m is not None else None), p[["sym", "entry", "pnl", "src"]]


def run_combo(parts, double, seed):
    allf = pd.concat(parts, ignore_index=True)
    allf = allf[allf["pnl"].notna() & (allf["pnl"] > -99)]
    prio = {"5+连板": 0, "动量池": 1, "回踩池": 2}
    allf["prio"] = allf["src"].map(prio)
    allf["rnd"] = np.random.default_rng(seed).random(len(allf))
    s = allf.sort_values(["entry", "prio", "rnd"], ascending=[True, True, True])
    if double:
        picks = s.groupby("entry").head(2).reset_index(drop=True)
    else:
        picks = s.groupby("entry").head(1).reset_index(drop=True)
    picks = picks.sort_values("entry")
    capital = capital0
    for day, g in picks.groupby("entry"):
        if len(g) == 1:
            capital *= (1 + float(g["pnl"].iloc[0]) / 100.0)
        else:
            r1 = float(g["pnl"].iloc[0]) / 100.0
            r2 = float(g["pnl"].iloc[1]) / 100.0
            capital = capital * (0.5 * (1 + r1) + 0.5 * (1 + r2))
    return (capital / capital0 - 1) * 100


L = ["# P0-A 整改①：组合级边际对照（t1raw 口径，20 seeds）", ""]
L.append("> 动量池=档1 开盘无纪律（t1raw）；5+ 连板=可成交 pnl_nt；回踩池=开盘执行 p_open。")
L.append("")
L.append("| 年 | 组合 | 单吊收益（20seed均值） | 双票收益（20seed均值） |")
L.append("|---|---|---|---|")
for y in (2023, 2024, 2025, 2026):
    g5, m, p = load_flows(str(y))
    combos = {
        "5+ 单独": [g5],
        "5++动量": [g5, m] if m is not None else [g5],
        "5++动量+回踩": [g5, m, p] if m is not None else [g5, p],
    }
    for name, parts in combos.items():
        s_single = np.mean([run_combo(parts, False, s) for s in range(20)])
        s_double = np.mean([run_combo(parts, True, s) for s in range(20)])
        L.append(f"| {y} | {name} | {s_single:+.1f}% | {s_double:+.1f}% |")
    L.append("")
open(OUT_DIR / "p0a_marginal_combo.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
