"""2026-09-10 运行节奏重构的契约回归测试(用户六项裁定)。

覆盖:
  - 交易日守卫: 日历判定 / 守卫存在 / 维护类豁免 / launch -Force 透传
  - 门禁解耦: tick 分支不再依赖 post_plan 门禁; 8-token 文本契约仍在(防 9/4 类回归)
  - 计划表即代码: register_schedule.ps1 的表与 preflight.TRIGGER_EXPECTED 一致, 且每个 Mode 在 runner 有 case
  - 自愈边界: 剧本不含账本/门禁写入路径, 只对 critical 失败负责, 资源类列入不可自愈
  - auction 降级: 缺计划时纯快照且标记 plan_missing
"""
from __future__ import annotations
import importlib
import json
import pathlib
import re
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "portfolio"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent / "py_libs"))

RUNNER = (ROOT / "scripts" / "run_trading_task.ps1").read_text(encoding="utf-8")
REGISTRAR = (ROOT / "scripts" / "register_schedule.ps1").read_text(encoding="utf-8")
SELFHEAL = (ROOT / "scripts" / "selfheal.py").read_text(encoding="utf-8")
WATCH = (ROOT / "scripts" / "_tick_watch.py").read_text(encoding="utf-8")


class TradingCalendarTest(unittest.TestCase):
    def setUp(self):
        import trading_calendar as tc
        self.tc = importlib.reload(tc)
        self.tmp = tempfile.TemporaryDirectory()
        self.tc.CAL_DIR = pathlib.Path(self.tmp.name)
        (pathlib.Path(self.tmp.name) / "trade_dates_2026.json").write_text(json.dumps({
            "year": 2026, "source": "test", "fetched_at": "2026-09-10 08:00:00",
            "start": "2026-01-01", "end": "2026-12-31",
            "days": {"2026-09-10": 1, "2026-09-11": 1, "2026-09-12": 0, "2026-09-13": 0, "2026-10-01": 0}}),
            encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_states(self):
        self.assertEqual(self.tc.classify("2026-09-11", allow_live=False)["state"], "trading")
        self.assertEqual(self.tc.classify("2026-09-12", allow_live=False)["state"], "non_trading")
        self.assertEqual(self.tc.classify("2026-10-01", allow_live=False)["state"], "non_trading")

    def test_unknown_is_fail_open(self):
        # 缓存未覆盖的日期 + 禁止在线刷新 -> unknown, 但 is_trading_day 必须按交易日处理(宁多跑不误停)
        self.assertEqual(self.tc.classify("2027-01-04", allow_live=False)["state"], "unknown")
        self.assertTrue(self.tc.is_trading_day("2027-01-04", allow_live=False))

    def test_prev_trading_day(self):
        self.assertEqual(self.tc.prev_trading_day("2026-09-14"), "2026-09-11")


class RunnerGuardTest(unittest.TestCase):
    def test_guard_and_exemptions(self):
        self.assertIn("trading_calendar.py", RUNNER)
        self.assertIn("交易日", RUNNER)
        self.assertIn("$CalendarExempt=@('tdx-verify','calendar-refresh')", RUNNER)
        self.assertIn("[switch]$Force", RUNNER)

    def test_tick_decoupled_from_gate(self):
        self.assertNotIn("'tick' {Gate", RUNNER, "tick 不得再被 post_plan 门禁前置(风控裸奔风险)")
        self.assertIn("'tick' {Run-Stage 'tick'", RUNNER)

    def test_eight_token_contract_intact(self):
        combo = RUNNER + WATCH
        for tok in ("--execute-risk", "--e4-support", "--temp-ladder", "--min-amt", "10",
                    "--execute", "feishu_notify.py", "generate_next_plan.py"):
            self.assertIn(tok, combo, "preflight runner_ok 依赖的文本契约缺失: " + tok)

    def test_new_modes_present(self):
        for mode in ("selfheal", "calendar-refresh"):
            self.assertIn("'" + mode + "' {Run-Stage", RUNNER)


class ScheduleTableTest(unittest.TestCase):
    def table(self):
        entries = []
        for block in re.findall(r"@\{([^}]*)\}", REGISTRAR):
            name = re.search(r'Name = "([^"]+)"', block)
            mode = re.search(r'Mode = "([^"]+)"', block)
            ats = re.findall(r'"(\d{2}:\d{2})"', block)
            if name:
                entries.append((name.group(1), mode.group(1) if mode else None, ats))
        return entries

    def test_every_mode_has_runner_case(self):
        missing = [m + " (" + n + ")" for n, m, _ in self.table() if m and ("'" + m + "' {") not in RUNNER]
        self.assertEqual(missing, [], "计划表里的 mode 在 runner 无对应 case: " + ",".join(missing))

    def test_preflight_trigger_contract_matches_table(self):
        import preflight
        expected = preflight.TRIGGER_EXPECTED
        drift = []
        for name, _mode, ats in self.table():
            if name in expected and sorted(ats) != sorted(expected[name]):
                drift.append(name + ": table=" + ",".join(sorted(ats)) + " preflight=" + ",".join(sorted(expected[name])))
        self.assertEqual(drift, [], "计划表与 preflight 触发器契约不一致: " + " | ".join(drift))
        self.assertIn("EvoAlphaSelfHeal", expected)

    def test_key_times(self):
        times = {n: a for n, _m, a in self.table()}
        self.assertEqual(times.get("EvoAlphaPreflight"), ["08:35"])
        self.assertEqual(times.get("EvoAlphaPostCloseChain"), ["15:35"])
        self.assertEqual(times.get("EvoAlphaEveningCheck"), ["17:30"])


class SelfHealBoundaryTest(unittest.TestCase):
    def test_no_ledger_or_gate_writes(self):
        # 断言针对代码体(去掉模块 docstring 里的禁令说明)
        body = SELFHEAL.split(chr(34) * 3, 2)[2]
        self.assertNotIn("ledger", body.lower())
        self.assertNotIn("portfolio", body.lower())
        # 只允许写自愈产物一个文件(不得写门禁/账本/L 其他产物)
        self.assertEqual(body.count(".write_text("), 1)
        self.assertIn("禁止: 改账本", SELFHEAL)

    def test_resources_are_not_repairable(self):
        for item in ("DISK C", "DISK F", "CPU", "内存"):
            self.assertIn(item, SELFHEAL)

    def test_only_critical_failures_are_actionable(self):
        import selfheal
        report = {"results": [{"name": "看板", "ok": False, "critical": True},
                              {"name": "DISK F 使用率", "ok": False, "critical": False}]}
        self.assertEqual(selfheal.failed_of(report), ["看板"])
        self.assertEqual(selfheal.warn_of(report), ["DISK F 使用率"])

    def test_playbook_never_touches_triggers(self):
        # 触发器漂移属人为改点可能, 自愈不得覆盖(preflight 只做非致命告警)
        self.assertNotIn("'任务触发器'", SELFHEAL.split("NOT_REPAIRABLE")[0])


class AuctionDegradeTest(unittest.TestCase):
    def test_missing_plan_degrades(self):
        import auction_monitor as am
        plan, missing = am.load_plan("1999-01-01")
        self.assertTrue(missing)
        self.assertEqual(plan, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
