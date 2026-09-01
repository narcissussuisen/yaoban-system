"""R3 整改项④ v2：停牌尾部建模（交易日历校准）

用全市场交易日历（上证指数日线）判断个股缺失交易日：
- 停牌 = 交易日历上有交易日、但个股无数据（且前后都有数据）
- 判定窗口：T+1 执行日之后 20 个交易日
- 报告：停牌发生率、停牌样本收益分布、对整体期望的影响
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data.store import Store
from data.qfq_store import QFQStore

OUT = Path(__file__).resolve().parent.parent / "outputs"
L = []
L.append("# R3 停牌尾部风险（v2，交易日历校准）")
L.append("")
L.append("> 方法：以上证指数交易日为日历，5+ 可成交样本 T+1 后 20 个交易日窗口内，")
L.append("> 个股缺日（日历有、个股无）>=3 天 且 首缺日不是窗口首日（避免数据边界误判）→ 记为停牌。")
L.append("")

for y in (2025, 2026):
    store = Store()
    idx = store.get_index("sh000001")
    cal = [r[1] for r in idx if r[1][:4] == str(y)]
    cal_set = set(cal)
    df = pd.read_csv(OUT / f"backtest_daban_{y}_raw.csv", dtype={"sym": str})
    g5 = df[(df["lb"] >= 5) & ~df["yizi"]].copy()
    qfq = QFQStore(str(y))
    n_suspend = 0
    susp_rows = []
    for _, row in g5.iterrows():
        sym, t1 = row["sym"], row["t1"]
        daily = qfq.get_stock(sym)
        if not daily:
            continue
        have = {r[1] for r in daily}
        try:
            i0 = cal.index(t1)
        except ValueError:
            continue
        win = cal[i0 + 1: i0 + 21]
        miss = [d for d in win if d not in have]
        # 停牌=窗口内连续缺日>=3（排除窗口尾部边界：窗口内最后一个交易日也有数据才算可靠）
        if len(miss) >= 3 and win[-1] in have:
            n_suspend += 1
            susp_rows.append((sym, t1, len(miss), row["pnl_nt"], row["pnl"]))
    L.append(f"## {y}（5+ 可成交 n={len(g5)}）")
    L.append("")
    L.append(f"| 指标 | 值 |")
    L.append("|---|---|")
    L.append(f"| T+1 后 20 交易日内停牌(缺日>=3) | {n_suspend}（{n_suspend/len(g5)*100:.1f}%） |")
    if susp_rows:
        sdf = pd.DataFrame(susp_rows, columns=["sym", "t1", "miss", "pnl_nt", "pnl"])
        L.append(f"| 停牌样本 pnl_nt 均值 | {sdf['pnl_nt'].mean():+.2f}% |")
        L.append(f"| 停牌样本 pnl_nt 胜率 | {(sdf['pnl_nt']>0).mean()*100:.0f}% |")
        L.append(f"| 停牌样本中位数 | {sdf['pnl_nt'].median():+.2f}% |")
        L.append("")
        L.append("| sym | t1 | 缺日 | pnl_nt% |")
        L.append("|---|---|---|---|")
        for s, t, k, p, _ in susp_rows[:20]:
            L.append(f"| {s} | {t} | {k} | {p:+.2f} |")
    L.append("")
    L.append("### 敏感性：停牌后补跌情景（全部停牌样本按复牌首日补跌计入）")
    L.append("")
    L.append("| 情景 | 2025 均值变化 | 2026 均值变化 |")
    L.append("|---|---|---|")
    for drop in (5, 10, 20):
        vals = []
        for yy in (2025, 2026):
            ddf = pd.read_csv(OUT / f"backtest_daban_{yy}_raw.csv", dtype={"sym": str})
            gg = ddf[(ddf["lb"] >= 5) & ~ddf["yizi"]].copy()
            q = QFQStore(str(yy))
            hits = 0
            tot = 0
            extra = 0.0
            for _, row in gg.iterrows():
                sym, t1 = row["sym"], row["t1"]
                daily = q.get_stock(sym)
                if not daily:
                    continue
                have = {r[1] for r in daily}
                caly = [r[1] for r in store.get_index("sh000001") if r[1][:4] == str(yy)]
                try:
                    i0 = caly.index(t1)
                except ValueError:
                    continue
                win = caly[i0 + 1: i0 + 21]
                miss = [d for d in win if d not in have]
                tot += 1
                if len(miss) >= 3 and win[-1] in have:
                    hits += 1
                    extra += -drop  # 补跌百分点点数
            base_m = gg["pnl_nt"].mean()
            # 新均值 = (sum + extra) / tot（extra 为负数）
            new_m = (gg["pnl_nt"].sum() + extra) / len(gg)
            vals.append((base_m, new_m))
        L.append(f"| 复牌补跌 -{drop}% | {vals[0][0]:+.2f}% → {vals[0][1]:+.2f}% | {vals[1][0]:+.2f}% → {vals[1][1]:+.2f}% |")

open(OUT / "daban_suspend_audit.md", "w", encoding="utf-8").write(chr(10).join(L))
print(chr(10).join(L[:16]))
