# -*- coding: utf-8 -*-
"""R3.1 六段决策环引擎的契约测试。

保护四件容易退化的事：
  1. **六段齐全**（不出现 status=stub）
  2. **每段 digest 过 R1.6 validate**
  3. **重放一致性**（R3 验收核心项：不可重放率 = 0）
  4. **三条安全红线**：
     - ④ 段 **不产生 exclude / order_intent**（否决权归 D6，R3.2 之前不得表决）
     - ⑤ 段空仓返回 `no_positions` 而非异常；**不调 ledger.transact**
     - ⑥ 段用 `cost_equity` 动态权益，**payload 内不得出现 `start_cash`**（防回退旧口径）
另外把 **7/17 落标锚点**固化为回归：选手 7/17 实证「1 只票、20% 仓位」⇒ regime 必须判 bear。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


def _run(day: str, use_llm: bool = False) -> dict:
    """默认**关 LLM**跑 —— 契约测试要的是「快速 + 确定性」，
    裁量层本身由 `LlmLayerTests` 单独覆盖。"""
    from decision_chain.engine import run
    return run(day, use_llm=use_llm)


class ChainStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = _run("2026-09-11")

    def test_six_segments_present(self):
        segs = self.a["segments"]
        self.assertEqual(set(segs), {"GATE", "MAIN", "DRAGON", "ENTRY", "HOLD", "SIZE"},
                         "六段必须齐全")
        stubbed = [k for k, v in segs.items() if v["status"] == "stub"]
        self.assertEqual(stubbed, [], f"仍存在 stub 段：{stubbed}")

    def test_all_digests_valid(self):
        for seg, s in self.a["segments"].items():
            self.assertEqual(s["digest_errors"], [], f"{seg} digest 未过 validate")
            self.assertTrue(self.a["digests"][seg]["digest_id"])

    def test_chain_hash_present(self):
        self.assertTrue(self.a["meta"]["chain_replay_hash"])

    def test_shadow_mode(self):
        self.assertEqual(self.a["meta"]["mode"], "shadow")

    def test_segments_no_data_is_not_crash(self):
        """非交易日/老日期（产物不存在）应返回 no_data，而非抛异常。"""
        b = _run("2026-07-17")
        self.assertIn(b["segments"]["ENTRY"]["status"], ("ok", "no_data"))


def _code_only(body: str) -> str:
    """剥掉函数体的 **docstring** 后返回代码部分。

    ⚠️ 为什么必须剥：本文件要断言「不得出现 `transact` / `start_cash`」，
    而这两条的**解释性 docstring 里必然会出现这两个词**（「不得调用 `ledger.transact`」、
    「禁用 `state['start_cash']`」）→ 不剥就会把断言打成假失败。
    （本测试首版连踩两次：先命中 `seg_hold` 的 docstring，再命中 `seg_size` 的 docstring。）
    """
    i = body.find('"""')
    if i == -1:
        return body
    j = body.find('"""', i + 3)
    return body[:i] + (body[j + 3:] if j != -1 else "")


class SafetyRedlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = _run("2026-09-11")
        cls.src = (ROOT / "src" / "decision_chain" / "engine.py").read_text(encoding="utf-8")

    def _fn_code(self, name: str) -> str:
        return _code_only(self.src.split(f"def {name}", 1)[1].split("\ndef ", 1)[0])

    def test_entry_exclusion_only_from_d6(self):
        """⭐ R3.2 后 ④ 段的核心契约：**排除只能来自 D6 的 `C_reject`**。

        - 关 LLM 时：`produces_exclude=False`，**不得有任何排除**
        - 开 LLM 时：`produces_exclude=True`，且每一只被排除的标的，
          其 `d6.choice` 必须恰好是 `C_reject`（不得由机械式 `px<vwap` 直接排除）
        """
        p = self.a["segments"]["ENTRY"]["payload"]
        self.assertFalse(p.get("produces_exclude"), "--no-llm 时不得产生排除")
        self.assertEqual(p.get("n_excluded"), 0, "--no-llm 时排除数必须为 0")
        self.assertEqual(p.get("veto_owner"), "D6", "否决权必须显式归 D6")
        # 机械事实可以非零，但它本身不构成排除
        self.assertIsInstance(p.get("n_break_below_vwap_fact"), int)

        src = (ROOT / "src" / "decision_chain" / "engine.py").read_text(encoding="utf-8")
        code = _code_only(src.split("def seg_entry", 1)[1].split("\ndef ", 1)[0])
        self.assertIn('r.get("choice") == llm.POINTS["D6"]["veto_choice"]', code,
                      "排除的判据必须是 D6 的 veto_choice，而非机械 px<vwap")

    def test_hold_empty_positions_is_ok(self):
        p = self.a["segments"]["HOLD"]["payload"]
        self.assertIn(p.get("status"), ("ok", "no_positions"))
        if p.get("status") == "no_positions":
            self.assertEqual(p.get("n_positions"), 0)

    def test_hold_is_read_only(self):
        """**行为断言**：跑完整条链后，账本文件字节必须**一字未变**（shadow 不得写账）。

        ⚠️ 不用「源码里不得出现 transact」那类文本断言 —— 解释该禁令的 docstring 里必然出现
        `transact` 一词，会把断言打成假失败（本测试连踩三次后改为行为断言，同时也更强：
        它能抓住任何形式的写账，而不只是 `transact(` 这一种写法）。
        """
        sys.path.insert(0, str(ROOT / "portfolio"))
        from ledger import LEDGER
        p = pathlib.Path(LEDGER)
        before = p.read_bytes()
        _run("2026-09-11")
        after = p.read_bytes()
        self.assertEqual(before, after, "⑤ 段（或整条链）改动了账本 —— shadow 必须只读")
        self.assertIn("executed=False", (ROOT / "src" / "decision_chain" / "engine.py")
                      .read_text(encoding="utf-8"))

    def test_size_uses_dynamic_equity_not_start_cash(self):
        """**行为断言**：⑥ 段报出的 equity 必须等于独立算出的 `cost_equity(state)`。

        这样比「源码里不得出现 start_cash」更本质：即便有人用 `start_cash` 凑出同样数字，
        只要账户权益偏离初始本金，本断言就会失败。
        """
        sys.path.insert(0, str(ROOT / "portfolio"))
        from ledger import LEDGER, cost_equity
        state = json.loads(pathlib.Path(LEDGER).read_text(encoding="utf-8"))
        expect = float(cost_equity(state))
        p = self.a["segments"]["SIZE"]["payload"]
        self.assertAlmostEqual(p["equity"], expect, places=2,
                              msg="⑥ 段权益口径必须与 ledger.cost_equity 完全同源")
        self.assertIn("cost_equity", p.get("equity_source", ""))

    def test_size_cap_within_sop_bands(self):
        p = self.a["segments"]["SIZE"]["payload"]
        c = p["cap"]
        self.assertIn(p["regime"], ("bear", "neutral", "bull"))
        if p["regime"] == "bear":
            self.assertTrue(c["hi"] <= 0.20)
        elif p["regime"] == "bull":
            self.assertTrue(0.50 <= c["lo"] and c["hi"] <= 0.70)
        else:
            self.assertTrue(0.30 <= c["lo"] and c["hi"] <= 0.50)

    def test_size_marks_composed_uncalibrated(self):
        p = self.a["segments"]["SIZE"]["payload"]
        self.assertTrue(p["composed"], "三档合成规则由本增量提出，必须标 composed")
        self.assertFalse(p["calibrated"], "整体未标定，必须标 calibrated=false")


