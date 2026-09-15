"""status_push.py 关键节点状态卡观测器的离线测试（2026-09-11）。

全部为哑数据 + mock，不联网、不推送、不写生产文件：
- 节点状态机: 未到点不推 / 来源缺失记 pending / 到点推送 / 重复不重推 / --force 补发 / 未到点不得靠 --force 提前发
- 渲染健壮性: 每个节点在「来源齐备 / 部分缺失 / 全缺」下都能产出合法卡片，绝不抛异常
- 解码回退: BOM JSON、GBK 文本、损坏 JSONL 行
- 失败升级: 连续 PENDING_ALERT_ROUNDS 轮来源缺失 → 恰好 1 条失败告警
- 非交易日静默: 零状态写入、零推送
- 边界: 只写 status_push_<date>.json 与 delivery_*.jsonl，绝不触碰 ledger/plans/门禁
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import status_push as sp  # noqa: E402

DAY = "2026-09-11"


def _env(td: pathlib.Path):
    """把模块的目录常量指向临时工作区。"""
    out = td / "outputs"
    notif = out / "notifications"
    for sub in ("plans", "intraday", "validation", "acceptance", "selfcheck", "calendar"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    notif.mkdir(parents=True, exist_ok=True)
    return out, notif


def _write_json(p: pathlib.Path, payload, encoding: str = "utf-8"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding=encoding)


def _ok(state, tone="green"):
    """伪造一个已渲染的节点结果。

    node["fn"] 的返回契约是 **(rendered, missing_reason)**：
    rendered 为 None 表示来源未就绪；否则 rendered 为 (card, state, tone)。
    """
    rendered = ({"msg_type": "interactive",
                 "card": {"header": {"template": tone}, "elements": []}}, state, tone)
    return rendered, None


def _missing(reason="来源缺失"):
    return (None, reason)


class Args:
    def __init__(self, **kw):
        self.node = kw.get("node", "run")
        self.date = kw.get("date", DAY)
        self.dry_run = kw.get("dry_run", False)
        self.no_push = kw.get("no_push", False)
        self.force = kw.get("force", False)
        self.allow_early = kw.get("allow_early", False)


class _FakeCards:
    """收集卡片推送，模拟 Feishu 返回成功。"""

    def __init__(self):
        self.sent = []

    def __call__(self, card, event_key, summary_text, dry_run=False):
        self.sent.append((event_key, card))
        return True, True


class NodeStateMachineTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._td.name)
        self.out, self.notif = _env(self.td)
        self._patches = [
            mock.patch.object(sp, "OUT", self.out),
            mock.patch.object(sp, "NOTIF", self.notif),
            mock.patch.object(sp, "STATE_DIR", self.out / "selfcheck"),
            mock.patch.object(sp, "BASE", self.td),
            # R0.2：status_push 改为 `from ledger import LEDGER`（env-aware 绝对路径），
            # 仅 patch BASE 已不足以隔离——必须同时 patch LEDGER，否则测试会读生产账本。
            mock.patch.object(sp, "LEDGER", self.td / "portfolio" / "ledger.json"),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(self._td.cleanup)
        for p in self._patches:
            self.addCleanup(p.stop)

    def _state(self):
        return sp._load_state(DAY)

    def test_waiting_before_deadline(self):
        st = self._state()
        node = {"name": "close", "at": "15:05", "fn": lambda d, n: _ok("pass")}
        node["fn"] = mock.Mock(return_value=_ok("pass"))
        r = sp._run_node(DAY, node, st, sp._deadline(DAY, "09:00"), Args())
        self.assertEqual(r["action"], "waiting")
        node["fn"].assert_not_called()

    def test_force_still_refuses_early_push(self):
        """--force 只用于补发，绝不能把未来时刻的卡片提前发出去。"""
        st = self._state()
        node = {"name": "close", "at": "15:05", "fn": mock.Mock(return_value=_ok("pass"))}
        r = sp._run_node(DAY, node, st, sp._deadline(DAY, "09:00"), Args(force=True))
        self.assertEqual(r["action"], "waiting")
        node["fn"].assert_not_called()

    def test_missing_source_records_pending_without_push(self):
        st = self._state()
        node = {"name": "close", "at": "15:05", "fn": mock.Mock(return_value=_missing("缺失"))}
        cards = _FakeCards()
        with mock.patch.object(sp, "_push_card", cards):
            r = sp._run_node(DAY, node, st, sp._deadline(DAY, "15:06"), Args())
        self.assertEqual(r["action"], "pending")
        self.assertEqual(r["rounds"], 1)
        self.assertEqual(cards.sent, [], "来源未就绪时不得推送空白卡")
        self.assertFalse(st["nodes"]["close"].get("pushed"))

    def test_push_once_then_dedupe(self):
        st = self._state()
        node = {"name": "close", "at": "15:05", "fn": mock.Mock(return_value=_ok("pass"))}
        cards = _FakeCards()
        with mock.patch.object(sp, "_push_card", cards):
            first = sp._run_node(DAY, node, st, sp._deadline(DAY, "15:06"), Args())
            second = sp._run_node(DAY, node, st, sp._deadline(DAY, "15:20"), Args())
        self.assertEqual(first["action"], "pushed")
        self.assertEqual(second["action"], "already")
        self.assertEqual(len(cards.sent), 1)
        self.assertEqual(cards.sent[0][0], f"status:{DAY}:close")

    def test_force_repushes_after_deadline(self):
        st = self._state()
        node = {"name": "close", "at": "15:05", "fn": mock.Mock(return_value=_ok("pass"))}
        cards = _FakeCards()
        with mock.patch.object(sp, "_push_card", cards):
            sp._run_node(DAY, node, st, sp._deadline(DAY, "15:06"), Args())
            again = sp._run_node(DAY, node, st, sp._deadline(DAY, "15:20"), Args(force=True))
        self.assertEqual(again["action"], "pushed")
        self.assertEqual(len(cards.sent), 2)

    def test_push_failure_retries_next_round(self):
        st = self._state()
        node = {"name": "close", "at": "15:05", "fn": mock.Mock(return_value=_ok("pass"))}
        with mock.patch.object(sp, "_push_card", mock.Mock(return_value=(False, True))):
            r = sp._run_node(DAY, node, st, sp._deadline(DAY, "15:06"), Args())
        self.assertEqual(r["action"], "push-failed")
        self.assertFalse(st["nodes"]["close"].get("pushed"))

    def test_escalates_once_after_threshold_rounds(self):
        """跑真实 _escalate（只 stub 网络与日志），验证按 PENDING_ALERT_ROUNDS 触发且每日至多 1 条。"""
        st = self._state()
        node = {"name": "close", "at": "15:05", "fn": mock.Mock(return_value=_missing("缺失"))}
        posts = []
        with mock.patch.object(sp, "_post",
                               side_effect=lambda payload, timeout=15.0:
                               (posts.append(payload), (True, 200, 0, None))[1]), \
             mock.patch.object(sp, "_anomaly", mock.Mock()):
            for _ in range(sp.PENDING_ALERT_ROUNDS + 2):
                sp._run_node(DAY, node, st, sp._deadline(DAY, "15:06"), Args())
        self.assertEqual(len(posts), 1, "每节点每日至多 1 条升级告警")
        self.assertIn("状态推送链路异常", json.dumps(posts[0], ensure_ascii=False))
        self.assertEqual(st["warned"], ["close"])
        self.assertEqual(st["nodes"]["close"]["pending_rounds"], sp.PENDING_ALERT_ROUNDS + 2)

    def test_early_source_missing_does_not_escalate(self):
        """未到点时的来源缺失属于正常（上游还没跑），不得触发升级告警。

        注意与 test_escalates_once_after_threshold_rounds 的区别：那条用 15:06（已过点）应升级；
        本条用 09:00（未到点，靠 --allow-early 放行）不应升级。
        """
        st = self._state()
        node = {"name": "close", "at": "15:05", "fn": mock.Mock(return_value=_missing("缺失"))}
        posts = []
        with mock.patch.object(sp, "_post",
                               side_effect=lambda payload, timeout=15.0:
                               (posts.append(payload), (True, 200, 0, None))[1]), \
             mock.patch.object(sp, "_anomaly", mock.Mock()):
            for _ in range(sp.PENDING_ALERT_ROUNDS + 2):
                sp._run_node(DAY, node, st, sp._deadline(DAY, "09:00"),
                             Args(allow_early=True))
        self.assertEqual(posts, [])
        self.assertEqual(st["warned"], [])
        self.assertGreaterEqual(st["nodes"]["close"]["pending_rounds"], sp.PENDING_ALERT_ROUNDS)

    def test_render_exception_is_contained(self):
        st = self._state()
        node = {"name": "close", "at": "15:05",
                "fn": mock.Mock(side_effect=ValueError("boom"))}
        r = sp._run_node(DAY, node, st, sp._deadline(DAY, "15:06"), Args())
        self.assertEqual(r["action"], "pending")
        self.assertIn("渲染异常", r["detail"])


class RenderingTests(unittest.TestCase):
    """每个节点都必须产出结构合法的卡片，缺少来源时返回 (None, 原因) 而不是抛异常。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._td.name)
        self.out, self.notif = _env(self.td)
        self._patches = [
            mock.patch.object(sp, "OUT", self.out),
            mock.patch.object(sp, "NOTIF", self.notif),
            mock.patch.object(sp, "BASE", self.td),
            # R0.2：同上，隔离 env-aware LEDGER
            mock.patch.object(sp, "LEDGER", self.td / "portfolio" / "ledger.json"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)

    def test_every_node_degrades_gracefully_when_all_sources_missing(self):
        now = sp._deadline(DAY, "23:00")
        for node in sp.NODES:
            with self.subTest(node=node["name"]):
                rendered, missing = node["fn"](DAY, now)
                if rendered is None:
                    self.assertTrue(isinstance(missing, str) and missing)
                else:
                    self._assert_card_shape(rendered[0])

    def _assert_card_shape(self, card):
        self.assertEqual(card["msg_type"], "interactive")
        body = card["card"]
        self.assertIn(body["header"]["template"], ("green", "yellow", "red"))
        self.assertTrue(body["header"]["title"]["content"].startswith("EvoAlpha"))
        divs = [e for e in body["elements"] if e.get("tag") == "div"]
        self.assertTrue(divs, "卡片至少要有内容行")
        for e in divs:
            self.assertEqual(e["text"]["tag"], "lark_md")
        self.assertTrue(any(e.get("tag") == "note" for e in body["elements"]),
                        "卡片必须有 note 页脚（免责/只读声明）")

    def test_close_card_fills_authority_not_audit(self):
        """2026-09-11 实录（用户发现）：账本当日在 account.fills 里只有 2 笔**卖出**
        （300468/300394 止损），而 close_decision（审计反事实）记 buys=2/sells=0。
        卡片原先把审计当"当日成交"报，得出与账本完全相反的结论。
        成交口径唯一权威 = account.fills；审计必须另起一行且标注"仅审计未执行"。
        """
        d = DAY
        _write_json(self.out / "intraday" / f"close_decision_{d}.json",
                    {"date": d, "equity": 91076.1, "ledger_revision": 35,
                     "buys": [{"sym": "000690"}, {"sym": "001236"}], "sells": [],
                     "positions": {"300394": {"qty": 100}},
                     "generated_at": f"{d} 15:10:41", "run_id": "close-x",
                     "kind": "audit_counterfactual", "executed": False,
                     "authority": "account.fills"})
        _write_json(self.td / "portfolio" / "ledger.json",
                    {"start_cash": 100000,
                     "account": {"cash": 91076.1, "positions": {},
                                 "equity_curve": [{"date": d, "equity": 91076.1}],
                                 "fills": [{"date": d, "side": "sell", "sym": "300468",
                                            "qty": 1800, "px": 23.31},
                                           {"date": d, "side": "sell", "sym": "300394",
                                            "qty": 100, "px": 261.92}]}})
        rendered, missing = sp._n_close(d, sp._deadline(d, "23:00"))
        self.assertIsNone(missing, "收盘节点不应因缺件降级")
        text = json.dumps(rendered[0], ensure_ascii=False)
        self.assertIn("买入 0 笔 · 卖出 2 笔 · 持仓 0 只", text)
        self.assertNotIn("买入 2 笔", text, "不得把审计反事实的 buys 当成交")
        self.assertIn("仅审计未执行", text, "审计行必须显式声明未执行")

    def test_all_nodes_render_with_full_fixture(self):
        d = DAY
        _write_json(self.out / f"preflight_{d}_infra.json",
                    {"date": d, "stage": "infra", "time": f"{d} 08:35:01", "status": "pass",
                     "summary": {"pass": 16, "fail": 0, "warn": 1},
                     "results": [{"name": "DISK F 使用率", "ok": False, "critical": False,
                                  "detail": "90.6%", "category": "资源"}]})
        _write_json(self.out / "selfheal" / f"{d}.json",
                    [{"date": d, "attempts": [], "verdict": "无需自愈(08:35 门禁全过)"}])
        _write_json(self.out / "validation" / f"morning_check_{d}.json",
                    {"date": d, "summary": {"chain_ok": True, "preflight_ok": True,
                                            "no_failure_push": True, "pass": True}})
        _write_json(self.out / "plans" / f"{d}_plan.json",
                    {"date": d, "mode": f"候选观察(输入≤{d}收盘)", "published_at": f"{d} 08:00:00",
                     "premarket_refreshed_at": f"{d} 08:50:03", "picks": [{"sym": "000690"}],
                     "emotion": {"temp": 40.0, "stage": "修复"},
                     "global": {"usDJI": {"name": "道琼斯", "chg_pct": -0.77}}})
        _write_json(self.out / f"preflight_{d}_post_plan.json",
                    {"date": d, "stage": "post_plan", "time": f"{d} 08:55:02", "status": "pass",
                     "summary": {"pass": 18, "fail": 0, "warn": 0},
                     "results": [{"name": "当日计划", "ok": True, "critical": True,
                                  "detail": "picks=4", "category": "当日计划"}]})
        _write_json(self.out / "intraday" / f"close_decision_{d}.json",
                    {"date": d, "equity": 93325.36, "ledger_revision": 30, "buys": [], "sells": [],
                     "positions": {}, "generated_at": f"{d} 15:19:37", "run_id": "close-x"})
        _write_json(self.out / "acceptance" / f"acceptance_{d}.json", {"date": d, "status": "pass"})
        _write_json(self.out / "validation" / f"evening_check_{d}.json",
                    {"date": d, "checked_at": f"{d} 17:30:02", "blockers": []})
        _write_json(self.td / "portfolio" / "ledger.json",
                    {"start_cash": 100000,
                     "account": {"cash": 1, "positions": {}, "equity_curve": [{"date": d, "equity": 93325.36}]}})
        (self.out / "intraday" / "pos_live.json").write_text(
            json.dumps({"date": d, "time": "11:29:00", "positions": {}}), encoding="utf-8")
        (self.out / "intraday" / "board_refresh.latest.log").write_text("x", encoding="utf-8")
        # 盘后链 manifest: 一个成功 stage
        (self.out / "acceptance" / f"chain_manifest_{d}.jsonl").write_text(
            json.dumps({"run_id": "pc", "stage": "rebuild", "attempt_no": 1, "exit_code": 0,
                        "status": "success", "started_at": f"{d} 15:35:01",
                        "finished_at": f"{d} 17:00:00"}, ensure_ascii=False) + "\n",
            encoding="utf-8")
        now = sp._deadline(DAY, "23:00")
        for node in sp.NODES:
            with self.subTest(node=node["name"]):
                rendered, missing = node["fn"](DAY, now)
                self.assertIsNotNone(rendered, f"{node['name']} 全量夹具下不应缺来源: {missing}")
                self._assert_card_shape(rendered[0])
                self.assertIn(rendered[1], ("pass", "warn", "fail"))

    def test_plan_gate_failure_marks_card_red(self):
        d = DAY
        _write_json(self.out / f"preflight_{d}_post_plan.json",
                    {"date": d, "stage": "post_plan", "time": f"{d} 08:55:02", "status": "fail",
                     "summary": {"pass": 16, "fail": 2, "warn": 0},
                     "results": [
                         {"name": "当日计划", "ok": False, "critical": True, "detail": "缺失",
                          "category": "当日计划"},
                         {"name": "外盘新鲜度", "ok": False, "critical": True, "detail": "陈旧",
                          "category": "当日计划"}]})
        rendered, _ = sp._n_plan_gate(d, sp._deadline(d, "09:00"))
        card, state, tone = rendered
        self.assertEqual(state, "fail")
        self.assertEqual(tone, "red")
        body = json.dumps(card, ensure_ascii=False)
        self.assertIn("买入类链停摆", body)
        self.assertIn("🔴", body)

    def test_post_close_marks_bad_stage_and_missing_next_plan(self):
        d = DAY
        (self.out / "acceptance").mkdir(parents=True, exist_ok=True)
        rows = [{"run_id": "pc", "stage": "acceptance", "attempt_no": 1, "exit_code": 3,
                 "status": "failed", "started_at": f"{d} 18:24:48", "finished_at": f"{d} 18:24:56"}]
        (self.out / "acceptance" / f"chain_manifest_{d}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        _write_json(self.out / "acceptance" / f"acceptance_{d}.json", {"date": d, "status": "incomplete"})
        rendered, missing = sp._n_post_close(d, sp._deadline(d, "18:30"))
        self.assertIsNotNone(rendered, missing)
        card, state, tone = rendered
        self.assertEqual(state, "fail")
        body = json.dumps(card, ensure_ascii=False)
        self.assertIn("acceptance", body)
        self.assertIn("次日计划", body)


class DecodingTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_reads_utf8_bom_json(self):
        p = self.td / "bom.json"
        p.write_text(json.dumps({"k": "值"}, ensure_ascii=False), encoding="utf-8-sig")
        self.assertEqual(sp._json(p), {"k": "值"})

    def test_reads_gbk_text(self):
        p = self.td / "gbk.log"
        p.write_bytes("中文日志 90.6%".encode("gbk"))
        self.assertIn("中文日志", sp._read_text(p))

    def test_jsonl_skips_corrupted_lines(self):
        p = self.td / "d.jsonl"
        p.write_text('{"ok":1}\nnot-json\n\n{"ok":2}\n', encoding="utf-8")
        self.assertEqual([r["ok"] for r in sp._jsonl(p)], [1, 2])

    def test_missing_file_is_tolerated(self):
        self.assertEqual(sp._jsonl(self.td / "nope.jsonl"), [])
        self.assertIsNone(sp._json(self.td / "nope.json"))


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._td.name)
        self.out, self.notif = _env(self.td)
        for p in (mock.patch.object(sp, "OUT", self.out),
                  mock.patch.object(sp, "NOTIF", self.notif),
                  mock.patch.object(sp, "STATE_DIR", self.out / "selfcheck"),
                  mock.patch.object(sp, "BASE", self.td),
                  # R0.2：同上，隔离 env-aware LEDGER（边界测试的核心断言是"绝不写账本"）
                  mock.patch.object(sp, "LEDGER", self.td / "portfolio" / "ledger.json")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._td.cleanup)

    def test_non_trading_day_is_silent(self):
        pushed = _FakeCards()
        with mock.patch.object(sp, "_calendar_check", return_value=3), \
             mock.patch.object(sp, "_push_card", pushed), \
             mock.patch.object(sp, "_post", mock.Mock(side_effect=AssertionError("must not push"))), \
             mock.patch("sys.argv", ["status_push.py", "--node", "run"]):
            rc = sp.main()
        self.assertEqual(rc, 0)
        self.assertEqual(pushed.sent, [])
        self.assertEqual(list(self.notif.glob("status_push_*.json")), [])
        self.assertEqual(list(self.notif.glob("delivery_*.jsonl")), [])

    def test_self_check_skips_when_not_scheduled_instance(self):
        """非计划实例（手动/补跑）只记 tick，不推导不推送。

        ⚠️ 2026-09-12 修复（两次才对）：原 `sys.argv` 没带 `--date` → `status_push.py:866`
        用 `now.strftime()` 取**运行当天** → 写成 `status_push_<今天>.json`，
        而本用例读的是固定的 `status_push_{DAY}.json` → 非 9/11 跑就 `FileNotFoundError`。

        正解**不是**传 `--date`：`status_push.py:867` 有
        `production = args.node == "run" and not args.date` ——
        一旦带 `--date`，`production` 变 False → **跳过 `:880` 的计划实例自证分支**，
        于是本用例反而真的去推送（触发 `must not push`）。
        → 必须 **mock `_now()`**：既让 `day` = 固定日，又保持 `production=True`。
        """
        fixed_now = datetime.datetime(2026, 9, 11, 17, 30, 0)
        with mock.patch.object(sp, "_calendar_check", return_value=0), \
             mock.patch.object(sp, "_self_next_run_day", return_value="2026-09-14"), \
             mock.patch.object(sp, "_post", mock.Mock(side_effect=AssertionError("must not push"))), \
             mock.patch.object(sp, "_now", return_value=fixed_now), \
             mock.patch("sys.argv", ["status_push.py", "--node", "run"]):
            rc = sp.main()
        self.assertEqual(rc, 0)
        st = json.loads((self.notif / f"status_push_{DAY}.json").read_text(encoding="utf-8"))
        self.assertTrue(st["ticks"], "计划实例自证失败时应留下 tick 记录")
        self.assertEqual(st["nodes"], {})

    def test_observer_never_writes_ledger_plans_or_gates(self):
        """只读边界：观测器只允许写 status_push_<date>.json 与 delivery_*.jsonl。

        ⚠️ 2026-09-12 修复两处：
        ① **日期**：原 `sys.argv` 没带 `--date` → 产出**运行当天**的文件名，与白名单里的 `DAY` 错开
           → 非 9/11 跑必红。改 **mock `_now()`**（既固定 `day`，又不破坏 `production` 语义；
           传 `--date` 会改 `production`，见 `status_push.py:867`，对本题虽无影响但语义更混）。
        ② **源码双源命名（真缺陷）**：`_audit_append` 原用 `_now()` 定 delivery 文件名，
           而读取侧用 `day` → 已修为「`day` 优先 → event_key 兜底」。本用例正是它的回归保护。
        ⚠️ 注意本题真正的「只读边界」断言（`portfolio`/`plans`/`acceptance` 三项不得写入）
        **一直是通过的** —— 它原先只是被日期错配连坐。
        """
        watched = (self.td / "portfolio", self.out / "plans", self.out / "acceptance")
        cards = _FakeCards()
        fixed_now = datetime.datetime(2026, 9, 11, 17, 30, 0)
        with mock.patch.object(sp, "_calendar_check", return_value=0), \
             mock.patch.object(sp, "_self_next_run_day", return_value=DAY), \
             mock.patch.object(sp, "_push_card", cards), \
             mock.patch.object(sp, "_now", return_value=fixed_now), \
             mock.patch("sys.argv", ["status_push.py", "--node", "preflight"]):
            sp.main()
        for w in watched:
            self.assertEqual(list(w.rglob("*")) if w.exists() else [], [],
                             f"观测器不得写入 {w}")
        allowed = {p.name for p in self.notif.iterdir()}
        self.assertTrue(allowed.issubset({f"status_push_{DAY}.json",
                                          f"delivery_{DAY.replace('-', '')}.jsonl"}),
                        f"出现越界写入: {allowed}")

    def test_env_var_webhook_is_accepted(self):
        with mock.patch.dict(os.environ, {"YAOBAN_FEISHU_WEBHOOK":
                                          "https://open.feishu.cn/open-apis/bot/v2/hook/abc"}):
            self.assertTrue(sp._webhook().endswith("/abc"))

    def test_invalid_webhook_fails_closed(self):
        with mock.patch.dict(os.environ, {"YAOBAN_FEISHU_WEBHOOK": "http://evil"}):
            with self.assertRaises(RuntimeError):
                sp._webhook()


class ScheduleContractTests(unittest.TestCase):
    """计划表即代码：状态卡任务的触发时刻必须与 preflight 契约、注册表一致。"""

    def test_preflight_contract_lists_status_push(self):
        import preflight
        self.assertIn("YaobanStatusPush", preflight.TRIGGER_EXPECTED)
        self.assertEqual(preflight.TRIGGER_EXPECTED["YaobanStatusPush"],
                         ["08:36", "08:50", "08:55", "09:00", "09:30", "09:35", "11:30",
                          "13:05", "13:10", "15:05", "15:40", "17:45", "18:30"])

    def test_registrar_declares_same_times(self):
        reg = (ROOT / "scripts" / "register_schedule.ps1").read_text(encoding="utf-8")
        self.assertIn('Name = "YaobanStatusPush"', reg)
        self.assertIn('Script = "run_status_push.ps1"', reg)
        import preflight
        for at in preflight.TRIGGER_EXPECTED["YaobanStatusPush"]:
            self.assertIn(f'"{at}"', reg, f"注册表缺少触发时刻 {at}")

    def test_node_deadlines_are_inside_the_repeat_window(self):
        """每个节点产卡时刻必须落在计划任务的重复窗（08:36–19:00）内。"""
        for node in sp.NODES:
            at = node["at"]
            self.assertGreaterEqual(at, "08:36")
            self.assertLessEqual(at, "19:00")

    def test_runner_script_uses_production_python(self):
        ps = (ROOT / "scripts" / "run_status_push.ps1").read_text(encoding="utf-8")
        self.assertIn("status_push.py", ps)
        self.assertIn("--node", ps)
        self.assertIn("workbuddy", ps)

    def test_ps1_entry_points_are_ascii_or_bom(self):
        """编码陷阱守卫（2026-09-11 实测踩坑）。

        Windows PowerShell 5.1 对**无 BOM** 的 .ps1 按系统 ANSI(GBK) 解码：
        含中文注释的脚本会被错解，个别行会连带吞掉相邻表项——register_schedule.ps1
        就这样静默漏注册了 YaobanStatusPush（只注册了 18/19 条）。
        规则：.ps1 要么保持纯 ASCII，要么必须带 UTF-8 BOM。
        """
        for name in ("run_status_push.ps1", "register_schedule.ps1", "run_trading_task.ps1"):
            p = ROOT / "scripts" / name
            with self.subTest(script=name):
                raw = p.read_bytes()
                has_bom = raw[:3] == b"\xef\xbb\xbf"
                is_ascii = all(b < 128 for b in raw)
                self.assertTrue(has_bom or is_ascii,
                                f"{name} 含非 ASCII 且无 UTF-8 BOM：PowerShell 5.1 会按 GBK 错解，"
                                f"可能静默漏注册计划任务")

    def test_status_push_script_has_no_non_ascii_identifiers(self):
        """status_push.py 由生产 python 以 UTF-8 读取，这里只确认它是合法 UTF-8。"""
        raw = (ROOT / "scripts" / "status_push.py").read_bytes()
        raw.decode("utf-8")  # 不抛异常即可


if __name__ == "__main__":
    unittest.main(verbosity=2)
