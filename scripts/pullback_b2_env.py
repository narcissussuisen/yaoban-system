"""R1.2c 形态池 × 情绪过滤交叉验证（读 raw CSV + sentiment/环境，事后过滤）

口径对齐动量池验证：信号日 env≥2 且 炸板率≤35%（可选 zt≥71）
输出: outputs/pullback_b2_env_{year}.md
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from data.store import Store  # noqa: E402
from core.env_score import env_score  # noqa: E402

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "outputs"


def main():
    year = sys.argv[1] if len(sys.argv) > 1 else "2026"
    csv = OUT_DIR / f"pullback_b2_{year}_raw.csv"
    df = pd.read_csv(csv)
    # 情绪
    sent = {}
    sp = OUT_DIR / f"sentiment_daily_{year}.csv"
    if sp.exists():
        sent = {r["date"]: r for _, r in pd.read_csv(sp).iterrows()}
    # 环境分（简版）
    store = Store()
    idx = pd.DataFrame(store.get_index("sh000001"),
                       columns=["symbol", "date", "open", "high", "low", "close", "volume"])
    def env_asof(d):
        sub = idx[idx["date"] <= d]
        if len(sub) < 25:
            return 0
        r = env_score(sub)
        return int(r["dims"]["trend"] + r["dims"]["volume"])
    df["env"] = df["d"].map(env_asof)
    df["zt"] = df["d"].map(lambda d: int(sent[d]["zt"]) if d in sent else -1)
    df["zhaban_rate"] = df["d"].map(lambda d: float(sent[d]["zhaban_rate"]) if d in sent else -1)

    L = [f"# 回踩形态池 × 情绪过滤 {year}（原始 n={len(df)}）", ""]
    filters = [
        ("全量", df),
        ("env≥2", df[df["env"] >= 2]),
        ("炸板率≤35", df[(df["zhaban_rate"] <= 35) | (df["zhaban_rate"] < 0)]),
        ("env≥2 + 炸板率≤35", df[((df["env"] >= 2) & ((df["zhaban_rate"] <= 35) | (df["zhaban_rate"] < 0)))]),
        ("env≥2 + 炸板率≤35 + 涨停≥71", df[((df["env"] >= 2) & ((df["zhaban_rate"] <= 35) | (df["zhaban_rate"] < 0))
                                            & ((df["zt"] >= 71) | (df["zt"] < 0)))]),
    ]
    for name, g in filters:
        if len(g) < 30:
            L.append(f"## {name}: n={len(g)}（样本不足）")
            L.append("")
            continue
        L.append(f"## {name}（n={len(g)}）")
        L.append("")
        L.append("| 执行 | n | 均值% | 中位% | 胜率% |")
        L.append("|---|---|---|---|---|")
        for col, cname in (("p_open", "A 开盘执行"), ("p_b2", "B B2任意"),
                           ("p_b2_low", "C B2真低吸"), ("p_b2_lowgap", "D B2低吸+低开")):
            gg = g[col].dropna()
            if len(gg):
                L.append(f"| {cname} | {len(gg)} | {gg.mean():+.2f} | {gg.median():+.2f} "
                         f"| {(gg > 0).mean() * 100:.0f} |")
        L.append("")
    L.append("> 仅供方法论研究，不构成投资建议。")
    outp = OUT_DIR / f"pullback_b2_env_{year}.md"
    outp.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\nREPORT -> {outp}")
    store.close()


if __name__ == "__main__":
    main()
