# -*- coding: utf-8 -*-
"""R3.1 ④⑤⑥ 段共用的**纯函数**（与 `engine.py` 分离，便于单测）。

## 关键复用（不重写）
- `src/iteration/intraday.py:75` **`first_break_below_vwap(...)`** —— 「第一波回落跌破均价线」的
  **既有完整实现**，且 `FROZEN_VWAP_PARAMS`（L183）是**已冻结标定的口径**（`09:35~10:00 / confirm 5min / tol 0.003`）
  → ④ 段**直接调用它**，不自研、不改参数。
- `src/iteration/intraday.py:60` `intraday_vwap(day)` —— 当日累计 VWAP（amount 缺失时退回典型价加权）
  ⚠️ 与 `src/core/intraday.py::vwap_series`（`volume.clip` 近似）**口径不同** → 本模块**只用 iteration 那套**，
  payload 里显式声明，避免两套口径混用。

## 为什么单独一层
④⑤⑥ 都依赖「取某标的某日的分钟序列 / VWAP / 分时特征」这类**无 IO 副作用的计算**。
把它们抽出来，`engine.py` 只做编排与 digest 组装，单测可以对纯函数直接下断言。
"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

BASE = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(BASE / "src"), str(BASE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data.minute_paths import parquet_path  # noqa: E402

# 冻结口径（来自 src/iteration/intraday.py:183，**不自行改动**）
from iteration.intraday import FROZEN_VWAP_PARAMS, first_break_below_vwap, intraday_vwap  # noqa: E402

VWAP_SOURCE = "src/iteration/intraday.py::intraday_vwap（amount 缺失退回典型价加权）"
VWAP_WHY = ("弃用 `core/intraday.py::vwap_series`（volume.clip 近似）—— 两套口径不同，"
            "混用会让同一条规则在不同模块给出不同结论。本链**只用 iteration 那套**。")
VWAP_PARAMS_FROZEN = dict(FROZEN_VWAP_PARAMS)


def load_day_minute(code: str, day: str):
    """取某标的**某日**的 1m 分钟序列（R2.4 落盘的 `data/minute/1m/<code>.parquet`）。

    返回 DataFrame（列 ts/open/high/low/close/volume/amount）；无文件、无该日数据、或行数 <5 时返回 None。
    """
    import pandas as pd
    fp = parquet_path(code)
    if not fp.exists():
        return None
    try:
        df = pd.read_parquet(fp)
    except Exception:
        return None
    if df is None or df.empty or "ts" not in df.columns:
        return None
    d = df["ts"].astype(str).str.slice(0, 10)
    sub = df[d == day]
    if len(sub) < 5:
        return None
    return sub.reset_index(drop=True)


def vwap_facts(day_df) -> dict:
    """VWAP 相关**事实**（不是否决票）。

    返回 {break_result, vwap_last, close_last, above_vwap, slope_pct, series}
    - `break_result` = `first_break_below_vwap(...)` 的原始返回（含 veto / break_ts / below_minutes / window_minutes / reason）
    - `above_vwap` = 收盘价是否在均价线上（`GEN-ENTRY-04` 的 `px < vwap` 事实，带 tol 容差）
    """
    if day_df is None or day_df.empty:
        return {}
    v = intraday_vwap(day_df)
    last_close = float(day_df["close"].iloc[-1])
    last_vwap = float(v[-1]) if len(v) and np.isfinite(v[-1]) else float("nan")
    tol = float(VWAP_PARAMS_FROZEN.get("tol", 0.0))
    above = bool(np.isfinite(last_vwap) and last_close >= last_vwap * (1 - tol))
    # 均价线斜率：窗口末段相对窗口初段的变化率
    slope = None
    fin = [x for x in v if np.isfinite(x)]
    if len(fin) >= 10:
        a, b = float(fin[-10]), float(fin[-1])
        if a:
            slope = round((b / a - 1) * 100, 4)
    res = {}
    try:
        res = first_break_below_vwap(day_df, **VWAP_PARAMS_FROZEN) or {}
    except Exception as e:  # noqa: BLE001
        res = {"error": f"{type(e).__name__}: {e}"}
    return dict(break_result=res, vwap_last=last_vwap, close_last=last_close,
                above_vwap=above, slope_pct=slope, series=v)


def d6_features(day_df, prev_day_df=None) -> dict:
    """`D6` 的 **5 项 inputs**（`persona/discretion_v0.toml:81`）—— 只算特征，**不定档**。

    档位（A_optimum / B_medium / C_reject）的判定属 D6 → **R3.2 由 LLM 承担**。
    """
    if day_df is None or day_df.empty:
        return {}
    f = vwap_facts(day_df)
    v = f.get("series")
    close = day_df["close"].to_numpy(float)
    vol = day_df["volume"].to_numpy(float)
    # ① 白线(价) vs 黄线(VWAP) 的相对位置与斜率
    pos_ratio = None
    if np.isfinite(f.get("vwap_last", np.nan)) and f["vwap_last"]:
        pos_ratio = round(f["close_last"] / f["vwap_last"] - 1, 5)
    # ② 上行段量能 / 回踩段量能 之比
    up_vol = down_vol = 0.0
    if len(close) >= 2:
        d = np.diff(close)
        for k in range(1, len(close)):
            if d[k - 1] > 0:
                up_vol += vol[k]
            elif d[k - 1] < 0:
                down_vol += vol[k]
    vol_ratio = round(up_vol / down_vol, 3) if down_vol > 0 else None
    # ③ 今日低点 vs 昨日低点（重心是否上移）
    low_today = float(day_df["low"].min())
    low_prev = float(prev_day_df["low"].min()) if (prev_day_df is not None and not prev_day_df.empty) else None
    low_shift = round(low_today / low_prev - 1, 5) if low_prev else None
    # ④ 日内振幅、回落幅度 vs 均价线
    amp = round((float(day_df["high"].max()) / low_today - 1) * 100, 3) if low_today else None
    # ⚠️⚠️ 语义纠错（2026-09-12，由 R3.3 双轨 shadow 暴露）：
    #   原字段名 `drawdown_vs_vwap_pct` 极易被读成「第一波回落跌破均价线」——
    #   但它是**全日最低点**相对均价线的跌幅，是**极值**，不是**时序**判据。
    #   任何振幅大的强势票盘中都可能瞬时刺破均价线，**这不等于「第一波回落跌破」**。
    #   实测后果：9 只选手明确买入（含 9/10 山东玻纤「①最优档」、9/09 海通发展「当日涨停+9.98%」）
    #   被 LLM 依据该字段误判为 `C_reject`，且出现 `score=0.83` 却选 `C_reject` 的自相矛盾。
    #   → 改名为 `intraday_low_vs_vwap_pct` 明示「全日最低」；「第一波」的事实**只由**
    #     `first_break_below_vwap` 提供（`break_result`：break_ts / below_minutes / reason）。
    low_vs_vwap = None
    if np.isfinite(f.get("vwap_last", np.nan)) and f["vwap_last"]:
        low_vs_vwap = round((low_today / f["vwap_last"] - 1) * 100, 3)
    # ⑤（加分）尾盘是否微翘不破均线
    tail_up = tail_above = None
    if len(close) >= 6:
        tail_up = bool(close[-1] > close[-6])
        if v is not None and len(v) >= 6 and np.isfinite(v[-1]):
            tail_above = bool(all(np.isfinite(v[-6:])) and close[-1] >= float(v[-1]) * (1 - 0.003))
    return dict(
        px_vs_vwap_pct=pos_ratio,                  # ①
        vwap_slope_pct=f.get("slope_pct"),
        up_down_vol_ratio=vol_ratio,               # ②
        low_shift_pct=low_shift,                   # ③
        amplitude_pct=amp,                         # ④ 日内振幅
        # ④ 全日最低 vs 均价线（⚠️ 极值，**不是**「第一波回落」）
        intraday_low_vs_vwap_pct=low_vs_vwap,
        # ⭐ 「第一波回落跌破均价线」的**权威事实**：只取 `first_break_below_vwap` 的结果
        first_break=(f.get("break_result") or {}).get("veto"),
        first_break_ts=(f.get("break_result") or {}).get("break_ts"),
        first_break_below_minutes=(f.get("break_result") or {}).get("below_minutes"),
        first_break_reason=(f.get("break_result") or {}).get("reason"),
        tail_up=tail_up, tail_above_vwap=tail_above,        # ⑤
        n_bars=int(len(day_df)),
    )


def break_below_vwap(code_facts: dict) -> bool:
    """`GEN-ENTRY-04` 的**事实**判定：是否出现「第一波回落跌破均价线」。

    ⚠️ 返回的是**事实**，**不是否决票** —— 否决权归 `D6`（见 engine.seg_entry 的 `veto_owner`）。
    """
    r = (code_facts or {}).get("break_result") or {}
    return bool(r.get("veto"))


def stop_distance_pct(entry_px: float | None, stop_px: float | None, vwap: float | None) -> dict:
    """止损距离（%）。用于 `GEN-SIZE-04` 反推单笔可承受仓位。

    优先用真止损位；没有则用 VWAP 作**占位**并**显式标注**（不假装是真止损）。
    """
    if not entry_px:
        return dict(pct=None, basis="unavailable")
    if stop_px and 0 < stop_px < entry_px:
        return dict(pct=round((entry_px - stop_px) / entry_px, 5), basis="real_stop")
    if vwap and 0 < vwap < entry_px:
        return dict(pct=round((entry_px - vwap) / entry_px, 5),
                    basis="vwap_placeholder", note="⚠️ 无真止损位，用 VWAP 占位（非选手证据）")
    return dict(pct=None, basis="unavailable")
