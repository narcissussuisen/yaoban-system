"""环境依赖研究：修复后 5+ 打板（W2 保留）分环境桶

问题：修复后 2023-2025 全负、2026 正——是否存在可用环境指标（信号日 T 收盘已知）区分？
指标候选（T 日已知，无前视）：
- 上证指数 20 日趋势（T 收盘 vs 20 日前）
- T 日涨停家数（sentiment zt）
- T 日炸板率
分桶对比 W2 保留组期望
"""
import pandas as pd, numpy as np, json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore
from data.store import Store

OUT = Path(__file__).resolve().parent.parent / "outputs"
CACHE = json.load(open(OUT / "w2_cache.json"))
L = ["# 环境依赖研究：W2 保留组分桶（修复后）", ""]

# 指数数据（全历史）
store = Store()
idx = pd.DataFrame(store.get_index("sh000001"), columns=["symbol","date","open","high","low","close","volume"])
idx = idx.sort_values("date").reset_index(drop=True)
idx["mom20"] = idx["close"] / idx["close"].shift(20) - 1

for y in (2023, 2024, 2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]].dropna(subset=["pnl_nt"]).copy()
    sent = {}
    sp = OUT / f"sentiment_daily_{y}.csv"
    if sp.exists():
        sent = {r["date"]: r for _, r in pd.read_csv(sp).iterrows()}
    rows = []
    for _, r in g.iterrows():
        w = CACHE.get(f"{y}|D|{r['sym']}|{r['t1']}")
        if w is None or w:
            continue  # 只保留 W2 保留组
        sr = sent.get(r["t"])
        m20 = None
        ixr = idx[idx["date"] == r["t"]]
        if len(ixr):
            m20 = float(ixr["mom20"].iloc[0])
        zt_v = int(sr["zt"]) if sr is not None else -1
        zb_v = float(sr["zhaban_rate"]) if sr is not None else -1
        rows.append({"pnl": r["pnl_nt"], "mom20": m20, "zt": zt_v, "zb": zb_v})
    d = pd.DataFrame(rows)
    if d.empty:
        continue
    L.append(f"## {y}（W2 保留 n={len(d)}）")
    L.append("")
    L.append("### 按指数 20 日趋势")
    L.append("")
    L.append("| 趋势 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    for name, mask in (("负（<0）", d["mom20"] < 0), ("正（≥0）", d["mom20"] >= 0)):
        sub = d[mask]
        if len(sub):
            L.append(f"| {name} | {len(sub)} | {sub['pnl'].mean():+.2f} | {(sub['pnl']>0).mean()*100:.0f} |")
    L.append("")
    L.append("### 按涨停家数")
    L.append("")
    L.append("| 涨停家数 | n | 均值% | 胜率% |")
    L.append("|---|---|---|---|")
    for lo, hi, name in ((0, 60, "<60"), (60, 100, "60-100"), (100, 10**9, ">100")):
        sub = d[(d["zt"] >= lo) & (d["zt"] < hi)]
        if len(sub):
            L.append(f"| {name} | {len(sub)} | {sub['pnl'].mean():+.2f} | {(sub['pnl']>0).mean()*100:.0f} |")
    L.append("")
open(OUT / "env_dependency_w2.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))