class ReplayTests(unittest.TestCase):
    def test_replay_identical(self):
        """R3 验收核心项：给定快照可重放同一输出（不可重放率 = 0）。"""
        a, b = _run("2026-09-11"), _run("2026-09-11")
        self.assertEqual(a["meta"]["chain_replay_hash"], b["meta"]["chain_replay_hash"])
        drift = [s for s in a["digests"] if a["digests"][s]["replay_hash"] != b["digests"][s]["replay_hash"]]
        self.assertEqual(drift, [], f"分段 replay hash 漂移：{drift}")

    def test_replay_identical_three_rounds(self):
        """⭐ 2026-09-14 加固：连跑 **3 次** —— 原 flake 是「前两次相同、后两次不同」，
        两轮比对抓不到。根因见 `ReplayTests::test_dragon_hits_no_lookahead` 的 docstring。"""
        hs = [_run("2026-09-11")["meta"]["chain_replay_hash"] for _ in range(3)]
        self.assertEqual(len(set(hs)), 1, f"chain_replay_hash 三次不一致：{[h[:12] for h in hs]}")

    def test_dragon_hits_no_lookahead(self):
        """⭐ 2026-09-14 修复锚：**DRAGON 段的形态命中日不得晚于 replay 日**。

        根因（实测）：`seg_dragon` 原先直接取 `daily_rebuilt/{sym}.parquet` 的**最新一根**
        （`S.detect(df, name)` 只看 `ser.iloc[-1]` / `df.iloc[-1]`），而该目录被收盘链与
        minute-snapshot 任务**持续更新** ⇒ 两重后果：
          ① **前视**：replay 2026-09-11 却得到 `date=2026-09-14` 的 hits；
          ② **不确定性**：同一天内多次 run 读到不同的「最新一根」⇒ `chain_replay_hash` 漂移
             ⇒ `test_replay_identical` 间歇失败（实测 30 passed / 1 failed 交替）。
        修法：读入后先按 `day` 截断（`df[df["date"] <= day]`），与 `core.pattern_pool` 的 asof 口径一致。
        """
        a = _run("2026-09-11")
        hits = a["segments"]["DRAGON"]["payload"].get("hits") or []
        bad = [h for h in hits if str(h.get("date", "")) > "2026-09-11"]
        self.assertEqual(bad, [], f"DRAGON 命中含晚于 replay 日的 bar（前视）：{bad[:3]}")
        self.assertTrue(hits, "2026-09-11 应有形态命中（否则本条断言无意义）")


