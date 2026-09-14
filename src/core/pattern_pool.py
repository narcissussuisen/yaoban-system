"""战法池（形态筛选层）—— 补齐 `docs/TRADER_ROADMAP_v2.md §1.2` 缺失的第三层。

【为什么有这个模块】
ROADMAP §1.2 约定的分层是：
    全市场 → 异动池(30-80) → **选手模式粗筛（形态+板块热度+连板气质）** → 确认队列(5-15)
           → 1m 分时三引擎 → 建仓
但生产 `scan_and_confirm.py` 里 **第三层实际不存在**：它把「异动池」直接当成了「确认队列」
（`pool.sort(key=lambda x: -x['chg'])` 后取前 8）。后果是选股层根本不是选手的战法，
而是「全市场涨幅前 8」—— 与选手的「上升回档回踩低吸」在**同一维度上方向相反**
（追高 vs 低吸），所以两边候选池**交集恒为空**（2026-09-14 实测：EvoAlpha top8 为
+9.29%~+7.10%，选手实买溢价 +2.61%）。

【设计原则】
1. **不重写检测逻辑**：直接复用 `core.strategies` 里已经过案例回归的 4 个检测器
   （`detect_huigui(mode='live')` / `detect_zt_huicai` / `detect_xianren` / `detect_fanbao`），
   参数一律来自 `config/parameters.toml`（单一事实源），本模块**不硬编码任何阈值**。
2. **日线级、T-1 收盘确定、全日不变** ⇒ 每个交易日只需构建一次，落盘缓存；
   盘中 scan 只读，不做重算（全市场 5000 只跑日线检测不可能每分钟重算）。
3. **信息不丢失**：返回每只票的 `pattern` / `sig_date` / `bars_since_sig` / 关键判据，
   供 scan 写入 `confirm_*.json` 与决策链留痕（本仓纪律：不得静默丢弃）。

【与 `src/decision_chain/engine.py:19` 的关系】
该处自述「③ 选真龙 | ⏳ stub | 复用 `plan_daily.detect_huigui_v5` 属后续增量
（**形态筛已有实现，需适配器**）」—— 本模块就是那个适配器。
"""
from __future__ import annotations

import pandas as pd

from core import strategies as S

# 战法名 → 检测器（全部来自 core.strategies；参数在其内部从 parameters.toml 读取）
DETECTORS = {
    "huigui": lambda df: S.detect_huigui(df, mode="live"),
    "zt_huicai": S.detect_zt_huicai,
    "xianren": lambda df: S.detect_xianren(df, with_confirm=True),
    "qu_shi_fanbao": S.detect_fanbao,
}
PATTERN_CN = S.STRATEGY_NAMES

# 检测器要求的最少日线根数（`strategies._require(df, 70/30/…)` 里最大是 70）
MIN_BARS = 76


def build_pattern_pool(dmap: dict, asof: str, lookback: int = 4,
                       patterns: tuple = ("huigui", "zt_huicai", "xianren", "qu_shi_fanbao"),
                       ) -> tuple[list[dict], dict]:
    """构建战法池。

    dmap      {sym: 日线 DataFrame(date/open/high/low/close/volume)}，date 升序
    asof      **信号窗口的最新交易日**（= T-1；调用方必须保证不含 T 日数据，否则即为前视）
    lookback  信号日回溯窗口（含 asof）。默认 4 —— 与 `plan_daily.py` 的「信号日∈最近4交易日」一致
    patterns  启用哪些战法（默认并集；单战法可传单元素元组做消融）

    返回 (pool, stats)：
      pool  [{'sym','pattern','pattern_cn','sig_date','bars_since_sig','close_asof','patterns'}]，按信号日降序
      stats {'n_syms_scanned','n_skipped_short','n_pool','by_pattern','asof','lookback','window'}
    """
    if not asof:
        raise ValueError("asof 不可为空（必须显式给出 T-1，避免隐式前视）")
    # 信号窗口 = asof 往前数 lookback 个**交易日**（下面按实际出现过的日期切片）
    stats = {"asof": asof, "lookback": lookback, "n_syms_scanned": 0,
             "n_skipped_short": 0, "n_pool": 0, "by_pattern": {}}
    out: list[dict] = []
    for sym, df in dmap.items():
        if df is None or not len(df):
            continue
        stats["n_syms_scanned"] += 1
        d = df[df["date"].astype(str) <= asof]
        if len(d) < MIN_BARS:
            stats["n_skipped_short"] += 1
            continue
        d = d.reset_index(drop=True)
        dates = d["date"].astype(str).tolist()
        # 信号窗口 = dates 里 <= asof 的最后 lookback 个交易日
        window = set(dates[-lookback:])
        hit_patterns, sig_dates = [], []
        for pname in patterns:
            fn = DETECTORS.get(pname)
            if fn is None:
                continue
            try:
                mask = fn(d)
            except Exception:
                # 单只票的检测异常不得影响整池（本仓纪律：失败只降级自身，不打死链路）
                continue
            if mask is None or not len(mask):
                continue
            for i, is_sig in enumerate(mask.tolist()):
                if is_sig and dates[i] in window:
                    hit_patterns.append(pname)
                    sig_dates.append(dates[i])
                    break
        if not hit_patterns:
            continue
        sig_date = max(sig_dates)
        sig_pos = dates.index(sig_date)
        stats["by_pattern"][hit_patterns[0]] = stats["by_pattern"].get(hit_patterns[0], 0) + 1
        out.append({
            "sym": sym,
            "pattern": hit_patterns[0],                       # 主要战法（首个命中）
            "pattern_cn": PATTERN_CN.get(hit_patterns[0], hit_patterns[0]),
            "patterns": sorted(set(hit_patterns)),            # 多战法共振
            "sig_date": sig_date,
            "bars_since_sig": len(dates) - 1 - sig_pos,       # 0 = 信号就在 asof 当日
            "close_asof": float(d["close"].iloc[-1]),
        })
    # 按信号日降序（最新信号优先）；Python 排序稳定 ⇒ 同信号日保持 dmap 原顺序，可复现
    out.sort(key=lambda r: r["sig_date"], reverse=True)
    stats["n_pool"] = len(out)
    return out, stats


def pattern_of(pool: list[dict]) -> dict:
    """{sym: 记录} 便于 scan 做 O(1) 查询与留痕。"""
    return {r["sym"]: r for r in pool}
