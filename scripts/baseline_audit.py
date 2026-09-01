"""P3 评审阻断项复查：基线口径独立重算（不复用 backtest_daban.py 逻辑）

daily 行结构: [symbol, date, open, high, low, close, volume, amount]
信号 T → T+1(t1) 开盘买入（一字板跳过）→ 持有 1 个交易日 → T+2 退出（A股T+1约束，当天买不能当天卖）
口径A（现状实现）: exit = T+2 close，若 r1[3]=HIGH <= stop 则按 stop（bug：HIGH 当 LOW，止损几乎不触发）
口径B（修正）: exit = T+2 close，若 r1[4]=LOW <= stop 则按 stop（真低点触发）
口径C（B+真实成交）: 止损触发时若 T+2 开盘 <= stop，成交价=开盘价（否则 stop）
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001

def buy_net(px): return px * (1 + COMM + SLIP)
def sell_net(px): return px * (1 - COMM - SLIP - STAMP)

L = ["# P3 阻断项复查：5+ 连板基线口径独立重算（2026-08-26）", ""]
L.append("> 退出日=T+2（T+1 买入后持有 1 交易日，A股 T+1 约束）；止损 -5%（相对买入价）")
L.append("> 口径A=现状实现（HIGH当LOW，止损几乎不触发）；B=真LOW触发；C=B+止损日开盘即破则按开盘价")
L.append("")
L.append("| 年 | 口径A(现状) | 口径B(真止损) | 口径C(真实成交) | 胜率C |")
L.append("|---|---|---|---|---|")

for y in (2023, 2024, 2025, 2026):
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g = df[(df["lb"] >= 5) & ~df["yizi"]].copy()
    qfq = QFQStore(str(y))
    a_vals, b_vals, c_vals = [], [], []
    for _, r in g.iterrows():
        sym, t1, entry_px = r["sym"], r["t1"], None
        # entry_px 需要 t1 开盘价：raw 没有，重新从日线取
        daily = qfq.get_stock(sym)
        if not daily:
            continue
        dates = [x[1] for x in daily]
        if t1 not in dates:
            continue
        i = dates.index(t1)
        if i + 1 >= len(daily):
            continue
        entry_px = float(daily[i][2])  # t1 开盘
        if entry_px <= 0:
            continue
        r1 = daily[i + 1]  # T+2
        op2, hi2, lo2, cl2 = float(r1[2]), float(r1[3]), float(r1[4]), float(r1[5])
        stop = entry_px * 0.95
        # A: HIGH<=stop → stop（几乎不触发）
        exA = stop if hi2 <= stop else cl2
        # B: LOW<=stop → stop
        exB = stop if lo2 <= stop else cl2
        # C: B 基础上，若 op2 <= stop，真实成交=op2（开盘即破，无法按 stop 卖）
        if lo2 <= stop:
            exC = min(op2, stop) if op2 > stop else op2
        else:
            exC = cl2
        a_vals.append((sell_net(exA) / buy_net(entry_px) - 1) * 100)
        b_vals.append((sell_net(exB) / buy_net(entry_px) - 1) * 100)
        c_vals.append((sell_net(exC) / buy_net(entry_px) - 1) * 100)
    a = np.array(a_vals); b = np.array(b_vals); c = np.array(c_vals)
    L.append(f"| {y} | {a.mean():+.3f}%（n={len(a)}） | {b.mean():+.3f}% | {c.mean():+.3f}% | {(c>0).mean()*100:.1f} |")
    # 口径A vs raw CSV pnl_nt 一致性检查
    raw_nt = g["pnl_nt"].mean()
    L.append(f"| 一致性: raw pnl_nt 均值 {raw_nt:+.3f}% vs 重算A {a.mean():+.3f}% | | | | |")
L.append("")
open(OUT / "daban_baseline_audit.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L))
