"""R1' 整改 R1.2：锚名单审计后验证（清洗分母 + 最近信号 + k 分布）"""
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.qfq_store import QFQStore
import core.strategies as S

# 审计后锚名单（剔除：002897意华=4天2板接力, 002197证通=反包, 603158腾龙=低吸；
# 补入：001337四川黄金, 002104恒宝, 600721百花, 600360华微7-9, 600667太极8-13；
# 长电600584日期修正 6/26→6/24）
huigui_audited = {
    "002580": "2026-04-07", "002962": "2026-04-20", "603002": "2026-04-21",
    "603178": "2026-04-21", "002176": "2026-04-24", "605006": "2026-05-07",
    "002015": "2026-05-13", "603316": "2026-05-20", "002156": "2026-05-20",
    "002617": "2026-05-26", "600719": "2026-05-26", "000700": "2026-05-26",
    "000021": "2026-05-26", "600207": "2026-05-27", "603693": "2026-05-27",
    "000733": "2026-06-03", "600667": "2026-06-03", "603823": "2026-06-08",
    "002669": "2026-06-08", "600552": "2026-06-16", "000823": "2026-06-17",
    "600063": "2026-06-17", "002407": "2026-06-22", "600360": "2026-06-23",
    "600584": "2026-06-24", "603005": "2026-06-26", "603078": "2026-06-26",
    "601958": "2026-06-29", "002584": "2026-07-02", "600379": "2026-07-06",
    "002137": "2026-07-09", "600360b": "2026-07-09", "000948": "2026-07-20",
    "001206": "2026-07-20", "002379": "2026-07-22", "001317": "2026-07-27",
    "002303": "2026-08-04", "603459": "2026-08-06", "603738": "2026-08-17",
    "603065": "2026-08-18", "001337": "2026-08-24", "002104": "2026-08-24",
    "600721": "2026-08-25", "600667b": "2026-08-13",
}
print(f"审计后锚名单: {len(huigui_audited)} 只")

qfq25 = QFQStore("2025")
qfq26 = QFQStore("2026")
hits, misses = [], []
for sym, date in sorted(huigui_audited.items()):
    s = sym.replace("b", "")  # 处理重复代码
    rows25 = qfq25.get_stock(s) or []
    rows26 = qfq26.get_stock(s) or []
    rows = rows25 + rows26
    # daily 回填库补充（F盘缺 8/24-8/26 时）
    dp = Path(r"F:/WorkBuddyItem/a股level2/daily") / f"{s}.parquet"
    if dp.exists():
        extra = pd.read_parquet(dp)
        extra_rows = list(extra[["symbol", "date", "open", "high", "low", "close", "volume", "amount"]].itertuples(index=False, name=None))
        rows = rows + extra_rows
    if not rows:
        misses.append((sym, date, "无数据")); continue
    df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"]).drop_duplicates(subset=["date"])
    dates = df["date"].astype(str).tolist()
    if date not in dates:
        misses.append((sym, date, "日期不在")); continue
    i = dates.index(date)
    sig = S.detect_huigui_v5(df)
    win = sig.iloc[max(0, i - 5): i + 1]
    hit = None
    for k in range(len(win) - 1, -1, -1):
        if win.iloc[k]:
            hit = k - 5
            break
    if hit is not None:
        hits.append((sym, date, hit))
    else:
        misses.append((sym, date, "无信号"))

print(f"覆盖率: {len(hits)}/{len(huigui_audited)} = {len(hits)/len(huigui_audited)*100:.0f}%")
k_le0 = sum(1 for h in hits if h[2] <= 0)
k_le1 = sum(1 for h in hits if h[2] >= -1)
print(f"k<=0: {k_le0}/{len(hits)}  k>=-1: {k_le1}/{len(hits)}")
print(f"k 分布: {sorted(h[2] for h in hits)}")
print("\n未命中:")
for m in misses:
    print(f"  {m[0]} {m[1]} {m[2]}")