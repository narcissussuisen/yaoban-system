"""P2-B 变体1：W2 + 极端情绪日（信号日炸板率>50）空仓

2025 剔除组 -0.24% 说明 W2 在弱市价值大；2026 剔除组 +0.86%（强市）。
变体：炸板率>50% 的信号日整体不交易（对动量池），叠加 W2。
"""
import pandas as pd, numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"

def w2(qfq, sym, ed):
    daily = qfq.get_stock(sym)
    if not daily: return None
    dates = [x[1] for x in daily]
    if ed not in dates: return None
    i = dates.index(ed)
    if i < 1: return None
    prev_low = float(daily[i-1][3])
    mrows = qfq.get_minute(sym, start=ed, end=ed)
    if not mrows: return None
    day = pd.DataFrame(mrows, columns=["symbol","freq","ts","open","high","low","close","volume","amount"])
    low30 = float(day["low"].iloc[:30].min())
    return low30 < prev_low

L = ["# P2-B 变体1：W2 + 信号日炸板率>50 空仓（修复后）", ""]
L.append("| 年 | 口径 | n | 均值% | 胜率% | t |")
L.append("|---|---|---|---|---|---|")
for y in (2025, 2026):
    df = pd.read_csv(OUT / f"backtest_market_{y}_t1raw.csv", dtype={"sym": str})
    sent = {}
    sp = OUT / f"sentiment_daily_{y}.csv"
    if sp.exists():
        sent = {r["date"]: r for _, r in pd.read_csv(sp).iterrows()}
    qfq = QFQStore(str(y))
    rows = []
    for _, r in df.iterrows():
        w = w2(qfq, r["sym"], r["ed"])
        sr = sent.get(r["d"])
        bad_env = sr is not None and float(sr["zhaban_rate"]) > 50
        if w is not None:
            rows.append({"pnl": r["pnl"], "weak": w, "bad_env": bad_env})
    d = pd.DataFrame(rows)
    # 组合：W2 保留 且 非 bad_env
    final = d[(~d["weak"]) & (~d["bad_env"])]
    n = len(final)
    m = final["pnl"].mean()
    sd = final["pnl"].std()
    t = m/(sd/np.sqrt(n)) if n > 1 and sd > 0 else 0
    wrate = (final["pnl"]>0).mean()*100
    L.append(f"| {y} | W2保留+非冰点 | {n} | {m:+.2f} | {wrate:.0f} | {t:.2f} |")
    # 对照
    base = d[~d["weak"]]
    L.append(f"| {y} | W2单独（对照） | {len(base)} | {base['pnl'].mean():+.2f} | {(base['pnl']>0).mean()*100:.0f} | — |")
    L.append("")
open(OUT / "intraday_bpoint_variant1.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
