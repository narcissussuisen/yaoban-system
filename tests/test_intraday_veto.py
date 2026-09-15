"""R4.1 盘中 D6 否决权回归测试（最小切片）。

覆盖四类**必须钉死的不变量**（每一条都对应一次真实风险）：

1. **否决语义** —— `C_reject` 必须丢弃、非否决档必须放行，且否决权来源单一（`llm.POINTS["D6"]["veto_choice"]`）。
2. **降级方向** —— `degraded / unreproducible / schema_failed / 超时 / 异常` **一律放行**
   （等价于退回无否决权现状，相对本次变更是零新增风险；这条最容易被人「顺手改成拒买」）。
3. **当日锁定** —— 日级稳定 `snapshot_hash` + 覆盖到收盘的 TTL ⇒ 同一标的当日**只真调一次**，
   且判定结果不会中途翻转（先 `C_reject` 后 `A_optimum` 会让否决权自我失效）。
4. **绝不阻断 scan** —— 任何内部失败都退化为放行，`judge` 永不抛异常。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "portfolio"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent / "py_libs"))

import pandas as pd  # noqa: E402

from decision_chain import engine as E  # noqa: E402
from decision_chain import intraday_veto as V  # noqa: E402
from decision_chain import llm  # noqa: E402

DAY = "2026-09-11"
NOW = dt.datetime(2026, 9, 11, 10, 6, 0)
SIG = f"{DAY} 10:05:00"

# 测试用 LLM 配置（避免读本机 models.json：测试不得依赖外部凭据状态）
CFG = dict(ok=True, model="deepseek-chat", url="http://127.0.0.1:1/x",
           key="test", config_source="test")


def _mk_df(day=DAY, n=40, start_min=9 * 60 + 31, slope=0.01, base=10.0):
    """构造分钟序列（列契约与 scan_and_confirm.pull_minutes 一致）。"""
    rows = []
    for i in range(n):
        hh, mm = divmod(start_min + i, 60)
        ts = f"{day} {hh:02d}:{mm:02d}"
        px = base + i * slope
        rows.append([ts, px, px * 1.002, px * 0.998, px, 100000 + i * 1000, px * 100000])
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "amount"])


def _ret(choice, status="ok", score=0.5, degraded=False):
    return dict(status=status, choice=choice, score=score, reason="test",
                confidence=0.5, _degraded=degraded, model="deepseek-chat",
                prompt_hash="deadbeef", cli_version=llm.CLI_VERSION, cache_hit=False)


def _raw_payload(choice="B_medium"):
    body = json.dumps(dict(discretion_id="D6", choice=choice, score=0.5,
                           reason="ok", confidence=0.5))
    return dict(payload={"choices": [{"message": {"content": body}, "finish_reason": "stop"}]},
                elapsed_ms=10, served_model="deepseek-flash", usage={"total_tokens": 5},
                finish_reason="stop", has_reasoning=False)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self._vo, self._lo = V.OUT_DIR, llm.OUT_DIR
        V.OUT_DIR = self.tmp / "intraday"
        llm.OUT_DIR = self.tmp / "llm"

    def tearDown(self):
        V.OUT_DIR, llm.OUT_DIR = self._vo, self._lo
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _judge(self, ret=None, exc=None, df=None, now=NOW, code="600000"):
        p = (mock.patch.object(V.llm, "consult", side_effect=exc) if exc
             else mock.patch.object(V.llm, "consult", return_value=ret or _ret("B_medium")))
        with p:
            return V.judge(code, day=DAY, df=_mk_df() if df is None else df,
                           now=now, signal_ts=SIG)


class VetoSemanticsTests(_Base):
    """① 否决语义：只有 C_reject 丢弃。"""

    def test_veto_choice_is_single_source(self):
        """防枚举漂移：否决权来源必须仍是 D6 的 veto_choice（而非硬编码字符串）。"""
        self.assertEqual(llm.POINTS["D6"]["veto_choice"], "C_reject")

    def test_c_reject_is_vetoed(self):
        r = self._judge(_ret("C_reject", score=0.1))
        self.assertFalse(r["allowed"])
        self.assertEqual(r["veto_reason"], "d6_c_reject")

    def test_veto_digest_side_is_skip(self):
        """被否决＝没有这笔买入 → `side=skip`（R1.6：「没做什么」也必须有 digest 才可审计）。"""
        r = self._judge(_ret("C_reject"))
        self.assertEqual(r["digest"]["side"], "skip")
        self.assertEqual(r["digest"]["action"]["order_intent"]["side"], "none")

    def test_a_optimum_passes(self):
        r = self._judge(_ret("A_optimum", score=0.9))
        self.assertTrue(r["allowed"])
        self.assertEqual(r["veto_reason"], "d6_not_reject")

    def test_b_medium_passes(self):
        """保守档 B_medium **不否决** —— 它不是 veto_choice。"""
        r = self._judge(_ret("B_medium"))
        self.assertTrue(r["allowed"])

    def test_pass_digest_side_is_buy(self):
        r = self._judge(_ret("A_optimum"))
        self.assertEqual(r["digest"]["side"], "buy")
        # ⚠️ 但本模块只负责「判定」，成交与否由 ledger 另记 → executed 必须为 False（R-IMPL-5）
        self.assertFalse(r["digest"]["action"]["executed"])
        self.assertNotEqual(r["digest"]["action"]["authority"], "account.fills")


class DegradePassTests(_Base):
    """② 降级方向：任何非 ok 状态都必须放行（防止被改成拒买）。"""

    def test_degraded_passes(self):
        r = self._judge(_ret("B_medium", status="degraded", degraded=True))
        self.assertTrue(r["allowed"])
        self.assertEqual(r["veto_reason"], "degraded_pass")
        self.assertTrue(r["degraded"])

    def test_unreproducible_passes(self):
        r = self._judge(_ret("C_reject", status="unreproducible"))
        # ⚠️ 关键：即便 choice 字面是 C_reject，非 ok 状态也**不得**据此否决
        self.assertTrue(r["allowed"])
        self.assertEqual(r["veto_reason"], "degraded_pass")

    def test_timeout_exception_passes(self):
        r = self._judge(exc=TimeoutError("timed out"))
        self.assertTrue(r["allowed"])
        self.assertEqual(r["status"], "error")
        self.assertIn("exception:TimeoutError", r["veto_reason"])

    def test_http_error_passes(self):
        r = self._judge(exc=OSError("connection reset"))
        self.assertTrue(r["allowed"])
        # 注意路径：risk_gate 在 `decision` 之下（R1.6 契约），不是顶层
        self.assertTrue(r["digest"]["decision"]["risk_gate"]["passed"])

    def test_llm_disabled_passes(self):
        r = V.judge("600000", day=DAY, df=_mk_df(), now=NOW, signal_ts=SIG, use_llm=False)
        self.assertTrue(r["allowed"])
        self.assertEqual(r["veto_reason"], "llm_disabled")


class NeverBlocksScanTests(_Base):
    """④ 绝不阻断：任何畸形输入都不得抛异常。"""

    def test_none_df_does_not_raise(self):
        r = self._judge(df=None)
        self.assertTrue(r["allowed"])

    def test_empty_df_does_not_raise(self):
        r = self._judge(df=pd.DataFrame(columns=["ts", "open", "high", "low", "close",
                                                 "volume", "amount"]))
        self.assertTrue(r["allowed"])

    def test_garbage_df_does_not_raise(self):
        r = self._judge(df="this-is-not-a-dataframe")
        self.assertTrue(r["allowed"])
        self.assertIn(r["status"], ("ok", "error", "degraded", "skipped"))

    def test_prev_df_none_is_fine(self):
        """昨日数据缺失 → D6 该 input 记 None，**不是**判定失败。"""
        r = V.judge("600000", day=DAY, df=_mk_df(), prev_df=None, now=NOW, signal_ts=SIG)
        self.assertIn("low_shift_pct", r["features"])

    def test_load_prev_df_never_raises(self):
        self.assertIsNone(V.load_prev_df("999999", DAY)) or True


class SnapshotLockTests(_Base):
    """③ 当日锁定：日级稳定键 + 覆盖收盘的 TTL。"""

    def test_hash_is_day_level_stable(self):
        self.assertEqual(V.intraday_snapshot_hash("600000", DAY),
                         V.intraday_snapshot_hash("600000", DAY))

    def test_hash_ignores_intraday_features(self):
        """⚠️ 本测试锁住「不含盘中特征」这一设计 —— 若有人把 feats 加进哈希，当日锁定立刻失效。"""
        h1 = V.intraday_snapshot_hash("600000", DAY)
        h2 = V.intraday_snapshot_hash("600000", DAY)
        self.assertEqual(h1, h2)
        df2 = _mk_df(slope=-0.05, base=99.0)   # 完全不同的盘中形态
        r = self._judge(df=df2)
        self.assertEqual(r["snapshot_hash"], h1)

    def test_hash_differs_from_chain_key(self):
        """盘中键必须与盘后链的 per-code+feats 键**结构性不同**（否则会互相命中/污染）。"""
        feats = {"px_vs_vwap_pct": 0.01}
        chain_key = E._sha(dict(code="600000", day=DAY, feats=feats))
        self.assertNotEqual(V.intraday_snapshot_hash("600000", DAY), chain_key)

    def test_hash_is_day_scoped(self):
        self.assertNotEqual(V.intraday_snapshot_hash("600000", DAY),
                            V.intraday_snapshot_hash("600000", "2026-09-14"))

    def test_cache_ttl_covers_to_close(self):
        self.assertEqual(V.lock_ttl(dt.datetime(2026, 9, 11, 10, 0)), 19800.0)  # 10:00→15:30
        self.assertEqual(V.lock_ttl(dt.datetime(2026, 9, 11, 15, 29)), 60.0)    # 下限兜底

    def test_judge_passes_long_ttl_to_consult(self):
        with mock.patch.object(V.llm, "consult", return_value=_ret("B_medium")) as m:
            V.judge("600000", day=DAY, df=_mk_df(), now=NOW, signal_ts=SIG)
        self.assertEqual(m.call_args.kwargs.get("cache_ttl"), V.lock_ttl(NOW))

    def test_second_call_same_day_hits_cache_and_does_not_recall(self):
        """⭐ 当日锁定端到端：同日第二次判定命中缓存 → `_raw_chat` 调用次数仍为 1。"""
        raw = _raw_payload("B_medium")
        with mock.patch.object(V.llm, "load_config", return_value=dict(CFG)), \
             mock.patch.object(V.llm, "_raw_chat", return_value=raw) as m:
            r1 = V.judge("600000", day=DAY, df=_mk_df(), now=NOW, signal_ts=SIG)
            self.assertFalse(r1["cache_hit"])
            self.assertEqual(m.call_count, 1)
            # 20 分钟后同一标的再次触发（且盘中特征已完全变化）
            r2 = V.judge("600000", day=DAY, df=_mk_df(slope=-0.05, base=99.0),
                         now=dt.datetime(2026, 9, 11, 10, 26, 0), signal_ts=SIG)
            self.assertTrue(r2["cache_hit"])
            self.assertEqual(m.call_count, 1, "当日锁定失效：同一标的被重复真调")
            self.assertEqual(r2["choice"], r1["choice"], "判定在当日发生了翻转")


class ArtifactTests(_Base):
    """digest 契约与落盘（R4 验收：全链 provenance 完整）。"""

    def test_digest_passes_contract_validation(self):
        for choice in ("A_optimum", "B_medium", "C_reject"):
            r = self._judge(_ret(choice))
            self.assertEqual(r["digest_errors"], [], f"{choice} 的 digest 未通过 R1.6 校验")

    def test_digest_uses_r1_6_identity_fields(self):
        r = self._judge(_ret("C_reject"))
        d = r["digest"]
        self.assertEqual(d["digest_revision"], "decision-digest/v1")
        self.assertEqual(d["day"], DAY)
        self.assertEqual(d["sym"], "600000")
        self.assertTrue(d["digest_id"].startswith("dd-"))
        self.assertIn("llm_veto", d["action"]["authority"])
        self.assertEqual(d["decision"]["discretions"][0]["point_id"], "D6")

    def test_both_outcomes_are_landed(self):
        self._judge(_ret("C_reject"))
        self._judge(_ret("A_optimum"), code="600519")
        rows = [json.loads(x) for x in V._out_path(DAY).read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["allowed"] for r in rows}, {True, False})
        self.assertTrue(all(r.get("digest_id") for r in rows))

    def test_summarize_counts(self):
        self._judge(_ret("C_reject"))
        self._judge(_ret("A_optimum"), code="600519")
        self._judge(_ret("B_medium", status="degraded", degraded=True), code="000001")
        s = V.summarize(DAY)
        self.assertEqual(s["n_judged"], 3)
        self.assertEqual(s["n_vetoed"], 1)
        self.assertEqual(s["n_degraded_pass"], 1)
        self.assertEqual(s["vetoed"], ["600000"])

    def test_exception_path_still_lands_digest(self):
        """⭐ 回归锚：**异常路径也必须落 digest**。

        首版把落盘写在 try 内 → LLM 抛异常时 digest 为 None ⇒「被放行的买入无痕」，
        直接违反 R4 验收「全链 provenance 完整」（由 test_http_error_passes 抓出）。
        """
        r = self._judge(exc=TimeoutError("boom"))
        self.assertIsNotNone(r["digest"], "异常路径未落 digest：放行的买入将无痕可查")
        self.assertEqual(r["digest_errors"], [])
        rows = [json.loads(x) for x in V._out_path(DAY).read_text(encoding="utf-8").splitlines()
                if x.strip()]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["allowed"])
        self.assertTrue(rows[0]["digest_id"])

    def test_cache_hit_does_not_emit_new_digest(self):
        """⭐ 当日锁定复用**不得产新 digest**。

        否则同一决策会拿到多个 digest_id（seq 递增），而 R1.6 明文「身份用 digest_id」——
        一个决策多个身份会让审计语义失效。本缺陷由 dry-run 演练的 summarize 双计暴露。
        """
        raw = _raw_payload("C_reject")
        with mock.patch.object(V.llm, "load_config", return_value=dict(CFG)), \
             mock.patch.object(V.llm, "_raw_chat", return_value=raw):
            r1 = V.judge("600000", day=DAY, df=_mk_df(), now=NOW, signal_ts=SIG)
            r2 = V.judge("600000", day=DAY, df=_mk_df(), now=NOW, signal_ts=SIG)
        self.assertFalse(r1["cache_hit"])
        self.assertTrue(r2["cache_hit"])
        self.assertIsNotNone(r1["digest"])
        self.assertIsNone(r2["digest"], "缓存命中却产出了新 digest → 同一决策出现多个身份")
        self.assertEqual(r2["reused_digest_id"], r1["digest"]["digest_id"])
        rows = [json.loads(x) for x in V._out_path(DAY).read_text(encoding="utf-8").splitlines()
                if x.strip()]
        self.assertEqual(len([r for r in rows if r.get("kind") != "reuse"]), 1)
        self.assertEqual(len([r for r in rows if r.get("kind") == "reuse"]), 1)

    def test_summarize_deduplicates_cache_reuse(self):
        """汇总按「真调」计数，缓存复用单列 —— 防止同一标的被重复计数。"""
        raw = _raw_payload("B_medium")
        with mock.patch.object(V.llm, "load_config", return_value=dict(CFG)), \
             mock.patch.object(V.llm, "_raw_chat", return_value=raw):
            V.judge("600000", day=DAY, df=_mk_df(), now=NOW, signal_ts=SIG)
            V.judge("600000", day=DAY, df=_mk_df(), now=NOW, signal_ts=SIG)
        s = V.summarize(DAY)
        self.assertEqual(s["n_judged"], 1, "缓存复用被重复计入 n_judged")
        self.assertEqual(s["n_reuse"], 1)
        self.assertEqual(s["n_vetoed"], 0)

    def test_summarize_missing_file_is_empty(self):
        s = V.summarize("1999-01-01")
        self.assertEqual(s["n_judged"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
