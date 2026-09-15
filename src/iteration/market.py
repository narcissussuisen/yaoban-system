"""市场数据适配层：日线读取（只读 F 盘 level2 store）。

口径（与既有 src/core/daily_src.py 一致）：
  - 主源 `daily_rebuilt`（TDX 60 分钟重建，当前最权威，覆盖最新交易日）
  - 兜底 `daily_tencent`（前复权，历史更长但停在 2026-08-28）
  - 单位：volume 为股；amount 为元
"""
from __future__ import annotations

import pathlib
import random

import numpy as np
import pandas as pd

LEVEL2 = pathlib.Path(r"F:/WorkBuddyItem/a股level2")
REBUILT = LEVEL2 / "daily_rebuilt"
TENCENT = LEVEL2 / "daily_tencent"

_CACHE: dict[str, pd.DataFrame | None] = {}


def load_daily(code: str, prefer_rebuilt: bool = True) -> pd.DataFrame | None:
    """返回升序日线 df[date,open,high,low,close,volume,amount]；缺失返回 None。"""
    key = f"{code}:{prefer_rebuilt}"
    if key in _CACHE:
        return _CACHE[key]
    order = [REBUILT, TENCENT] if prefer_rebuilt else [TENCENT, REBUILT]
    df = None
    for d in order:
        fp = d / f"{code}.parquet"
        if not fp.exists():
            continue
        try:
            t = pd.read_parquet(fp)
            if not len(t):
                continue
            t["date"] = t["date"].astype(str)
            for c in ("open", "high", "low", "close", "volume", "amount"):
                if c in t.columns:
                    t[c] = pd.to_numeric(t[c], errors="coerce")
            t = t.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
            df = t
            break
        except Exception:  # noqa: BLE001 - 单文件损坏不阻塞批次
            continue
    _CACHE[key] = df
    return df


def available_codes(limit: int | None = None, seed: int = 20260911) -> list[str]:
    """可用标的清单。limit 给定时按固定种子抽样（保证批次可复现）。"""
    codes = sorted(p.stem for p in REBUILT.glob("*.parquet") if p.stem.isdigit())
    if limit and limit < len(codes):
        rnd = random.Random(seed)
        codes = sorted(rnd.sample(codes, limit))
    return codes


def history_span(codes: list[str]) -> dict:
    """统计日期覆盖（用于批次自检：数据是否推进到上一交易日）。"""
    lo, hi, rows = None, None, []
    for c in codes:
        df = load_daily(c)
        if df is None or not len(df):
            continue
        a, b = str(df["date"].iloc[0]), str(df["date"].iloc[-1])
        lo = a if lo is None or a < lo else lo
        hi = b if hi is None or b > hi else hi
        rows.append(len(df))
    n = len(rows)
    return {
        "codes_ok": n,
        "start_min": lo,
        "end_max": hi,
        "rows_median": int(np.median(rows)) if n else 0,
        "rows_min": int(min(rows)) if n else 0,
    }


def forward_metrics(opens, highs, closes, i: int, horizon: int,
                    stop_pct: float | None = 5.0) -> dict | None:
    """自第 i 根（信号日收盘）之后 horizon 根的开盘买入，前向收益口径。

    entry = open[i+1]；若期内某日收盘 <= entry*(1-stop/100) 则按该收盘止损先出。
    返回 {ret_pct, hwm_pct, stopped, days}；数据不足返回 None。
    """
    n = len(closes)
    if i + 1 >= n:
        return None
    entry = float(opens[i + 1])
    if not np.isfinite(entry) or entry <= 0:
        return None
    end = min(i + 1 + horizon, n)
    hwm = entry
    stopped = False
    exit_px = float(closes[end - 1])
    for j in range(i + 1, end):
        hwm = max(hwm, float(highs[j]))
        if stop_pct is not None and float(closes[j]) <= entry * (1 - stop_pct / 100.0):
            exit_px = float(closes[j])
            stopped = True
            end = j + 1
            break
    return {
        "ret_pct": (exit_px / entry - 1.0) * 100.0,
        "hwm_pct": (hwm / entry - 1.0) * 100.0,
        "stopped": stopped,
        "days": end - i - 1,
    }


def agg_metrics(rows: list[dict]) -> dict:
    """把逐事件前向收益聚合为规则容量指标。"""
    if not rows:
        return {"n": 0}
    r = np.array([x["ret_pct"] for x in rows], dtype=float)
    h = np.array([x["hwm_pct"] for x in rows], dtype=float)
    return {
        "n": int(len(r)),
        "mean_ret_pct": round(float(r.mean()), 3),
        "median_ret_pct": round(float(np.median(r)), 3),
        "win_rate_pct": round(float((r > 0).mean() * 100.0), 1),
        "hwm_ge3_pct": round(float((h >= 3.0).mean() * 100.0), 1),
        "stop_rate_pct": round(float(np.mean([x["stopped"] for x in rows]) * 100.0), 1),
        "mean_hwm_pct": round(float(h.mean()), 3),
        "p10_ret_pct": round(float(np.percentile(r, 10)), 3),
    }