class CandidateUnionSchemaTests(unittest.TestCase):
    """⭐ 2026-09-14 守卫：`candidate_union` 必须兼容 confirm 快照的**新旧两种 schema**。

    背景：当日把 `scan_and_confirm` 的快照字段 `pool` 拆成 `movers`（异动池）/ `confirm_queue`
    （准入层）。`candidate_union` 原先只读 `pool` ⇒ 新 schema 下**静默返回空并集**，
    DRAGON 段的「候选池」凭空消失且不报错。本测试锁死兼容性。
    """

    def _union(self, doc):
        import tempfile
        sys.path.insert(0, str(ROOT / "scripts"))
        import minute_incremental_snapshot as MIS
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td)
            (p / "confirm_20260911_1000.json").write_text(
                json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            old = MIS.INTRADAY
            MIS.INTRADAY = p
            try:
                return MIS.candidate_union("2026-09-11")
            finally:
                MIS.INTRADAY = old

    def test_new_schema_confirm_queue(self):
        syms, diag = self._union({"candidates_snapshot": {
            "confirm_queue": [{"sym": "600354"}, {"sym": "002436"}]}})
        self.assertEqual(syms, ["002436", "600354"], "新 schema 的 confirm_queue 未被读到")

    def test_new_schema_movers_fallback(self):
        syms, _ = self._union({"candidates_snapshot": {"movers": [{"sym": "600354"}]}})
        self.assertEqual(syms, ["600354"], "新 schema 的 movers 回退未生效")

    def test_new_schema_prefers_confirm_queue_over_movers(self):
        syms, _ = self._union({"candidates_snapshot": {
            "confirm_queue": [{"sym": "002436"}], "movers": [{"sym": "600354"}]}})
        self.assertEqual(syms, ["002436"], "confirm_queue 应优先于 movers")

    def test_old_schema_still_works(self):
        syms, _ = self._union({"candidates_snapshot": {"pool": [{"sym": "600354"}]}})
        self.assertEqual(syms, ["600354"], "旧 schema（pool）兼容被破坏")


class RegimeAnchorTests(unittest.TestCase):
    """⭐ 落标锚点：把选手行为实证固化为回归。

    依据：`选手战法画像-累计.md:885` 与 `20260717_至暗时刻三条纪律/record.md:47`
    记载 2026-07-17 大跌日选手**持仓仅 1 只、20% 仓位**，与「熊市 ≤20%」原则吻合。
    → 若引擎在该日算出的 regime 不是 bear，说明判据与选手行为不符，必须回改判据。
    """

    @classmethod
    def setUpClass(cls):
        cls.a = _run("2026-07-17")

    def test_0717_is_bear(self):
        g = self.a["segments"]["GATE"]["payload"]
        s = self.a["segments"]["SIZE"]["payload"]
        self.assertEqual(g.get("status"), "ok", "7/17 情绪表须有数据")
        self.assertTrue(g["features"]["is_bingdian"],
                        f"7/17 应判冰点（zt={g['features']['zt']} dt={g['features']['dt']}）")
        self.assertEqual(s.get("regime"), "bear",
                         "⭐ 锚点失败：7/17 选手实证为熊市（1 只票/20% 仓位），引擎必须判 bear")

    def test_0717_cap_le_20pct(self):
        s = self.a["segments"]["SIZE"]["payload"]
        self.assertTrue(s["cap"]["hi"] <= 0.20, "7/17 cap 上限必须 ≤20%")


class LlmLayerTests(unittest.TestCase):
    """R3.2 裁量层：schema 严校验 + 降级保守档 + 协议落盘字段。

    ⚠️ 只有 `test_live_call_and_schema` 会真联网；其余是**纯函数**断言，离线也过。
    """

    def setUp(self):
        from decision_chain import llm
        self.llm = llm

    # ── schema（协议：枚举 + 0-1 评分 + ≤N 字理由；输出必须 100% 过校验）
    def test_schema_accepts_legal(self):
        out = {"discretion_id": "D6", "choice": "A_optimum", "score": 0.8,
               "reason": "有量且逐步上移", "confidence": 0.7}
        self.assertEqual(self.llm.validate_output("D6", out), [])

    def test_schema_rejects_choice_out_of_enum(self):
        out = {"discretion_id": "D6", "choice": "D_buy_now", "score": 0.5, "reason": "x"}
        self.assertTrue(any("枚举" in e for e in self.llm.validate_output("D6", out)))

    def test_schema_rejects_score_out_of_range(self):
        for bad in (-0.1, 1.1, "0.5"):
            out = {"discretion_id": "D6", "choice": "B_medium", "score": bad, "reason": "x"}
            self.assertTrue(self.llm.validate_output("D6", out), f"score={bad!r} 应被拒")

    def test_schema_rejects_overlong_reason(self):
        out = {"discretion_id": "D6", "choice": "B_medium", "score": 0.5,
               "reason": "x" * (self.llm.REASON_MAX + 1)}
        self.assertTrue(any("reason" in e for e in self.llm.validate_output("D6", out)))

    def test_schema_rejects_extra_keys(self):
        out = {"discretion_id": "D6", "choice": "B_medium", "score": 0.5, "reason": "x",
               "order": {"side": "buy", "qty": 100}}
        self.assertTrue(any("未允许的键" in e for e in self.llm.validate_output("D6", out)))

    def test_schema_rejects_free_text_order(self):
        """协议禁止自由文本下单：不得出现未允许的键（如 side/qty/px）。"""
        out = {"discretion_id": "D6", "choice": "B_medium", "score": 0.5, "reason": "x",
               "side": "buy", "qty": 1000}
        errs = self.llm.validate_output("D6", out)
        self.assertTrue(any("未允许的键" in e for e in errs))

    # ── 降级（协议第 6 条：硬规则 + 保守默认档）
    def test_conservative_defaults_are_safe(self):
        d6 = self.llm.conservative_default("D6", "test")
        # D6 的保守档必须是"可观察不优先"，**不是**"弃" ——
        # 因为 GEN-ENTRY-04 在收益上已被证伪，退化时不该用被证伪的判据去否决
        self.assertEqual(d6["choice"], "B_medium")
        self.assertNotEqual(d6["choice"], self.llm.POINTS["D6"]["veto_choice"])
        d8 = self.llm.conservative_default("D8", "test")
        self.assertEqual(d8["choice"], "floor", "仓位降级必须落到区间下沿（少下注）")

    def test_no_config_degrades_not_raises(self):
        r = self.llm.consult("D8", date="2026-09-11", obs={}, snapshot_hash="x0",
                             no_cache=True)
        self.assertIn(r["status"], ("ok", "degraded"))
        if r["status"] == "degraded":
            self.assertEqual(r["choice"], "floor")

    # ── prompt 必须含禁令与 schema 约束（协议：禁止自由文本下单/覆盖硬规则）
    def test_prompt_contains_bans_and_schema(self):
        pr = self.llm.build_prompt("D6", obs={"px_vs_vwap_pct": -0.01}, sop=None)
        for kw in ("禁止", "枚举", "JSON", "不超过"):
            self.assertIn(kw, pr)
        self.assertIn("A_optimum", pr)


@unittest.skipUnless(
    (pathlib.Path.home() / ".workbuddy" / "models.json").exists(),
    "无 ~/.workbuddy/models.json，跳过联网裁量测试")
class LlmLiveTests(unittest.TestCase):
    """真实调用一次，验证：**输出 100% 过 schema** + 协议要求的落盘字段齐全。"""

    def test_live_call_schema_and_persistence(self):
        from decision_chain import llm
        obs = {"px_vs_vwap_pct": -0.012, "up_down_vol_ratio": 0.42,
               "low_shift_pct": -0.03, "amplitude_pct": 6.1,
               "drawdown_vs_vwap_pct": -2.3, "tail_above_vwap": False}
        r = llm.consult("D6", date="2026-09-11", obs=obs, snapshot_hash="pytest-live-1",
                        no_cache=True)
        self.assertIn(r["status"], ("ok", "degraded"))
        if r["status"] == "ok":
            self.assertEqual(llm.validate_output("D6", {k: r[k] for k in
                             ("discretion_id", "choice", "score", "reason")}), [])
            self.assertIn(r["choice"], llm.POINTS["D6"]["choices"])
        # 协议落盘字段：模板版本 / 模型名 / 快照 hash / 耗时 / tokens
        d = ROOT / "outputs" / "decision_chain" / "llm" / "2026-09-11"
        self.assertTrue(d.exists(), "裁量记录必须落盘")
        recs = [json.loads(p.read_text(encoding="utf-8")) for p in d.glob("D6__*.json")]
        self.assertTrue(recs)
        last = recs[-1]
        for k in ("prompt_template_version", "model", "snapshot_hash", "prompt_hash"):
            self.assertIn(k, last, f"协议要求落盘 {k}")


class ShadowReportTests(unittest.TestCase):
    """R3.3 双轨 shadow：**分类正确性 + 计数纪律**。

    ⭐ 全部离线：`shadow.compare(..., evolved=<注入载荷>)` 允许不跑 ④ 段 /
    不调 LLM 就验证分类逻辑（这也是当初给 `compare` 加 `evolved` 参数的原因）。
    """

    def setUp(self):
        from decision_chain import shadow
        self.sh = shadow

    def _ev(self, items):
        return dict(status="ok", scope="fake", items=items)

    def _row(self, code, excluded, d6=None, mech=None):
        return dict(code=code, excluded=excluded, d6=d6, break_below_vwap=mech)

    def test_verdict_classification(self):
        """四类主判定 + UNKNOWN + 无分钟，逐类断言。

        ⭐ 并**显式钉住判定优先级**（2026-09-12 修正）：
        `player_unknown`（选手侧未说明原因）**优先于** `no_minute`（我们没数据）——
        前者是关于**选手披露质量**的属性，与我们有没有数据无关；两者都不计分，
        但分桶必须准确，否则「选手未说明原因」的例数会被数据缺口吞掉。
        """
        players = [
            dict(code="111111", name="A", action="BUY", why=""),
            dict(code="222222", name="B", action="BUY", why=""),
            dict(code="333333", name="C", action="VETO", why=""),
            dict(code="444444", name="D", action="VETO", why=""),
            dict(code="555555", name="E", action="UNKNOWN", why=""),   # 无数据 + UNKNOWN
            dict(code="666666", name="F", action="BUY", why=""),       # 无数据 + 非 UNKNOWN
        ]
        ev = self._ev([
            self._row("111111", False),      # BUY + keep  → agree_keep
            self._row("222222", True),       # BUY + excl  → miss（漏选）
            self._row("333333", True),       # VETO + excl → agree_veto
            self._row("444444", False),      # VETO + keep → false_keep（误留）
            # 555555 / 666666 不在 items → absent
        ])
        with mock.patch.object(self.sh, "load_player", return_value=players):
            r = self.sh.compare("2026-09-11", evolved=ev, universe="player")
        got = {x["code"]: x["verdict"] for x in r["rows"]}
        self.assertEqual(got["111111"], "agree_keep")
        self.assertEqual(got["222222"], "miss")
        self.assertEqual(got["333333"], "agree_veto")
        self.assertEqual(got["444444"], "false_keep")
        # ⭐ 优先级：UNKNOWN 胜过「无数据」
        self.assertEqual(got["555555"], "player_unknown", "UNKNOWN 应优先于 no_minute")
        self.assertEqual(got["666666"], "no_minute", "非 UNKNOWN 且无数据才是 no_minute")
        s = r["summary"]
        # ⭐ 纪律：UNKNOWN 与 no_minute **都不计入分母** → 分母只有 4
        self.assertEqual(s["n_scored"], 4, "UNKNOWN / no_minute 不得进分母")
        self.assertEqual(s["n_agree"], 2)
        self.assertEqual(s["agree_rate"], 0.5)
        self.assertEqual(s["n_miss"], 1)
        self.assertEqual(s["n_false_keep"], 1)
        self.assertEqual(s["n_player_unknown"], 1)
        self.assertEqual(s["n_no_minute"], 1)

    def test_player_unknown_never_scored(self):
        """⭐ `UNKNOWN`＝未执行但未说明原因 → **绝不计分**（否则把资金/闸门误判为分时否决）。"""
        players = [dict(code=f"{i}" * 6, name=str(i), action="UNKNOWN", why="") for i in range(1, 6)]
        with mock.patch.object(self.sh, "load_player", return_value=players):
            r = self.sh.compare("2026-09-11", evolved=self._ev([]), universe="player")
        self.assertEqual(r["summary"]["n_scored"], 0)
        self.assertIsNone(r["summary"]["agree_rate"], "无样本时一致率应为 None，不是 0（不得假装 0%）")
        self.assertEqual(r["summary"]["n_player_unknown"], 5)

    def test_discipline_report_only(self):
        """⭐ 报告必须**自带**「只报告、不作晋级」声明 —— 防止数字被误用为晋级依据。"""
        with mock.patch.object(self.sh, "load_player", return_value=[]):
            r = self.sh.compare("2026-09-11", evolved=self._ev([]), universe="player")
        self.assertIn("不作晋级", r["discipline"])
        agg = self.sh.aggregate([r])
        self.assertIn("不作晋级", agg["discipline"])

    def test_verdict_spec_consistency(self):
        """分类表自洽：`scored=True` 的桶必须有布尔 `ok`；`scored=False` 的必须 `ok is None`。"""
        for k, v in self.sh.VERDICT_SPEC.items():
            if v["scored"]:
                self.assertIn(v["ok"], (True, False), f"{k} 必须给出 ok 布尔")
            else:
                self.assertIsNone(v["ok"], f"{k} 不计分就不该有成败判定")

    def test_no_evolved_data_is_graceful(self):
        """无 EvoAlpha 侧数据（如老日期无分钟/无候选池）→ 返回 no_evolved_data，不抛异常。"""
        r = self.sh.compare("2020-01-02", use_llm=False, universe="player")
        self.assertIn(r["status"], ("ok", "no_evolved_data"))
        if r["status"] == "no_evolved_data":
            self.assertEqual(r["rows"], [])

    def test_player_labels_registry_intact(self):
        """选手标签库的日期集合（防误删/误改 —— 它是诊断轨的**唯一**选手侧真相源）。"""
        from iteration.intraday import DECISION_CASES
        expect = {"2026-08-31", "2026-09-02", "2026-09-03", "2026-09-04",
                  "2026-09-07", "2026-09-09", "2026-09-10"}
        self.assertTrue(expect.issubset(set(DECISION_CASES)),
                        f"缺失日期: {expect - set(DECISION_CASES)}")
        n = sum(len(v) for v in DECISION_CASES.values())
        self.assertGreaterEqual(n, 25, "案例总数不应减少")
        # action 只能是三值之一
        acts = {row[2] for v in DECISION_CASES.values() for row in v}
        self.assertTrue(acts.issubset({"BUY", "VETO", "UNKNOWN"}), f"出现未知 action: {acts}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
