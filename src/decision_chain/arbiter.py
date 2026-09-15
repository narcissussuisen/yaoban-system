# -*- coding: utf-8 -*-
"""R3.4 · 三方冲突裁定器（人格 SOP 硬规则 × LLM 裁量 × 风控 veto）。

## 权威依据
`docs/EVOALPHA_V2_RESTRUCTURE_PLAN.md`：
- §R3.4：「冲突注入测试：人格 SOP vs LLM 裁量 vs 风控 veto 三方冲突时，**独立意见可见、veto 不可绕过**」
- §4.1 纪律：「LLM 可以在区间内裁量，**不能创造买入理由**，不能覆盖硬规则与风控 veto（继承蓝图 §6）」
- R1.6 `decision.risk_gate` 的 note：「**风控 veto 不可被裁量绕过**；checks 列出实际执行的门禁项」

## 优先级（写死，不可由调用方参数化）
```
risk_veto  >  hard_rules(SOP)  >  llm_discretion
```
- **风控 veto 最高**：任何情况下 veto 都不可被 SOP 或 LLM 绕过
- **硬规则次之**：SOP 说不行就是不行，LLM 不能覆盖
- **LLM 最低**：它只在「风控放行 + 硬规则放行」的**区间内**做选择（选什么档、做不做）

## ⭐ 两个必须成立的不变量（由 `tests/test_conflict_injection.py` 穷举验证）
1. **`veto_never_bypassed`**：只要 `risk.veto=True` ⇒ `final == "blocked"`，
   无论 SOP/LLM 怎么判、无论它们多"有理"。
2. **`independent_opinions_visible`**：`opinions` **永远包含三方各自的原始意见**
   （含被否决方），且被否决方必须在 `dissent` 里留痕 —— **不得静默丢弃**。
   > 「独立意见可见」的意义：否决发生后仍能复盘「谁不同意、为什么」，
   > 否则冲突被抹平、无法审计，R3.3 的决策差异报告也会失去依据。

## 越界输出的处理
LLM 的 `output` 若**不在该裁量点的枚举内**（R1.6 R-IMPL-4：`output ∈ 枚举`），
则该项意见标 `valid=False`，**不参与裁定**（相当于"这条意见无效"），
但**仍保留在 `opinions` 里**并附 `invalid_reason` —— 便于发现 prompt/模型退化（如 2026-09-13 的驼峰键名事故）。
"""
from __future__ import annotations

import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parents[2]
if str(BASE / "src") not in sys.path:
    sys.path.insert(0, str(BASE / "src"))

# 优先级序（数字越小越优先；**写死**，不暴露为参数）
PRECEDENCE = {"risk": 0, "sop": 1, "discretion": 2}

# ⭐ `final` 携带**结论**（不是"谁来决定"）—— 否则无法判断各方是否与结论一致，
#    也就无法区分「真异议」与「一致同意」（2026-09-13 由测试暴露后修正）。
FINAL = {
    "blocked": "风控 veto —— 结论「否决」，任何裁量**不可绕过**",
    "rejected": "结论「拒绝」（由风控/SOP/LLM 裁量中优先级最高者给出）",
    "accepted": "结论「放行」",
}


def _norm_discretion(d, point_enums: dict | None = None) -> dict:
    """把 LLM 意见规整成裁定用结构；**校验枚举**，越界标 invalid（但不丢弃）。"""
    if not d:
        return {}
    pid = d.get("point_id") or d.get("discretion_id")
    out = d.get("output") or d.get("choice")
    valid, why = True, ""
    if not pid:
        valid, why = False, "缺 point_id"
    elif point_enums is not None:
        allowed = point_enums.get(pid)
        if allowed is None:
            valid, why = False, f"未知裁量点 {pid}"
        elif out not in allowed:
            valid, why = False, f"output={out!r} 越出 {pid} 枚举 {allowed}"
    return dict(point_id=pid, output=out, valid=valid, invalid_reason=why,
                model=d.get("model"), rationale=d.get("rationale") or d.get("reason"),
                raw=dict(d))


