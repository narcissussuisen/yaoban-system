# -*- coding: utf-8 -*-
"""R3.4 · 冲突注入测试：人格 SOP vs LLM 裁量 vs 风控 veto。

验收（§R3）：「**冲突注入可见独立意见**」；§R3.4：「veto 不可绕过」。

本套测试有**两类**：
- **场景注入**（具名冲突剧本）—— 便于人读、便于复盘
- ⭐ **穷举不变量**（笛卡尔积遍历）—— 不依赖我"想得到哪些场景"。
  具名场景只能证明我想到的情况；**穷举才能证明"不存在绕过路径"**。

## 语义约定（2026-09-13 由本轮测试暴露后确立）
- `final` 携带**结论**：`blocked` / `rejected` / `accepted`
- `winner` 表示**谁定的**：`risk` / `sop` / `discretion` / `mechanical`
- `dissent` **只记真正的分歧**（该方期望结论 ≠ 实际结论）；一致时**不得留痕**，
  否则 dissent 被噪声填满、失去审计价值。
"""
from __future__ import annotations

import itertools
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from decision_chain import arbiter as A  # noqa: E402

ENUMS = {"D6": ["A_optimum", "B_medium", "C_reject"],
         "D8": ["floor", "mid", "ceiling"]}
# 与 engine 同源：由 llm.POINTS[*].veto_choice 给出「哪一档代表否决」
VETO_OUT = {"D6": "C_reject", "D8": None}

SOP_PASS = dict(passed=True, reason="", rules=["GEN-ENTRY-04"])
SOP_FAIL = dict(passed=False, reason="破均价线（GEN-ENTRY-04）", rules=["GEN-ENTRY-04"])
RISK_OK = dict(veto=False, reason="", checks=["shadow_mode", "no_order"])
RISK_VETO = dict(veto=True, reason="跌停不可卖 / 账户模式非 autonomous_paper",
                 checks=["price_limit", "account_mode"])
DISC_BEST = dict(discretion_id="D6", output="A_optimum", model="deepseek-chat",
                 rationale="有量+一步步向上")
DISC_REJECT = dict(discretion_id="D6", output="C_reject", model="deepseek-chat",
                   rationale="第一波回落破均价线")
DISC_INVALID = dict(discretion_id="D6", output="D_buy_now", model="deepseek-chat",
                    rationale="表外枚举")

ALL_SOP = [None, SOP_PASS, SOP_FAIL]
ALL_DISC = [None, DISC_BEST, DISC_REJECT, DISC_INVALID]
ALL_RISK = [None, RISK_OK, RISK_VETO]


def arb(sop=None, disc=None, risk=None):
    return A.arbitrate(sop=sop, discretion=disc, risk=risk,
                       point_enums=ENUMS, veto_outputs=VETO_OUT)


def wants(sop, disc, risk):
    """各方**期望的结论**（与 arbitrate 内部同一套定义；供不变量断言独立复算）。"""
    w = {}
    w["risk"] = "blocked" if (risk or {}).get("veto") else None
    w["sop"] = None if (sop or {}).get("passed", True) else "rejected"
    if disc is None:
        w["discretion"] = None
    elif disc.get("output") not in ENUMS.get(disc.get("discretion_id"), []):
        w["discretion"] = None                       # 越界 → 无有效主张
    else:
        w["discretion"] = ("rejected" if disc["output"] == VETO_OUT.get(disc["discretion_id"])
                           else "accepted")
    return w


