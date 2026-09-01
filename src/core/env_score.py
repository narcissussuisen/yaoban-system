"""市场环境打分（v4.0 手册 §1.1，五维各 0~2 分，总分 0~10）

维度:
  trend    指数趋势: 站上20日线且20日线向上=2 | 站上20日线=1 | 跌破=0
  volume   成交额:   较20日均量 +10%=2 | ±10%内=1 | -10%=0
  limit_up 涨停家数: ≥100=2 | 60~99=1 | <60=0
  height   连板高度: ≥5板=2 | 3~4板=1 | ≤2板=0
  breadth  赚钱效应: 红盘占比≥60%=2 | 40~60%=1 | <40%=0

仓位映射（v3.1/v4.0）:
  ≥8 强势 50~70% | 4~7 中性 30~50% | ≤3 弱势 ≤20%

数据不足的维度自动跳过（返回 available_dims），简版 = 仅 trend+volume，
已验证方向有效（v4_verification_report.md：简版分 0→4，后3日收益 -0.12%→+0.71%）。
"""
from __future__ import annotations

import pandas as pd

from config import section

ENV = section("env")
RISK = section("risk")
POSITION_CAPS = RISK.get("regime_position_cap", {"strong": 70.0, "neutral": 50.0, "weak": 20.0})


def _dim_trend(df: pd.DataFrame) -> int:
    if df is None or len(df) < 25:
        return 0
    c = df["close"]
    ma20 = c.rolling(20).mean()
    last, last_ma = c.iloc[-1], ma20.iloc[-1]
    if last > last_ma:
        return 2 if ma20.iloc[-1] > ma20.iloc[-2] else 1
    return 0


def _dim_volume(df: pd.DataFrame) -> int:
    if df is None or len(df) < 25:
        return 0
    vol = df["volume"] if "volume" in df.columns else df["amount"]
    v20 = vol.rolling(20).mean()
    if v20.iloc[-1] <= 0:
        return 0
    ratio = vol.iloc[-1] / v20.iloc[-1]
    if ratio >= 1 + ENV["volume"]["expand_pct"]:
        return 2
    if ratio >= 1 - ENV["volume"]["shrink_pct"]:
        return 1
    return 0


def _dim_limit_up(limit_up_count: int | None) -> int:
    if limit_up_count is None:
        return 0
    if limit_up_count >= ENV["limit_up"]["strong_ge"]:
        return 2
    if limit_up_count >= ENV["limit_up"]["mid_ge"]:
        return 1
    return 0


def _dim_height(max_board_height: int | None) -> int:
    if max_board_height is None:
        return 0
    if max_board_height >= ENV["height"]["strong_ge"]:
        return 2
    if max_board_height >= ENV["height"]["mid_ge"]:
        return 1
    return 0


def _dim_breadth(red_pct: float | None) -> int:
    if red_pct is None:
        return 0
    if red_pct >= ENV["breadth"]["strong_pct"]:
        return 2
    if red_pct >= ENV["breadth"]["mid_pct"]:
        return 1
    return 0


def env_score(index_df: pd.DataFrame | None = None,
              limit_up_count: int | None = None,
              max_board_height: int | None = None,
              red_pct: float | None = None) -> dict:
    """计算环境分。返回 {score, dims, available, regime, position_cap}

    说明：缺失维度计 0 分并列入 unavailable；regime 依据总分会偏保守（数据不全时降档），
    完整五维需涨停池/活跃度数据积累（见 ROADMAP P1）。
    """
    dims = {
        "trend": _dim_trend(index_df),
        "volume": _dim_volume(index_df),
        "limit_up": _dim_limit_up(limit_up_count),
        "height": _dim_height(max_board_height),
        "breadth": _dim_breadth(red_pct),
    }
    available = [k for k, v in dims.items() if v is not None]
    # 标记真正有数据的维度（缺失时 dims 值为 0，与"弱"同值，需显式区分）
    has = {"trend": index_df is not None and len(index_df) >= 25,
           "volume": index_df is not None and len(index_df) >= 25,
           "limit_up": limit_up_count is not None,
           "height": max_board_height is not None,
           "breadth": red_pct is not None}
    avail = [k for k in dims if has[k]]
    score = sum(dims.values())
    if score >= ENV["regime_strong_ge"]:
        regime, cap = "strong", POSITION_CAPS["strong"]
    elif score >= ENV["regime_neutral_ge"]:
        regime, cap = "neutral", POSITION_CAPS["neutral"]
    else:
        regime, cap = "weak", POSITION_CAPS["weak"]
    return {
        "score": score,
        "dims": dims,
        "available_dims": avail,
        "regime": regime,
        "position_cap": cap,
    }


