"""时序契约（蓝图 P0.2 / B0-P0-2 修复）：买入成交的时间单调性与信号新鲜度。

语义（F10 勘察核实）：TDX 1m bar 标签 T = 该 bar 结束时刻，覆盖墙钟 [T-60s, T)。
字段语义：
- signal_ts   触发 bar 的标签（分钟粒度，'YYYY-MM-DD HH:MM'）
- decision_ts 决策确认墙钟（秒级，'YYYY-MM-DD HH:MM:SS'）
- exec_bar_ts 成交 bar 标签 = fill.ts（扫描路径 = 触发 bar 下一根；触发为末根时 = 触发 bar 收盘价）
- recorded_at 账本写入墙钟（秒级）

规则（蓝图 §9 Phase 0 / P0.2 验收条款）：
- R1 新鲜度：decision_ts - signal_ts <= 120s（在途 bar 标签 > decision 时 age 为负，放行）
- R2 成交因果：decision_ts >= exec_bar_ts - 60s（exec bar 窗口已开始，其 open 在 decision 时已知）
- R3 单调+容差：recorded_at >= exec_bar_ts - 60s 且 recorded_at >= decision_ts
  （容差恰为一根 bar：分钟标签与秒级墙钟的确定性错位，见 300489 案例）

锚定案例（两笔真实成交，双判定测试用）：
- 300489（合规）：signal 09:59 → decision 09:59:09 → exec bar 10:00 → recorded 09:59:09
  exec bar 10:00 覆盖 09:59:00-09:59:59，decision 在其窗口内、open 已知 → 全规则通过
- 300468（违规）：signal 10:05 → decision 13:12:08 → R1 违规（3h07m >> 120s）→ 拒单
"""
from __future__ import annotations

from datetime import datetime

FRESHNESS_SECONDS = 120
BAR_TOLERANCE_SECONDS = 60


def parse_ts(ts: str) -> datetime:
    """接受 'YYYY-MM-DD HH:MM'（bar 标签）或 'YYYY-MM-DD HH:MM:SS'（墙钟）。"""
    s = str(ts).strip()
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f'无法解析时间戳: {ts!r}')


def signal_fresh(signal_bar_label: str, decision_ts: str) -> tuple[bool, str]:
    """R1: 信号新鲜度（在途 bar 视为最新）。"""
    sig = parse_ts(signal_bar_label)
    dec = parse_ts(decision_ts)
    age = (dec - sig).total_seconds()
    if age > FRESHNESS_SECONDS:
        return False, f'R1 信号新鲜度违规: signal={signal_bar_label} decision={decision_ts} age={age:.0f}s > {FRESHNESS_SECONDS}s'
    return True, 'ok'


def validate_buy_timing(signal_ts: str, decision_ts: str, exec_bar_ts: str,
                        recorded_at: str) -> tuple[bool, str]:
    """R1+R2+R3 全量校验。返回 (ok, reason)；reason 在违规时供拒单留痕。"""
    sig = parse_ts(signal_ts)
    dec = parse_ts(decision_ts)
    ex = parse_ts(exec_bar_ts)
    rec = parse_ts(recorded_at)
    # R1 新鲜度
    age = (dec - sig).total_seconds()
    if age > FRESHNESS_SECONDS:
        return False, f'R1 信号新鲜度违规: signal={signal_ts} decision={decision_ts} age={age:.0f}s > {FRESHNESS_SECONDS}s'
    # R2 成交因果：exec bar 窗口须已开始（open 已知）
    if (dec - ex).total_seconds() < -BAR_TOLERANCE_SECONDS:
        return False, f'R2 成交因果违规: decision={decision_ts} 早于 exec bar 窗口起点 {exec_bar_ts} 超过 {BAR_TOLERANCE_SECONDS}s'
    # R3 单调+容差
    if (rec - ex).total_seconds() < -BAR_TOLERANCE_SECONDS:
        return False, f'R3 记录时点违规: recorded={recorded_at} 早于 exec bar {exec_bar_ts} 超过 {BAR_TOLERANCE_SECONDS}s'
    if rec < dec:
        return False, f'R3 单调违规: recorded={recorded_at} 早于 decision={decision_ts}'
    return True, 'ok'
