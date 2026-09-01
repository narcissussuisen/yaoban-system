"""R1' 分组验证：上升回档组覆盖率（按三片报告买入逻辑分类）"""
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.qfq_store import QFQStore
from core.strategies import detect_huigui_v5

# 战法分类（依据三片精读报告的"买入逻辑"字段）
huigui = {  # 上升回档组（约40只）
    "002580": "2026-04-07", "002962": "2026-04-20", "603002": "2026-04-21",
    "603178": "2026-04-21", "002176": "2026-04-24", "605006": "2026-05-07",
    "002015": "2026-05-13", "603316": "2026-05-20", "002156": "2026-05-20",
    "603158": "2026-05-20", "002617": "2026-05-26", "600719": "2026-05-26",
    "000700": "2026-05-26", "000021": "2026-05-26", "600207": "2026-05-27",
    "603693": "2026-05-27", "000733": "2026-06-03", "600667": "2026-06-03",
    "603823": "2026-06-08", "002669": "2026-06-08", "600552": "2026-06-16",
    "000823": "2026-06-17", "600063": "2026-06-17", "002897": "2026-06-18",
    "002407": "2026-06-22", "600360": "2026-06-23", "600584": "2026-06-26",
    "603005": "2026-06-26", "603078": "2026-06-26", "601958": "2026-06-29",
    "002584": "2026-07-02", "600379": "2026-07-06", "002137": "2026-07-09",
    "000948": "2026-07-20", "001206": "2026-07-20", "002379": "2026-07-22",
    "001317": "2026-07-27", "002303": "2026-08-04", "002197": "2026-08-04",
    "603459": "2026-08-06", "603738": "2026-08-17", "603065": "2026-08-18",
}
fanbao = {  # 趋势反包组
    "605589": "2026-06-02", "601137": "2026-06-05", "605020": "2026-06-26",
    "300725": "2026-08-07",
}
chaodie = {  # 超跌反弹/大底组
    "000737": "2026-06-23", "600176": "2026-08-06", "000676": "2026-07-28",
    "002539": "2026-07-29",
}
qita = {  # 其他（接力/仙人指路/未明）
    "600722": "2026-07-23", "002747": "2026-06-08", "002131": "2026-08-13",
}

print(f"分组: 上升回档{len(huigui)} / 反包{len(fanbao)} / 超跌{len(chaodie)} / 其他{len(qita)}")

qfq25 = QFQStore("2025")
qfq26 = QFQStore("2026")
hits, misses = [], []
for sym, date in sorted(huigui.items()):
    rows25 = qfq25.get_stock(sym) or []
    rows26 = qfq26.get_stock(sym) or []
    rows = rows25 + rows26
    if not rows:
        misses.append((sym, date, "无数据")); continue
    df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"]).drop_duplicates(subset=["date"])
    dates = df["date"].astype(str).tolist()
    if date not in dates:
        misses.append((sym, date, "日期不在")); continue
    i = dates.index(date)
    sig = detect_huigui_v5(df)
    win = sig.iloc[max(0, i - 5): i + 1]  # 止跌信号在买入日前 0-5 天（止跌→次日确认买）
    hit = None
    for k in range(len(win)):
        if win.iloc[k]:
            hit = dates[max(0, i - 5) + k]
            break
    if hit:
        hits.append((sym, date, hit))
    else:
        misses.append((sym, date, "无信号"))

print(f"\n上升回档组覆盖率: {len(hits)}/{len(huigui)} = {len(hits)/len(huigui)*100:.0f}%")
print("命中:")
for h in hits:
    print(f"  {h[0]} 买入{h[1]} 止跌信号{h[2]}")
print("未命中:")
for m in misses:
    print(f"  {m[0]} {m[1]} {m[2]}")