def env_score_full(index_df: pd.DataFrame | None,
                   sentiment: dict | None = None,
                   red_pct: float | None = None) -> dict:
    """完整五维打分（情绪数据就绪时使用，a-stock-data market_sentiment）

    在 env_score 基础上补齐:
      limit_up  涨停家数: zt_count ≥100=2 | 60~99=1 | <60=0（手册 §6.3 分档校准）
      height    连板高度: max_height ≥5=2 | 3~4=1 | ≤2=0（手册「5连板打开高度」v63）
      breadth   赚钱效应: zt/(zt+dt) ≥0.8=2 | ≥0.6=1 | <0.6=0（设计代理；红盘占比可用时优先）
    sentiment 缺省时退化为 env_score 简版口径。
    """
    base = env_score(index_df, limit_up_count=None, max_board_height=None, red_pct=red_pct)
    if not sentiment:
        return base
    zt = int(sentiment.get("zt_count", 0) or 0)
    dt = int(sentiment.get("dt_count", 0) or 0)
    height = int(sentiment.get("max_height", 0) or 0)
    base["dims"]["limit_up"] = _dim_limit_up(zt)
    base["dims"]["height"] = _dim_height(height)
    if red_pct is not None:
        base["dims"]["breadth"] = _dim_breadth(red_pct)
    else:
        ratio = zt / (zt + dt) if (zt + dt) else 0.0
        base["dims"]["breadth"] = 2 if ratio >= 0.8 else (1 if ratio >= 0.6 else 0)
    base["available_dims"] = ["trend", "volume", "limit_up", "height", "breadth"]
    base["score"] = sum(base["dims"].values())
    if base["score"] >= ENV["regime_strong_ge"]:
        base["regime"], base["position_cap"] = "strong", POSITION_CAPS["strong"]
    elif base["score"] >= ENV["regime_neutral_ge"]:
        base["regime"], base["position_cap"] = "neutral", POSITION_CAPS["neutral"]
    else:
        base["regime"], base["position_cap"] = "weak", POSITION_CAPS["weak"]
    return base


def regime_from_simple(simple_score: int) -> tuple[str, float]:
    """简版（trend+volume，0~4）专属映射（过渡口径，待涨停池数据后切换完整五维）：
    ≥4 强势 70% | 2~3 中性 50% | ≤1 弱势 20%
    依据：v4_verification_report 简版分 4 → 后3日 +0.71%（强势），0~1 → 负收益（弱势）。"""
    if simple_score >= 4:
        return "strong", POSITION_CAPS["strong"]
    if simple_score >= 2:
        return "neutral", POSITION_CAPS["neutral"]
    return "weak", POSITION_CAPS["weak"]


def simple_score(index_df: pd.DataFrame) -> dict:
    """简版打分（trend+volume，0~4 分）——数据积累期的过渡口径，已验证方向有效"""
    s = env_score(index_df=index_df)
    s["score_simple"] = s["dims"]["trend"] + s["dims"]["volume"]
    return s


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    from data.store import Store

    st = Store()
    rows = st.get_index("sh000001")
    print("index rows in db:", len(rows))
    if rows:
        df = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume"])
        r = env_score(df, limit_up_count=None)
        print("env:", r)
    st.close()