class ScenarioInjectionTests(unittest.TestCase):
    """具名冲突剧本。"""

    def test_01_llm_best_but_risk_veto(self):
        """① LLM 选最优档 + 风控 veto → **必须 blocked**，且 LLM 意见仍可见。"""
        v = arb(SOP_PASS, DISC_BEST, RISK_VETO)
        self.assertEqual(v["final"], "blocked")
        self.assertEqual(v["winner"], "risk")
        self.assertEqual(v["opinions"]["discretion"]["output"], "A_optimum",
                         "被否决的 LLM 意见必须保留在 opinions")
        parties = {d["party"] for d in v["dissent"]}
        self.assertIn("discretion", parties, "LLM 期望 accepted 但被 blocked → 必须留痕")

    def test_02_llm_best_but_sop_reject(self):
        """② 硬规则拒绝 + LLM 选最优档 → 必须 rejected，LLM 不可覆盖硬规则。"""
        v = arb(SOP_FAIL, DISC_BEST, RISK_OK)
        self.assertEqual(v["final"], "rejected")
        self.assertEqual(v["winner"], "sop")
        self.assertIn("discretion", {d["party"] for d in v["dissent"]})

    def test_03_both_reject_no_dissent(self):
        """③ 硬规则与 LLM **都拒绝** → rejected 且**无异议**。

        ⭐ 本条正是首版裁量器的缺陷所在：它无条件把 LLM 记为异议 → dissent 被噪声填满。
        意见一致时**不该产生异议**（否则"独立意见可见"退化为"满屏噪声"）。
        """
        v = arb(SOP_FAIL, DISC_REJECT, RISK_OK)
        self.assertEqual(v["final"], "rejected")
        self.assertEqual(v["dissent"], [], "双方都主张 rejected → 不该有异议")

    def test_04_veto_overrides_everything(self):
        """④ 风控 veto + 硬规则放行 + LLM 最优 → blocked，且**两方都留痕**。"""
        v = arb(SOP_PASS, DISC_BEST, RISK_VETO)
        self.assertEqual(v["final"], "blocked")
        self.assertEqual({d["party"] for d in v["dissent"]}, {"discretion"},
                         "SOP 放行且结果也是 blocked → SOP 不算异议；LLM 主张 accepted → 算")

    def test_05_llm_out_of_enum_not_allowed_to_decide(self):
        """⑤ LLM 越界输出 → 标 invalid、**不参与裁定**，但仍可见（便于发现 prompt/模型退化）。"""
        v = arb(SOP_PASS, DISC_INVALID, RISK_OK)
        self.assertFalse(v["opinions"]["discretion"]["valid"])
        self.assertIn("枚举", v["opinions"]["discretion"]["invalid_reason"])
        self.assertEqual(v["winner"], "mechanical", "无效裁量不得决定结果")
        self.assertEqual(v["final"], "accepted")

    def test_06_all_pass_discretion_decides(self):
        """⑥ 三方全放行 → 结论 adopted 自 LLM 判（这正是它的合法职权）。"""
        v = arb(SOP_PASS, DISC_BEST, RISK_OK)
        self.assertEqual(v["final"], "accepted")
        self.assertEqual(v["winner"], "discretion")
        self.assertEqual(v["dissent"], [])

    def test_07_no_discretion_pure_mechanical(self):
        """⑦ 无裁量输入（--no-llm / 降级）→ `winner=mechanical`，纯机械放行。"""
        v = arb(SOP_PASS, None, RISK_OK)
        self.assertEqual(v["final"], "accepted")
        self.assertEqual(v["winner"], "mechanical")
        self.assertIsNone(v["opinions"]["discretion"])