def arbitrate(*, sop: dict | None = None, discretion: dict | None = None,
              risk: dict | None = None, point_enums: dict | None = None,
              veto_outputs: dict | None = None) -> dict:
    """三方裁定。**返回结构保证 `opinions` 含三方全部意见**（含被否决方）。

    :param veto_outputs: 各裁量点里**代表"否决"的枚举值**（如 `{"D6": "C_reject"}`）。
        用于判断「LLM 的裁量是否等同于提出否决」，进而算它是否与结论一致。
        默认空 → 视为「LLM 从不投否决」（保守）。engine 传 `llm.POINTS[*].veto_choice`（单一事实源）。
    """
    sop = sop or {}
    risk = risk or {}
    veto_outputs = veto_outputs or {}
    disc = _norm_discretion(discretion, point_enums) if discretion else {}

    # `opinions`：三方各自原始意见（**始终保留**，不因被否决而删除）
    # ⚠️ 字段名用 `passed` 而非 `pass` —— 后者是 Python 关键字，无法作关键字参数。
    opinions = {
        "sop": dict(passed=bool(sop.get("passed", True)), reason=str(sop.get("reason") or ""),
                    rules=list(sop.get("rules") or [])),
        "discretion": (dict(point_id=disc.get("point_id"), output=disc.get("output"),
                            valid=disc.get("valid"), invalid_reason=disc.get("invalid_reason"),
                            model=disc.get("model"), rationale=disc.get("rationale"))
                       if disc else None),
        "risk": dict(veto=bool(risk.get("veto")), reason=str(risk.get("reason") or ""),
                     checks=list(risk.get("checks") or [])),
    }

    # ── 各方的「期望结论」：用于判断一致性（也是 dissent 的唯一依据）
    risk_want = "blocked" if opinions["risk"]["veto"] else None          # None=无强主张
    sop_want = None if opinions["sop"]["passed"] else "rejected"
    disc_want = None
    if disc and disc.get("valid"):
        disc_want = ("rejected" if disc.get("output") == veto_outputs.get(disc.get("point_id"))
                     else "accepted")

    # ── 裁定：结论由**优先级最高且持有主张的一方**决定
    if risk_want:
        final, winner = "blocked", "risk"
    elif sop_want:
        final, winner = "rejected", "sop"
    elif disc_want:
        final, winner = disc_want, "discretion"
    else:
        final, winner = "accepted", "mechanical"

    # ── 异议留痕：**只记真正的分歧**（该方的期望结论 ≠ 实际结论）
    #    ⚠️ 一致时不得留痕 —— 否则 dissent 会被噪声填满、失去审计价值
    #    （2026-09-13：首版在 winner=sop 时无条件把 LLM 记为异议，被测试 test_03 抓住）。
    dissent = []
    for party, want in (("risk", risk_want), ("sop", sop_want), ("discretion", disc_want)):
        if want is not None and want != final:
            o = opinions[party]
            dissent.append(dict(party=party, wants=want, got=final,
                                position=o.get("output") if party == "discretion" else
                                         ("veto" if party == "risk" else f"passed={o.get('passed')}"),
                                reason=o.get("rationale") or o.get("reason")))

    return dict(
        final=final, winner=winner, precedence=dict(PRECEDENCE),
        final_note=FINAL[final],
        opinions=opinions,
        dissent=dissent,
        n_dissent=len(dissent),
        veto_bypassed=False,          # ⭐ 恒为 False：本裁定器不存在绕过路径
        veto_never_bypassable=True,
        discipline="风险 veto > 硬规则(SOP) > LLM 裁量（§4.1）—— **veto 不可绕过**；"
                   "被否决方意见**不得静默丢弃** —— 一律保留在 opinions 且留痕于 dissent。",
    )


# ⭐ R1.6 `decision.risk_gate` 只接受 fields=[passed, veto_reason, checks]
def to_risk_gate(v: dict) -> dict:
    """把裁定结果映射成 R1.6 `decision.risk_gate`（字段受限，不额外加键）。"""
    not_passed = v["final"] in ("blocked", "rejected")
    reasons = []
    if not_passed:
        reasons.append(v["opinions"]["risk"]["reason"] or v["final"])
    if v["dissent"]:
        reasons.append("dissent=" + ",".join(
            f"{d['party']}(wants {d['wants']})" for d in v["dissent"]))
    return dict(passed=not not_passed,
                veto_reason="; ".join(r for r in reasons if r),
                checks=list(v["opinions"]["risk"]["checks"]) or ["shadow_mode", "no_order"])
