"""每日自迭代批（EvoAlphaDailyIteration）核心数据结构。

设计原则（继承 EvoAlpha 蓝图硬边界）：
  1. 确定性优先：本包所有逻辑禁止 LLM 语义判断；卡片只搬运资料已归纳结论。
  2. 参数类 / 规则类分治：参数类过门槛才生效，规则类与代码类永远只出提案等人工确认。
  3. 失败可见：样本不足、数据缺口、未量化规则一律显式标注，不得静默通过。
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass, field

# 变更类别：决定是否可自动生效
CHANGE_PARAMETER = "parameter"   # 已存在参数的阈值调整 → 过门槛可生效
CHANGE_RULE = "rule"             # 新规则/规则结构变化 → 永远待人工确认
CHANGE_CODE = "code"             # 需要改引擎代码 → 永远待人工确认
CHANGE_DATA = "data"             # 数据缺口/口径修正 → 永远待人工确认

CHANGE_CLASSES = (CHANGE_PARAMETER, CHANGE_RULE, CHANGE_CODE, CHANGE_DATA)

VERDICT_APPLIED = "applied"
VERDICT_REJECTED = "rejected"
VERDICT_PENDING_CONFIRM = "pending_confirm"


def today_str() -> str:
    return _dt.date.today().isoformat()


def now_str() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Card:
    """知识卡片（机读投影）。source 逐条可追溯，禁止无来源结论。"""
    card_id: str
    title: str
    category: str
    source: str
    source_date: str
    statement: str
    quantified: str | None = None
    testability: str = "qualitative"   # daily | intraday | qualitative
    sample_n: int | None = None
    linked_params: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Proposal:
    """迭代提案。parameter 类可过门槛自动生效；其余类别恒 pending_confirm。"""
    proposal_id: str
    change_class: str
    title: str
    rationale: str
    card_ids: list[str] = field(default_factory=list)
    target: dict = field(default_factory=dict)        # {param_path: {"from":x,"to":y}}
    evidence: dict = field(default_factory=dict)      # 回归结果/门槛判定
    thresholds: dict = field(default_factory=dict)    # 门槛明细（passed/failed 逐项）
    samples: int = 0
    risk: str = ""                                    # 失效后果/回滚方式
    status: str = VERDICT_PENDING_CONFIRM
    decided_at: str = ""
    decision_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BatchResult:
    """一次批次的产物索引。"""
    run_id: str
    date: str
    generated_at: str
    mats_new: int = 0
    mats_total: int = 0
    cards_total: int = 0
    cards_new: int = 0
    proposals_total: int = 0
    proposals_applied: int = 0
    proposals_pending: int = 0
    shadow_runs: int = 0
    shadow_ok: int = 0
    shadow_insufficient: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
