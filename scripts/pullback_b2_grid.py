"""R1.2b 组合条件网格搜索：B2 真低吸的正期望区域（读 pullback_b2_{year}_raw.csv）

网格维度：gap 阈值 × dd 区间 × dpk 上限；目标：B2 低吸均值>0 且胜率≥50% 且 n≥50
输出: outputs/pullback_b2_grid_{year}.md
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import pandas as pd  # noqa: E402

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"


def main():
    year = sys.argv[1] if len(sys.argv) > 1 else "2026"
    csv = OUT_DIR / f"pullback_b2_{year}_raw.csv"
    df = pd.read_csv(csv)
    L = [f"# B2 真低吸组合网格 {year}（n={len(df)}）", "",
         "> 目标：均值>0 且胜率≥50% 且 n≥50。p_b2_low=B2≤开盘+0.5%。", ""]
    results = []
    for gap_th in (0.0, -0.5, -1.0, -1.5, -2.0, -3.0):
        for (dd_lo, dd_hi) in ((2, 20), (3, 15), (5, 15), (6, 15), (8, 20), (2, 8)):
            for dpk_max in (2, 4, 8):
                g = df[(df["gap"] <= gap_th) & (df["dd"] >= dd_lo) & (df["dd"] <= dd_hi)
                       & (df["dpk"] <= dpk_max)].dropna(subset=["p_b2_low"])
                if len(g) < 30:
                    continue
                results.append({"gap<=": gap_th, "dd": f"{dd_lo}-{dd_hi}", "dpk<=": dpk_max,
                                "n": len(g), "mean": g["p_b2_low"].mean(),
                                "win": (g["p_b2_low"] > 0).mean() * 100,
                                "open_mean": g["p_open"].mean()})
    r = pd.DataFrame(results).sort_values("mean", ascending=False)
    L.append("## 正期望区域（按均值降序，前 25）")
    L.append("")
    L.append("| gap≤ | dd | dpk≤ | n | B2低吸均值% | 胜率% | 开盘均值% |")
    L.append("|---|---|---|---|---|---|---|")
    for _, x in r.head(25).iterrows():
        mark = " ✅" if (x["mean"] > 0 and x["win"] >= 50 and x["n"] >= 50) else ""
        L.append(f"| {x['gap<=']}% | {x['dd']}% | {x['dpk<=']} | {int(x['n'])} | {x['mean']:+.2f} "
                 f"| {x['win']:.0f} | {x['open_mean']:+.2f} |{mark}")
    L.append("")
    L.append("## 达标组合（均值>0 且胜率≥50% 且 n≥50）")
    L.append("")
    ok = r[(r["mean"] > 0) & (r["win"] >= 50) & (r["n"] >= 50)]
    if len(ok):
        L.append("| gap≤ | dd | dpk≤ | n | 均值% | 胜率% |")
        L.append("|---|---|---|---|---|---|")
        for _, x in ok.iterrows():
            L.append(f"| {x['gap<=']}% | {x['dd']}% | {x['dpk<=']} | {int(x['n'])} | {x['mean']:+.2f} | {x['win']:.0f} |")
    else:
        L.append("- 无达标组合（需放宽标准或补充条件）")
    L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    outp = OUT_DIR / f"pullback_b2_grid_{year}.md"
    outp.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {outp}")


if __name__ == "__main__":
    main()
