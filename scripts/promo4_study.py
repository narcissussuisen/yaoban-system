"""P0-D 4 板晋级特征研究

从 backtest_daban raw 取 lb==4 的行（T 日 4 板，T+1 执行），
用 T 日分钟数据提取：首封时间、炸板次数（触及涨停-回落-再封）、封板时长，
交叉情绪（涨停家数/炸板率）→ 分组区分度。
"""
import pandas as pd
import numpy as np
from math import sqrt
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore
from core.sell import limit_pct_of

OUT = Path(__file__).resolve().parent.parent / "outputs"
L = []
L.append("# P0-D 4 板晋级特征（2025/2026）")
L.append("")

def zhaban_features(mrows, limit_px):
    """从 T 日分钟线提取封板特征：首封时间、炸板次数"""
    if not mrows:
        return None, None, None
    hi = [float(r[4]) for r in mrows]
    ts = [r[2] for r in mrows]
    n = len(hi)
    if n == 0:
        return None, None, None
    # 首封：第一根 high>=limit 的分钟
    first_seal = None
    for i in range(n):
        if hi[i] >= limit_px - 0.01:
            first_seal = ts[i][11:16]
            break
    # 炸板次数：high>=limit 后回落（后续 high < limit-0.01 且再触及）计数
    seals = 0
    above = False
    for i in range(n):
        if hi[i] >= limit_px - 0.01:
            if not above:
                seals += 1
            above = True
        else:
            above = False
    return first_seal, seals, n


for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g4 = df[df["lb"] == 4].copy()
    sent = {}
    sp = OUT / f"sentiment_daily_{y}.csv"
    if sp.exists():
        sent = {r["date"]: r for _, r in pd.read_csv(sp).iterrows()}
    qfq = QFQStore(str(y))
    rows = []
    for _, r in g4.iterrows():
        sym, t = r["sym"], r["t"]
        lpx = limit_pct_of(sym)
        daily = qfq.get_stock(sym)
        if not daily:
            continue
        dates = [x[1] for x in daily]
        if t not in dates:
            continue
        i = dates.index(t)
        if i < 1:
            continue
        prev_c = float(daily[i - 1][5])
        limit_px = round(prev_c * (1 + lpx), 2)
        mrows = qfq.get_minute(sym, start=t, end=t)
        fs, seals, nbar = zhaban_features(mrows, limit_px)
        sr = sent.get(t)
        zt_v = int(sr["zt"]) if sr is not None else -1
        zb_v = float(sr["zhaban_rate"]) if sr is not None else -1
        rows.append({"sym": sym, "t": t, "pnl_nt": r["pnl_nt"], "yizi": r["yizi"],
                     "first_seal": fs, "seals": seals,
                     "zt": zt_v, "zhaban_rate": zb_v})
    d = pd.DataFrame(rows)
    d = d[d["yizi"] == False]
    L.append(f"## {y}（4 板可成交 n={len(d)}）")
    L.append("")
    # 1) 次日表现总览
    L.append(f"| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| 次日均值 | {d['pnl_nt'].mean():+.2f}% |")
    L.append(f"| 胜率 | {(d['pnl_nt']>0).mean()*100:.0f}% |")
    L.append("")
    # 2) 按炸板次数
    if d["seals"].notna().any():
        L.append("### 按炸板次数（T 日）")
        L.append("")
        L.append("| 炸板次数 | n | 均值% | 胜率% |")
        L.append("|---|---|---|---|")
        for s in sorted(d["seals"].dropna().unique()):
            gg = d[d["seals"] == s]
            L.append(f"| {int(s)} | {len(gg)} | {gg['pnl_nt'].mean():+.2f} | {(gg['pnl_nt']>0).mean()*100:.0f} |")
    # 3) 按首封时间
    if d["first_seal"].notna().any():
        d["early"] = d["first_seal"].apply(lambda x: "早封(≤10:30)" if x and x <= "10:30" else ("中封" if x and x <= "13:30" else "尾盘(>13:30)"))
        L.append("")
        L.append("### 按首封时间")
        L.append("")
        L.append("| 首封 | n | 均值% | 胜率% |")
        L.append("|---|---|---|---|")
        for k, gg in d.groupby("early"):
            L.append(f"| {k} | {len(gg)} | {gg['pnl_nt'].mean():+.2f} | {(gg['pnl_nt']>0).mean()*100:.0f} |")
    # 4) 情绪交叉
    L.append("")
    L.append("### 情绪交叉（炸板率 ≤35 vs >35）")
    L.append("")
    L.append("| 炸板率 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    for lo, hi, name in ((0, 35, "≤35"), (35, 101, ">35")):
        gg = d[(d["zhaban_rate"] >= lo) & (d["zhaban_rate"] < hi)]
        if len(gg):
            L.append(f"| {name} | {len(gg)} | {gg['pnl_nt'].mean():+.2f} | {(gg['pnl_nt']>0).mean()*100:.0f} |")
    L.append("")

open(OUT / "promo4_findings.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L[:14]))