class ExhaustiveInvariantTests(unittest.TestCase):
    """⭐ 穷举不变量（3×4×3 = 36 组合）。"""

    def test_invariant_veto_never_bypassed(self):
        """⭐ 硬不变量：只要 `risk.veto=True` ⇒ `final=="blocked"`，**无任何例外**。"""
        n = 0
        for sop, disc, risk in itertools.product(ALL_SOP, ALL_DISC, ALL_RISK):
            n += 1
            v = arb(sop, disc, risk)
            if (risk or {}).get("veto"):
                self.assertEqual(v["final"], "blocked",
                                 f"veto 被绕过！sop={sop} disc={disc}")
                self.assertEqual(v["winner"], "risk")
                self.assertFalse(v["veto_bypassed"])
        self.assertEqual(n, 36, "组合数应为 3×4×3=36")

    def test_invariant_discretion_never_overrides_sop(self):
        """⭐ 硬不变量：SOP 拒绝时，LLM 永不能把结论变成"放行"。"""
        for disc in ALL_DISC:
            v = arb(SOP_FAIL, disc, RISK_OK)
            self.assertEqual(v["final"], "rejected",
                             f"SOP 拒绝却未拒绝：disc={disc} → {v['final']}")

    def test_invariant_opinions_always_three_parties(self):
        """⭐ 硬不变量：`opinions` 永远含 sop/risk/discretion 三键（独立意见可见）。"""
        for sop, disc, risk in itertools.product(ALL_SOP, ALL_DISC, ALL_RISK):
            self.assertEqual(set(arb(sop, disc, risk)["opinions"]),
                             {"sop", "discretion", "risk"})

    def test_invariant_dissent_is_exactly_the_disagreement(self):
        """⭐ 硬不变量：`dissent` **恰好等于**「期望结论 ≠ 实际结论」的那批方 —— 不多不少。

        这一条同时覆盖两个方向：
        - **不可遗漏**（异议必须可见）
        - **不可多余**（一致时不得留痕，否则 dissent 失去审计价值）
        """
        for sop, disc, risk in itertools.product(ALL_SOP, ALL_DISC, ALL_RISK):
            v = arb(sop, disc, risk)
            w = wants(sop, disc, risk)
            expect = {p for p, want in w.items() if want is not None and want != v["final"]}
            got = {d["party"] for d in v["dissent"]}
            self.assertEqual(got, expect,
                             f"dissent 不符：final={v['final']} wants={w} got={got} expect={expect}")
            self.assertEqual(v["n_dissent"], len(expect))

    def test_invariant_precedence_is_fixed(self):
        """优先级写死且不可被调用方改变（防将来被参数化后误用）。"""
        v = arb(SOP_PASS, DISC_BEST, RISK_OK)
        self.assertEqual(v["precedence"], {"risk": 0, "sop": 1, "discretion": 2})
        self.assertTrue(v["veto_never_bypassable"])
        self.assertIn("不可绕过", v["discipline"])

    def test_invariant_winner_is_consistent_with_final(self):
        """`winner` 必须与 `final` 自洽：blocked→risk / rejected→risk 或 sop 或 discretion / accepted→discretion 或 mechanical。"""
        allowed = {"blocked": {"risk"}, "rejected": {"risk", "sop", "discretion"},
                   "accepted": {"discretion", "mechanical"}}
        for sop, disc, risk in itertools.product(ALL_SOP, ALL_DISC, ALL_RISK):
            v = arb(sop, disc, risk)
            self.assertIn(v["winner"], allowed[v["final"]],
                          f"winner={v['winner']} 与 final={v['final']} 不符")


class RiskGateMappingTests(unittest.TestCase):
    """裁定结果 → R1.6 `decision.risk_gate`（字段受限：passed/veto_reason/checks）。"""

    def test_blocked_maps_to_not_passed_with_reason(self):
        g = A.to_risk_gate(arb(SOP_PASS, DISC_BEST, RISK_VETO))
        self.assertEqual(set(g), {"passed", "veto_reason", "checks"},
                         "risk_gate 只能有 R1.6 声明的三个字段")
        self.assertFalse(g["passed"])
        self.assertIn("dissent=", g["veto_reason"], "异议必须在 veto_reason 里可见")

    def test_rejected_maps_to_not_passed(self):
        self.assertFalse(A.to_risk_gate(arb(SOP_FAIL, DISC_BEST, RISK_OK))["passed"])

    def test_clean_pass_maps_to_passed(self):
        g = A.to_risk_gate(arb(SOP_PASS, DISC_BEST, RISK_OK))
        self.assertTrue(g["passed"])
        self.assertEqual(g["veto_reason"], "")

    def test_risk_gate_never_exposes_bypass(self):
        """穷举：任何组合下 risk_gate 都不得出现「veto 被绕过」的形态。"""
        for sop, disc, risk in itertools.product(ALL_SOP, ALL_DISC, ALL_RISK):
            v = arb(sop, disc, risk)
            g = A.to_risk_gate(v)
            if (risk or {}).get("veto"):
                self.assertFalse(g["passed"])
                self.assertTrue(g["veto_reason"], "veto 必须有理由留痕")


if __name__ == "__main__":
    unittest.main(verbosity=2)
