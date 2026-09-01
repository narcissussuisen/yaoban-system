"""P3 遗留：修复后滑点敏感性重验（5+ 连板 + W2 规避层）

修复前结论"滑点0.3%四年仍全正"基于 bug 口径。修复后重验：
- 5+ 全量（修复后 pnl_nt）
- 5+ W2 保留（修复后，开盘口径）
- 5+ W2 保留 + 10:00 执行（可执行口径）
滑点 0.1%→0.2%→0.3%
"""
import pandas as pd, numpy as np, json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"
CACHE = json.load(open(OUT / "w2_cache.json"))
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001

def buy_net(px, slip): return px * (1 + COMM + slip)
def sell_net(px, slip): return px * (1 - COMM - slip - STAMP)

L = ["# P3 遗留：修复后滑点敏感性（2026-08-26）", ""]
L.append("| 年 | 口径 | n | 滑点0.1% | 滑点0.2% | 滑点0.3% |")
L.append("|---|---|---|---|---|---|")

for y in (2023, 2024, 2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]].dropna(subset=["pnl_nt"]).copy()
    qfq = QFQStore(str(y))
    # 用 raw 的 pnl_nt 反推毛比，再按不同滑点重算
    # pnl_nt = sell_net(exit,0.001)/buy_net(entry,0.001) - 1 → ratio = exit/entry
    rows = []
    for _, r in g.iterrows():
        w = CACHE.get(f"{y}|D|{r['sym']}|{r['t1']}")
        if w is None:
            continue
        # 反推 exit/entry 毛比（用 0.1% 滑点口径）
        bf = 1 + COMM + SLIP
        sf = 1 - COMM - SLIP - STAMP
        ratio = (1 + r["pnl_nt"] / 100) * bf / sf
        rows.append({"pnl": r["pnl_nt"], "weak": w, "ratio": ratio})
    d = pd.DataFrame(rows)
    for name, sub in (("全量", d), ("W2保留", d[~d["weak"]])):
        if len(sub) == 0:
            continue
        vals = []
        for slip in (0.001, 0.002, 0.003):
            bf2 = 1 + COMM + slip
            sf2 = 1 - COMM - slip - STAMP
            pnls = (sub["ratio"] * sf2 / bf2 - 1) * 100
            vals.append(pnls.mean())
        L.append(f"| {y} | {name} | {len(sub)} | {vals[0]:+.2f} | {vals[1]:+.2f} | {vals[2]:+.2f} |")
    L.append("")
open(OUT / "slip_sensitivity_fixed.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
