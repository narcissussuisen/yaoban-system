# -*- coding: utf-8 -*-
"""R1.6 契约测试：decision_digest 的可重放性与分层判定。

为什么需要这组测试：
  R5.0 要用 `replay_hash` 把「实现层改动」与「行为改动」机械分开
  （hash 不变 = 只动实现 → 可直接晋级；hash 变化 = 行为改动 → 必须进 60 日队列）。
  这个分判据成立的前提是 `replay_hash` 的**敏感性边界必须精确**：
    - 必须对「决策内容」敏感（否则行为改动会被误判为实现层改动 → 危险：等于让行为改动绕过 60 日评审）
    - 必须对「运行期属性与结果」不敏感（否则同一次决策在不同时刻/不同执行环境下会算出不同 hash
      → 重放永远不一致，判据失效）
  两条边界都容易被后续改动无声破坏，故用测试钉住。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.decision_digest import (  # noqa: E402
    IMPLEMENTATION_HASH_FIELDS, SCHEMA_VERSION, build_digest, canonical, diff_fields,
    layer_verdict, replay_hash, validate,
)

SOP_IDS = {"GEN-HOLD-01", "GEN-HOLD-22", "GEN-ENTRY-04", "P1-HOLD-01"}
DISC_IDS = {"D1", "D6", "D7"}
DISC_ENUMS = {"D6": ["A_optimum", "B_medium", "C_reject"]}


def mk(**over):
    """构造一个合规 digest；用 over 覆盖任意 kwargs。"""
    kw = dict(
        decision_id="dec-tick-20260911-300468",
        day="2026-09-11", sym="300468", side="sell",
        signal_ts="2026-09-11 09:41:08",
        decision_ts="2026-09-11 09:41:08",
        recorded_at="2026-09-11 09:41:09",
        candidate_snapshot_id="cand-20260911-0943",
        market_snapshot_hash="a" * 64,
        sop_version_id="v0", params_hash="b" * 64,
        rules_fired=["GEN-HOLD-22", "P1-HOLD-01"],
        discretions=[dict(point_id="D7", output="logic_invalidated",
                          rationale="破 20 日线且次日未收回", model="deepseek-v4-flash",
                          prompt_sha256="c" * 64, cli_version="n/a")],
        risk_gate=dict(passed=True, veto_reason="", checks=["single_stock_pct", "stop_px"]),
        order_intent=dict(side="sell", qty=1800, px_limit=23.30,
                          reason="stop_loss", plan_ref="plan-20260911"),
        executed=True, kind="fill", authority="account.fills",
        fill_ref="300468|2026-09-11T09:41:09|1800|23.31",
        narrative_refs=["MEM-005"], seq=2,
        pnl_attributable=-1930.52, effectiveness_basis="v0-seg-1",
    )
    kw.update(over)
    return build_digest(**kw)


class HashBoundaryTests(unittest.TestCase):
    """replay_hash 的敏感性边界 —— 本组是本契约的核心。"""

    def test_canonical_is_key_order_independent(self):
        self.assertEqual(canonical({"b": 1, "a": 2}), canonical({"a": 2, "b": 1}))
        self.assertEqual(canonical({"x": 1, "y": 2}), '{"x":1,"y":2}')

    def test_same_input_same_hash(self):
        self.assertEqual(mk()["replay_hash"], mk()["replay_hash"])

    def test_rules_order_does_not_matter(self):
        a = mk(rules_fired=["GEN-HOLD-22", "P1-HOLD-01"])
        b = mk(rules_fired=["P1-HOLD-01", "GEN-HOLD-22"])
        # 刻意选择：列表顺序属同一语义（同一组规则），故 canonical 保序会不同；
        # 若将来要求顺序无关，应改为 set 语义并在此断言相等。
        self.assertNotEqual(a["replay_hash"], b["replay_hash"],
                            "当前契约：rules_fired 为有序列表，顺序变化即视为内容变化")

    def test_behavior_field_change_changes_hash(self):
        base = mk()
        for over in (dict(rules_fired=["GEN-HOLD-01"]),
                     dict(order_intent=dict(side="sell", qty=900, px_limit=23.30,
                                            reason="vwap_halve", plan_ref="plan-20260911")),
                     dict(risk_gate=dict(passed=False, veto_reason="drawdown_floor", checks=[])),
                     dict(discretions=[]),
                     dict(narrative_refs=[]),
                     dict(sop_version_id="v1"),
                     dict(market_snapshot_hash="d" * 64)):
            self.assertNotEqual(base["replay_hash"], mk(**over)["replay_hash"],
                                f"行为字段改动必须改变 replay_hash：{list(over)}")

    def test_timing_change_does_not_change_hash(self):
        base = mk()
        for over in (dict(signal_ts="2026-09-11 09:41:00"),
                     dict(decision_ts="2026-09-11 09:41:07"),
                     dict(recorded_at="2026-09-11 09:41:30"),
                     dict(seq=9)):
            self.assertEqual(base["replay_hash"], mk(**over)["replay_hash"],
                             f"运行期属性不得改变 replay_hash：{list(over)}")

    def test_outcome_change_does_not_change_hash(self):
        base = mk()
        for over in (dict(pnl_attributable=999999.0),
                     dict(effectiveness_basis="v0-seg-99")):
            self.assertEqual(base["replay_hash"], mk(**over)["replay_hash"],
                             "结果绝不能进内容哈希（否则赚钱与否会改变『决策是什么』）")

    def test_execution_context_excluded_from_hash(self):
        """⭐ 本契约最容易被误改的一条：执行环境字段必须**不进**内容哈希。

        若把 kind/authority 纳入哈希，R3 影子盘 → R4 实盘的同一个决策会得到不同 fingerprint，
        R5.0 会把「渠道切换」误判为行为改动，对每一天都触发 60 日评审 → 判据失效。
        """
        base = mk()
        for over in (dict(executed=False),
                     dict(fill_ref="different"),
                     dict(kind="shadow", authority="shadow", executed=False, fill_ref=""),
                     dict(kind="audit_counterfactual", authority="audit_counterfactual"),
                     dict(seq=99)):
            self.assertEqual(base["replay_hash"], mk(**over)["replay_hash"],
                             f"执行环境字段不得改变 replay_hash：{list(over)}")

    def test_execution_context_list_is_explicit(self):
        from core.decision_digest import EXECUTION_CONTEXT_FIELDS
        for f in ("action.kind", "action.authority", "action.executed",
                  "timing.signal_ts", "timing.freshness_sec"):
            self.assertIn(f, EXECUTION_CONTEXT_FIELDS)
            self.assertNotIn(f, IMPLEMENTATION_HASH_FIELDS)

    def test_hash_field_list_is_explicit(self):
        for bad in ("timing.signal_ts", "outcome.pnl_attributable", "action.executed",
                    "action.fill_ref", "replay_hash", "digest_id"):
            self.assertNotIn(bad, IMPLEMENTATION_HASH_FIELDS)


class LayerVerdictTests(unittest.TestCase):
    def test_no_baseline_is_behavior(self):
        v = layer_verdict(None, mk())
        self.assertEqual(v["verdict"], "behavior")
        self.assertTrue(v["replay_hash_changed"])

    def test_same_hash_is_implementation(self):
        a, b = mk(), mk(decision_ts="2026-09-11 09:41:09", recorded_at="2026-09-11 09:41:12")
        v = layer_verdict(a, b)
        self.assertEqual(v["verdict"], "implementation")
        self.assertFalse(v["replay_hash_changed"])

    def test_changed_hash_is_behavior(self):
        v = layer_verdict(mk(), mk(rules_fired=["GEN-HOLD-01"]))
        self.assertEqual(v["verdict"], "behavior")
        self.assertTrue(v["replay_hash_changed"])

    def test_diff_fields_points_at_real_change(self):
        d = diff_fields(mk(), mk(risk_gate=dict(passed=False, veto_reason="x", checks=[])))
        self.assertEqual([x["field"] for x in d], ["decision.risk_gate"])


class ValidateTests(unittest.TestCase):
    def test_clean_digest_passes(self):
        self.assertEqual(validate(mk(), sop_rule_ids=SOP_IDS, discretion_ids=DISC_IDS,
                                  discretion_enums=DISC_ENUMS), [])

    def test_missing_required_field(self):
        d = mk()
        del d["inputs"]["market_snapshot_hash"]
        self.assertTrue(any("R-IMPL-2" in e and "market_snapshot_hash" in e for e in validate(d)))

    def test_enum_violation(self):
        d = mk(side="hold")
        d["side"] = "sideways"
        self.assertTrue(any("越出枚举" in e for e in validate(d)))

    def test_unknown_rule_id_rejected(self):
        d = mk(rules_fired=["NOT-A-RULE"])
        errs = validate(d, sop_rule_ids=SOP_IDS)
        self.assertTrue(any("SOP 表外规则" in e for e in errs))

    def test_discretion_output_out_of_enum(self):
        d = mk(discretions=[dict(point_id="D6", output="XYZ", rationale="r", model="m",
                                 prompt_sha256="c" * 64, cli_version="n/a")])
        errs = validate(d, discretion_ids=DISC_IDS, discretion_enums=DISC_ENUMS)
        self.assertTrue(any("越出枚举" in e for e in errs))

    def test_unknown_discretion_point(self):
        d = mk(discretions=[dict(point_id="D99", output="A_optimum", rationale="r",
                                 model="m", prompt_sha256="c" * 64, cli_version="n/a")])
        errs = validate(d, discretion_ids=DISC_IDS)
        self.assertTrue(any("不在 D1–D10" in e for e in errs))

    def test_freshness_gate_only_for_non_tick(self):
        d = mk(signal_ts="2026-09-11 09:30:00", decision_ts="2026-09-11 09:35:00",
               tick_executor=True)
        self.assertEqual([e for e in validate(d) if "R-IMPL-3" in e], [],
                         "tick 执行器豁免 120s 新鲜度门（SELL_EXECUTION_CONTRACT §2）")
        d2 = mk(signal_ts="2026-09-11 09:30:00", decision_ts="2026-09-11 09:35:00",
                tick_executor=False)
        self.assertTrue(any("R-IMPL-3" in e for e in validate(d2)))

    def test_timing_monotonicity(self):
        d = mk(decision_ts="2026-09-11 09:41:10", recorded_at="2026-09-11 09:41:09")
        self.assertTrue(any("recorded_at 早于 decision_ts" in e for e in validate(d)))

    def test_audit_counterfactual_must_not_be_executed(self):
        d = mk(kind="audit_counterfactual", executed=True)
        self.assertTrue(any("R-IMPL-5" in e for e in validate(d)),
                        "审计重建被计为成交 → 双重计算风险（SELL_EXECUTION_CONTRACT §4）")

    def test_executed_requires_account_fills_authority(self):
        d = mk(executed=True, authority="shadow")
        self.assertTrue(any("account.fills" in e for e in validate(d)))

    def test_hash_tamper_detected(self):
        d = mk()
        d["action"]["order_intent"]["qty"] = 1  # 篡改内容但不更新 hash
        self.assertTrue(any("replay_hash 与内容不符" in e for e in validate(d)))

    def test_revision_mismatch(self):
        d = mk()
        d["digest_revision"] = "decision-digest/v0"
        self.assertTrue(any("digest_revision" in e for e in validate(d)))

    def test_schema_version_constant(self):
        self.assertEqual(SCHEMA_VERSION, "decision-digest/v1")
        self.assertEqual(mk()["digest_revision"], SCHEMA_VERSION)


class DigestIdTests(unittest.TestCase):
    def test_id_shape_and_determinism(self):
        d = mk()
        self.assertTrue(d["digest_id"].startswith("dd-2026-09-11-2-"))
        self.assertTrue(d["digest_id"].endswith(d["replay_hash"][:8]))
        self.assertEqual(d["digest_id"], mk()["digest_id"])

    def test_id_changes_with_content_but_not_with_seq(self):
        self.assertNotEqual(mk()["digest_id"], mk(rules_fired=["GEN-HOLD-01"])["digest_id"])
        self.assertNotEqual(mk(seq=2)["digest_id"], mk(seq=3)["digest_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
