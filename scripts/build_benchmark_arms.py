"""R0.7 双市场基准臂（口径：**基准净值序列**，非模拟被动账户 —— 2026-09-12 用户裁定）。

为什么是"净值序列"而不是"被动账户"
------------------------------------
模拟账户要引入费用、T+1、成分与调仓口径，而基准臂的用途只是**剥离 β**：
回答"EvoAlpha 的收益是不是随便买点大盘就有"。指数日收益累乘即可回答，
不必再引入一层可能有 bug 的记账。

两条臂
------
- ``all_a_equal``      全 A 等权 —— 全市场日线**逐日等权平均收益链**（自建）
- ``small_cap_2000``   小盘 2000 —— **国证2000 (sz399303)** 指数日收益链
  ⚠️ 计划原写"中证2000"，但它在本项目可用源（akshare 走新浪）**取不到**
     （``sh932000`` / ``sz932000`` 均 ``KeyError: 'date'``，2026-09-12 实测）。
     国证2000 同为"2000 只小盘"、风格最接近，故替代；``--replace-arm`` 可换。

净值归一化
----------
每条臂把 ``nav`` 归一化到 **两臂共同可得的首个交易日 = 1.0000**，并记录
``series_start``。**比较层必须再从 ``max(series_start, 主账本 start_date)`` 重新起算**
（用 ``rebase()``），否则拿本臂的历史段去比主账本的前向段会得出无意义的结论。

产物
----
``portfolio/benchmarks/arms.json``（原子写）::

    {"generated_at","series_start","ledger_start_date",
     "arms": {"<id>": {"label","basis","source","n","start","end",
                       "points":[{"date","nav","ret"}]}}}

用法::

    python scripts/build_benchmark_arms.py                 # 2026 全年（默认）
    python scripts/build_benchmark_arms.py --year 2026 --workers 6
    python scripts/build_benchmark_arms.py --index-arm sz399303
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from datetime import datetime

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))

import pandas as pd  # noqa: E402

from data.qfq_store import QFQStore, build_daily_map  # noqa: E402

OUT_PATH = BASE / "portfolio" / "benchmarks" / "arms.json"
LEDGER = BASE / "portfolio" / "ledger.json"

# 与 board_analysis.py:34 同口径：排除指数/ETF/B 股等非个股代码段
_EXCLUDE_PREFIX = ("399", "5", "15", "16")
DEFAULT_INDEX_ARM = "sz399303"   # 国证2000


def _atomic_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _nav_points(dates: list[str], rets: list[float], start_nav: float = 1.0) -> list[dict]:
    """把日收益链累乘成净值序列。"""
    out, nav = [], start_nav
    for d, r in zip(dates, rets):
        nav *= (1.0 + float(r))
        out.append({"date": d, "nav": round(nav, 6), "ret": round(float(r), 6)})
    return out


def build_all_a_equal(year: str, workers: int) -> dict:
    """全 A 等权：全市场日线逐日等权平均收益。

    🔴 只统计**相邻两日都有行情**的个股 —— 新股上市首日、停牌首日只在单边出现，
    计进去等于凭空塞进一个 ±10% 的样本（量级足以扭曲 5000 只的均值）。
    """
    qfq = QFQStore(year)
    symbols = [s for s in qfq.symbols() if not s.startswith(_EXCLUDE_PREFIX)]
    print(f"[all_a_equal] 待聚合个股 {len(symbols)}", flush=True)
    daily = build_daily_map(year, symbols, workers=workers)
    daily = {k: v for k, v in daily.items() if v}
    print(f"[all_a_equal] 日线就绪 {len(daily)}", flush=True)

    # 逐股构造 {date: close}，再按日汇总
    per_date_sum: dict[str, float] = {}
    per_date_cnt: dict[str, int] = {}
    for _sym, rows in daily.items():
        # rows: [symbol, date, open, high, low, close, volume, amount]
        prev_c = None
        for r in rows:
            try:
                ts = str(r[1])[:10]
                c = float(r[5])
            except (IndexError, TypeError, ValueError):
                continue
            if not (c > 0):
                prev_c = None
                continue
            if prev_c is not None:
                ret = c / prev_c - 1.0
                # 单日 ±11% 以上视为除权/异常，剔除（等权均值极易被单点带偏）
                if -0.11 < ret < 0.11:
                    per_date_sum[ts] = per_date_sum.get(ts, 0.0) + ret
                    per_date_cnt[ts] = per_date_cnt.get(ts, 0) + 1
            prev_c = c

    dates = sorted(d for d in per_date_sum if per_date_cnt.get(d, 0) >= 100)
    rets = [per_date_sum[d] / per_date_cnt[d] for d in dates]
    return {
        "label": "全 A 等权",
        "basis": "全市场日线逐日等权平均收益链（自建）",
        "source": f"QFQStore({year}) 全市场日线 / build_daily_map",
        "n_symbols": len(daily),
        "points": _nav_points(dates, rets),
    }


def build_index_arm(symbol: str) -> dict:
    """指数臂：日收益链（净值序列），取自项目既有 fetcher。"""
    from data.fetchers import fetch_index_daily

    df = fetch_index_daily(symbol)
    if df is None or df.empty:
        raise RuntimeError(f"指数 {symbol} 取不到数据（fetcher 返回空）")
    df = df.sort_values("date")
    close = df["close"].astype(float)
    ret = close.pct_change()
    rows = list(zip(df["date"].astype(str).str[:10], ret))
    dates = [d for d, r in rows if r == r and r is not None]  # 去 NaN
    rets = [float(r) for _d, r in rows if r == r and r is not None]
    return {
        "label": f"小盘2000（{symbol}）",
        "basis": "指数日收益链",
        "source": f"data.fetchers.fetch_index_daily('{symbol}') via akshare/新浪",
        "points": _nav_points(dates, rets),
    }


def rebase(points: list[dict], start_date: str) -> list[dict]:
    """把净值序列**重基**到 start_date（该日 nav=1.0）。比较层必须用它。

    主账本从 start_date 起算，基准臂若带着更早的历史段直接比，等于拿不同区间相减。
    """
    idx = next((i for i, p in enumerate(points) if p["date"] >= start_date), None)
    if idx is None:
        return []
    base = points[idx]["nav"] or 1.0
    return [{"date": p["date"], "nav": round(p["nav"] / base, 6), "ret": p["ret"]}
            for p in points[idx:]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default="2026")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--index-arm", default=DEFAULT_INDEX_ARM)
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    t0 = time.time()
    ledger_start = None
    try:
        ledger_start = json.loads(LEDGER.read_text(encoding="utf-8")).get("start_date")
    except Exception:
        pass

    arms: dict[str, dict] = {}
    arms["all_a_equal"] = build_all_a_equal(args.year, args.workers)
    print(f"[{time.time()-t0:.0f}s] all_a_equal 完成 {len(arms['all_a_equal']['points'])} 点", flush=True)

    try:
        arms["small_cap_2000"] = build_index_arm(args.index_arm)
        print(f"[{time.time()-t0:.0f}s] small_cap_2000 完成 "
              f"{len(arms['small_cap_2000']['points'])} 点", flush=True)
    except Exception as exc:
        # 指数臂失败不能把已算好的全A臂一起丢掉 —— 记下来，人看得见
        arms["small_cap_2000"] = {"label": f"小盘2000（{args.index_arm}）", "basis": "指数日收益链",
                                  "source": f"fetch_index_daily('{args.index_arm}')",
                                  "error": f"{type(exc).__name__}: {exc}", "points": []}
        print(f"[WARN] 指数臂失败: {exc}", file=sys.stderr, flush=True)

    # 共同起点 = 两臂都有数据的最早日期
    starts = [a["points"][0]["date"] for a in arms.values() if a["points"]]
    ends = [a["points"][-1]["date"] for a in arms.values() if a["points"]]
    common_start = max(starts) if starts else None
    common_end = min(ends) if ends else None

    doc = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "basis_note": "基准净值序列（非模拟账户）：指数/等权日收益累乘，起点 nav=1.0000",
        "series_start": common_start,
        "series_end": common_end,
        "ledger_start_date": ledger_start,
        "compare_from": max([d for d in (common_start, ledger_start) if d], default=None),
        "arms": arms,
    }
    out = pathlib.Path(args.out)
    _atomic_json(out, doc)

    print(json.dumps({
        "out": str(out),
        "series": f"{common_start} → {common_end}",
        "ledger_start_date": ledger_start,
        "compare_from": doc["compare_from"],
        "arms": {k: {"n": len(v["points"]),
                     "nav_end": v["points"][-1]["nav"] if v["points"] else None,
                     "error": v.get("error")}
                 for k, v in arms.items()},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
