"""技术指标（pandas 实现，口径对齐 v4.0 手册）

- ma(series, n)           简单均线
- multi_head(df)          MA5>MA10>MA20>MA60 多头排列
- vol_shrink_ratio(df,n)  近 n 日均量 / 更早 n 日均量（<0.7 即缩量 30%+）
- upper_shadow(df)        上影线指标（仙人指路用）
"""
from __future__ import annotations

import pandas as pd


def ma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def multi_head(df: pd.DataFrame, periods=(5, 10, 20, 60)) -> pd.Series:
    """均线多头排列：ma5 > ma10 > ma20 > ma60"""
    cols = [ma(df["close"], p) for p in periods]
    out = cols[0] > cols[1]
    for i in range(1, len(cols) - 1):
        out &= cols[i] > cols[i + 1]
    return out.fillna(False)


def vol_shrink_ratio(df: pd.DataFrame, n: int = 5) -> pd.Series:
    """当前 n 日均量 / 前 n 日均量（1.0 = 持平；0.7 = 缩量 30%）"""
    v5 = df["volume"].rolling(n).mean()
    v5_prev = df["volume"].shift(n).rolling(n).mean()
    return v5 / v5_prev


def upper_shadow(df: pd.DataFrame) -> pd.Series:
    """上影线长度"""
    return df["high"] - pd.concat([df["close"], df["open"]], axis=1).max(axis=1)


def lower_shadow(df: pd.DataFrame) -> pd.Series:
    return pd.concat([df["close"], df["open"]], axis=1).min(axis=1) - df["low"]


def body(df: pd.DataFrame) -> pd.Series:
    return (df["close"] - df["open"]).abs()


def is_bearish(df: pd.DataFrame) -> pd.Series:
    return df["close"] < df["open"]


def is_bullish(df: pd.DataFrame) -> pd.Series:
    return df["close"] > df["open"]


def limit_up_mask(df: pd.DataFrame, pct_threshold: float = 9.8,
                  symbol: str | None = None) -> pd.Series:
    """涨停判定。

    ⚠️ 2026-09-14 修正（此前**注释与实现不符**：docstring 自称「兼容 10% 与 ST 5%」，
       但实现只有一个 9.8% 阈值 ⇒ **20cm 股的 +10% 被误判为涨停**，污染 `zt_huicai`）。

    口径：
      - `symbol` 给定 ⇒ 按**板块**判定，阈值 = `core.sell.limit_pct_of(symbol) - 0.002`
        （0.2pp 容差，与 `plan_daily.py:163` 同源）。依据 `core/sell.py:79-84`：
        300/301/688/689 → 20%；4/8/92（北交所）→ 30%；其余 → 10%。
        ⇒ 主板结果与旧 9.8% **完全等价**（0.10 − 0.002 = 0.098），
          仅 20cm/30cm 抬高阈值 ⇒ 涨停标记**只减不增**（消假阳性，不影响真涨停）。
      - `symbol` 为空 ⇒ 走旧 `pct_threshold`（默认 9.8%）分支，**向后兼容**
        （7 个研究脚本与 `strategies.detect()` 统一入口仍按单参调用）。
      - ⚠️ 已知限制：`limit_pct_of` **无 ST 5% 分支** ⇒ ST 涨停仍识别不出；
        ST 已在池层被剔（`core/pattern_pool.py:149`），此处不为它引入新分支
        （该限制已由 `tests/test_limit_up_mask.py` 的 `test_st_no_branch_documented_limitation` 钉住）。
    """
    ret = df["close"].pct_change()
    if symbol:
        from core.sell import limit_pct_of   # 局部 import：避免模块级导入顺序耦合
        return ret >= (limit_pct_of(symbol) - 0.002)
    return ret >= pct_threshold / 100.0

def kdj(df: pd.DataFrame, n: int = 9, k_period: int = 3, d_period: int = 3) -> pd.DataFrame:
    """KDJ 指标（选手参数 9,3,3，v48 截图 K19.2 确认）
    返回 DataFrame: k / d / j 列
    """
    low_n = df["low"].rolling(n, min_periods=1).min()
    high_n = df["high"].rolling(n, min_periods=1).max()
    rsv = (df["close"] - low_n) / (high_n - low_n).replace(0, float("nan")) * 100
    rsv = rsv.fillna(50.0)
    k = rsv.ewm(com=k_period - 1, adjust=False).mean()
    d = k.ewm(com=d_period - 1, adjust=False).mean()
    j = 3 * k - 2 * d
    return pd.DataFrame({"k": k, "d": d, "j": j}, index=df.index)


def bias_ma5(df: pd.DataFrame) -> pd.Series:
    """乖离率（相对5日线，%）：(close-ma5)/ma5*100"""
    ma5 = ma(df["close"], 5)
    return (df["close"] - ma5) / ma5 * 100
