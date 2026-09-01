"""R3' 卖出时点回放（正式版）：判定=选手卖出日 ±1 天内引擎触发离场"""
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.qfq_store import QFQStore
from core.sell import simulate_hold

qfq = QFQStore("2026")
# (sym, 建仓日, 买入价, 选手卖出日, 理由)
cases = [
    ("002580", "2026-04-07", 16.065, "2026-04-21", "开盘出局落袋（热度高远离）"),
    ("603713", "2026-07-07", 80.835, "2026-07-08", "出线死叉+破5日线"),
    ("002354", "2026-06-01", 7.132, "2026-06-02", "低开不及预期可走"),
    ("601137", "2026-06-05", 23.677, "2026-06-12", "高开冲高兑现"),
    ("600360", "2026-06-23", 12.424, "2026-06-24", "冲高兑现"),
    ("000733", "2026-06-03", 54.94, "2026-06-05", "分时破均价线落袋"),
    ("600667", "2026-06-03", 15.505, "2026-06-05", "分时破均价线落袋"),
    ("603005", "2026-06-26", 48.785, "2026-06-29", "隔天冲高不拉板跌分时线就走"),
    ("600584", "2026-06-24", 88.647, "2026-06-26", "收获20%落袋"),
    ("002407", "2026-06-22", 39.792, "2026-06-23", "冲高没封板止盈"),
    ("001317", "2026-07-27", 48.955, "2026-07-28", "3点前不涨停分批止盈"),
    ("600722", "2026-07-23", 10.273, "2026-07-24", "破均价线不拉板收获10%"),
]
ok = 0
L = ["# R3' 卖出时点回放验证（第一轮）", ""]
L.append("| 案例 | 建仓 | 选手卖出 | 理由 | 引擎卖出 | 判定 |")
L.append("|---|---|---|---|---|---|")
for sym, entry_d, entry_px, sell_d, reason in cases:
    rows = qfq.get_stock(sym)
    if not rows:
        L.append(f"| {sym} | {entry_d} | {sell_d} | {reason} | 无数据 | ✗ |")
        continue
    daily = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"])
    res = simulate_hold(qfq, sym, entry_d + " 09:31", entry_px, 10000, horizon_days=10, daily_df=daily)
    sells = [f for f in res["fills"] if f["side"] == "sell"]
    sell_days = sorted({f["ts"][:10] for f in sells})
    sell_desc = "; ".join(f"{f['ts'][:10]}:{f['reason']}" for f in sells[:4])
    # 判定：选手卖出日 ±1 天内有无离场触发
    near = any(abs((pd.Timestamp(d) - pd.Timestamp(sell_d)).days) <= 1 for d in sell_days)
    if near:
        ok += 1
    L.append(f"| {sym} | {entry_d} | {sell_d} | {reason} | {sell_desc} | {'✓' if near else '✗'} |")
L.append("")
L.append(f"**回放命中率: {ok}/{len(cases)}**（±1 天判定）")
out = Path(__file__).resolve().parent.parent / "outputs" / "r3p_replay.md"
out.write_text(chr(10).join(L), encoding="utf-8")
print(chr(10).join(L))
