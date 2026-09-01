"""R1' 自验：detect_huigui_v5 × 选手实盘名单覆盖率（2026）"""
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.qfq_store import QFQStore
from core.strategies import detect_huigui_v5

# 选手实盘名单（2026-04-17 ~ 08-21 F盘覆盖范围）
anchor = {
    "002580": "2026-04-07", "002962": "2026-04-20", "603002": "2026-04-21",
    "603178": "2026-04-21", "002176": "2026-04-24", "605006": "2026-05-07",
    "002015": "2026-05-13", "603316": "2026-05-20", "002156": "2026-05-20",
    "603158": "2026-05-20", "002617": "2026-05-26", "600719": "2026-05-26",
    "000700": "2026-05-26", "000021": "2026-05-26", "600207": "2026-05-27",
    "603693": "2026-05-27", "605589": "2026-06-02", "000733": "2026-06-03",
    "600667": "2026-06-03", "601137": "2026-06-05", "603823": "2026-06-08",
    "002747": "2026-06-08", "002669": "2026-06-08", "600552": "2026-06-16",
    "000823": "2026-06-17", "600063": "2026-06-17", "002897": "2026-06-18",
    "002407": "2026-06-22", "600360": "2026-06-23", "000737": "2026-06-23",
    "600584": "2026-06-26", "603005": "2026-06-26", "605020": "2026-06-26",
    "603078": "2026-06-26", "601958": "2026-06-29", "002584": "2026-07-02",
    "600379": "2026-07-06", "002137": "2026-07-09", "000948": "2026-07-20",
    "001206": "2026-07-20", "002379": "2026-07-22", "600722": "2026-07-23",
    "001317": "2026-07-27", "000676": "2026-07-28", "002539": "2026-07-29",
    "002303": "2026-08-04", "002197": "2026-08-04", "600176": "2026-08-06",
    "603459": "2026-08-06", "300725": "2026-08-07", "002131": "2026-08-13",
    "603738": "2026-08-17", "603065": "2026-08-18",
}
print(f"锚名单: {len(anchor)} 只")

qfq = QFQStore("2026")
hits = []
misses = []
for sym, date in sorted(anchor.items()):
    rows = qfq.get_stock(sym)
    if not rows:
        misses.append((sym, date, "无数据"))
        continue
    df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"])
    # 触发窗口：买入日前后 3 个交易日内信号
    dates = df["date"].astype(str).tolist()
    if date not in dates:
        misses.append((sym, date, "日期不在F盘"))
        continue
    i = dates.index(date)
    sig = detect_huigui_v5(df)
    win = sig.iloc[max(0, i - 3): i + 1]  # 止跌信号在买入日前 0-3 天内（买入=止跌日或次日确认）
    hit_day = None
    for k in range(len(win)):
        if win.iloc[k]:
            hit_day = dates[max(0, i - 3) + k]
            break
    if hit_day:
        hits.append((sym, date, hit_day))
    else:
        misses.append((sym, date, "无信号"))

print(f"\n覆盖率: {len(hits)}/{len(anchor)} = {len(hits)/len(anchor)*100:.0f}%")
print("\n命中样例（前10）:")
for h in hits[:10]:
    print(f"  {h[0]} 买入{h[1]} 信号{h[2]}")
print("\n未命中（前15）:")
for m in misses[:15]:
    print(f"  {m[0]} {m[1]} {m[2]}")