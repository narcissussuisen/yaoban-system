"""R1' 整改后验证：最近信号 + k 分布 + c3 容差敏感性"""
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data.qfq_store import QFQStore
import core.strategies as S
import importlib
importlib.reload(S)

huigui = {
    "002580": "2026-04-07", "002962": "2026-04-20", "603002": "2026-04-21",
    "603178": "2026-04-21", "002176": "2026-04-24", "605006": "2026-05-07",
    "002015": "2026-05-13", "603316": "2026-05-20", "002156": "2026-05-20",
    "603158": "2026-05-20", "002617": "2026-05-26", "600719": "2026-05-26",
    "000700": "2026-05-26", "000021": "2026-05-26", "600207": "2026-05-27",
    "603693": "2026-05-27", "000733": "2026-06-03", "600667": "2026-06-03",
    "603823": "2026-06-08", "002669": "2026-06-08", "600552": "2026-06-16",
    "000823": "2026-06-17", "600063": "2026-06-17", "002897": "2026-06-18",
    "002407": "2026-06-22", "600360": "2026-06-23", "600584": "2026-06-24",
    "603005": "2026-06-26", "603078": "2026-06-26", "601958": "2026-06-29",
    "002584": "2026-07-02", "600379": "2026-07-06", "002137": "2026-07-09",
    "000948": "2026-07-20", "001206": "2026-07-20", "002379": "2026-07-22",
    "001317": "2026-07-27", "002303": "2026-08-04", "002197": "2026-08-04",
    "603459": "2026-08-06", "603738": "2026-08-17", "603065": "2026-08-18",
}

qfq25 = QFQStore("2025")
qfq26 = QFQStore("2026")

def run(tol):
    """容差敏感性：修改模块内检测器的支撑容差并重跑（通过 monkeypatch 参数不现实——用复制检测逻辑简化：
    直接重新读取函数源码修改容差重载）"""
    # 简化：通过修改源码字符串动态创建变体
    import src.core.strategies as S2
    import inspect
    src = inspect.getsource(S2.detect_huigui_v5)
    var_src = src.replace("<= 0.015", f"<= {tol}")
    ns = {"pd": pd, "ind": S2.ind, "_require": S2._require, "_vol_ma": S2._vol_ma}
    exec(var_src, ns)
    fn = ns["detect_huigui_v5"]
    hits = []
    k_dist = []
    for sym, date in sorted(huigui.items()):
        rows25 = qfq25.get_stock(sym) or []
        rows26 = qfq26.get_stock(sym) or []
        rows = rows25 + rows26
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "amount"]).drop_duplicates(subset=["date"])
        dates = df["date"].astype(str).tolist()
        if date not in dates:
            continue
        i = dates.index(date)
        sig = fn(df)
        win = sig.iloc[max(0, i - 5): i + 1]
        hit = None
        for k in range(len(win) - 1, -1, -1):  # 从最近开始
            if win.iloc[k]:
                hit = k - 5  # 相对买入日的天数（负=前，0=当日）
                break
        if hit is not None:
            hits.append(sym)
            k_dist.append(hit)
    return hits, k_dist

L = ["# R1' 整改验证：最近信号 + k 分布 + c3 容差敏感性", ""]
L.append("| 容差 | 命中数 | 覆盖率 | k≤0 命中 | k 分布 |")
L.append("|---|---|---|---|---|")
for tol in (0.010, 0.015, 0.020, 0.025, 0.030):
    hits, k_dist = run(tol)
    k_le0 = sum(1 for k in k_dist if k <= 0)
    L.append(f"| {tol*100:.1f}% | {len(hits)} | {len(hits)/42*100:.0f}% | {k_le0} | {sorted(k_dist)} |")
L.append("")
L.append("> 注：k=负表示信号在买入日前 |k| 天，k=0 表示买入日当天")
out = Path(__file__).resolve().parent.parent / "outputs" / "r1p_sensitivity.md"
out.write_text(chr(10).join(L), encoding="utf-8")
print(chr(10).join(L))
