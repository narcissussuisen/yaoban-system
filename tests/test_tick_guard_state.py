"""tick 守护双信号与隔离可恢复的离线单元测试（2026-09-11）。

覆盖 INC-2026-09-11-01 暴露的六个缺陷中本批修复的部分：
  ③ 唤醒信号耦合（存活 vs 数据新鲜）
  ④ degraded 单向闩锁（恢复后不清状态 → 174 次 scan 全天 fail-closed）
  ⑤ 双重 watcher 留痕
  ⑥ 预算语义（当日累计 → 滑动窗口）与降噪

全部离线、哑数据、不联网、不推送、不启动真实 daemon。
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import _tick_watch as tw  # noqa: E402

TZ = tw.TZ


def _base_cfg(**kw):
    cfg = {"stale_sec": 90, "daemon_stale_sec": 60, "max_restarts": 5,
           "restart_window_sec": 900, "boot_grace": 150}
    cfg.update(kw)
    return cfg


class BeatAgeTests(unittest.TestCase):
    """心跳年龄: 缺失语义 + 与 effective_age 的午休差异。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_missing_file_is_none(self):
        self.assertIsNone(tw.beat_age(self.td / "nope.beat"))

    def test_age_is_computed_from_mtime(self):
        p = self.td / "b.beat"
        p.write_text("{}", encoding="ascii")
        os.utime(p, (time.time() - 30, time.time() - 30))
        age = tw.beat_age(p)
        self.assertAlmostEqual(age, 30, delta=3)

    def test_never_negative(self):
        p = self.td / "future.beat"
        p.write_text("{}", encoding="ascii")
        os.utime(p, (time.time() + 300, time.time() + 300))
        self.assertEqual(tw.beat_age(p), 0.0)

    def test_does_not_discount_lunch_unlike_effective_age(self):
        """daemon 午休仍在写心跳, 因此心跳**不能**扣午休；pos_live 必须扣。"""
        p = self.td / "cross.beat"
        p.write_text("{}", encoding="ascii")
        today = datetime.datetime.now(TZ)
        last_am = datetime.datetime(today.year, today.month, today.day, 11, 29, 30, tzinfo=TZ)
        os.utime(p, (last_am.timestamp(), last_am.timestamp()))
        pm = datetime.datetime(today.year, today.month, today.day, 13, 2, 0, tzinfo=TZ)
        hb = tw.beat_age(p, now_epoch=pm.timestamp())
        pd = tw.effective_age(p, m_now=13 * 60 + 2, now_epoch=pm.timestamp())
        self.assertGreater(hb, 1800, "心跳跨午休不应被扣除")
        self.assertLess(pd, 180, "pos_live 跨午休必须扣除 90 分钟")


class DaemonStatusTests(unittest.TestCase):
    """存活信号判据: 心跳优先, 仅在心跳文件从未出现时回退进程探测。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def test_fresh_heartbeat_means_alive(self):
        beat = self.td / "b"
        beat.write_text("{}", encoding="ascii")
        alive, age = tw._daemon_status(_base_cfg(beat=beat), pids=[])
        self.assertTrue(alive)
        self.assertLess(age, 5)

    def test_stale_heartbeat_means_dead_even_if_pids_present(self):
        """关键语义: pids 查询失败会返回空表, 但反过来 pids 有值也不能推翻心跳定论。"""
        beat = self.td / "b"
        beat.write_text("{}", encoding="ascii")
        old = time.time() - 600
        os.utime(beat, (old, old))
        alive, _ = tw._daemon_status(_base_cfg(beat=beat), pids=[1234])
        self.assertFalse(alive, "心跳停更 = 进程死, 不得被 pids 非空掩盖")

    def test_no_heartbeat_falls_back_to_pids(self):
        cfg = _base_cfg(beat=self.td / "never")
        alive_yes, age = tw._daemon_status(cfg, pids=[111])
        alive_no, _ = tw._daemon_status(cfg, pids=[])
        self.assertTrue(alive_yes, "旧 daemon/未升级环境应回退到进程探测")
        self.assertFalse(alive_no)
        self.assertIsNone(age)


class TickFreshTests(unittest.TestCase):
    """数据信号: 午休冻结期不判陈旧; 缺失视为不新鲜。"""

    def test_missing_is_not_fresh(self):
        self.assertFalse(tw._tick_fresh_state(_base_cfg(), None, False))

    def test_fresh_within_threshold(self):
        self.assertTrue(tw._tick_fresh_state(_base_cfg(), 10, False))

    def test_stale_beyond_threshold(self):
        self.assertFalse(tw._tick_fresh_state(_base_cfg(), 91, False))

    def test_lunch_freeze_tolerates_staleness(self):
        self.assertTrue(tw._tick_fresh_state(_base_cfg(), 5000, True))

    def test_boundary_exactly_threshold_is_fresh(self):
        self.assertTrue(tw._tick_fresh_state(_base_cfg(), 90, False))
        self.assertFalse(tw._tick_fresh_state(_base_cfg(), 90.5, False))


class BudgetWindowTests(unittest.TestCase):
    """预算语义: 滑动窗口, 不是当日累计(9/11 的 90 秒烧穿全天)。"""

    def test_within_budget(self):
        cfg = _base_cfg(max_restarts=3, restart_window_sec=900)
        budget = [time.time() - 10, time.time() - 20]
        self.assertFalse(tw._over_budget(budget, cfg))

    def test_at_budget_is_over(self):
        cfg = _base_cfg(max_restarts=2, restart_window_sec=900)
        budget = [time.time() - 10, time.time() - 20]
        self.assertTrue(tw._over_budget(budget, cfg))

    def test_window_slide_restores_budget(self):
        """窗口滑过之后必须重新可用 —— 否则一次抖动等于"当天完蛋"。"""
        cfg = _base_cfg(max_restarts=2, restart_window_sec=60)
        budget = [time.time() - 3600, time.time() - 7200]
        self.assertFalse(tw._over_budget(budget, cfg))
        self.assertEqual(budget, [], "过期记录应被就地清理")

    def test_prunes_only_expired(self):
        cfg = _base_cfg(max_restarts=5, restart_window_sec=60)
        fresh = time.time() - 5
        budget = [time.time() - 3600, fresh]
        tw._over_budget(budget, cfg)
        self.assertEqual(len(budget), 1)
        self.assertAlmostEqual(budget[0], fresh, delta=1)

    def test_default_window_when_unset(self):
        cfg = {"max_restarts": 1}
        self.assertTrue(tw._over_budget([time.time() - 5], cfg))


class StateExtrasTests(unittest.TestCase):
    """双信号字段必须齐全(含 None) —— 字段缺失会被读成"健康"。"""

    def test_all_keys_present(self):
        cfg = _base_cfg()
        ex = tw._state_extras(cfg, True, False, 3, 500, True, 2, [1, 2])
        for k in ("daemon_alive", "tick_fresh", "daemon_beat_age_s", "tick_age_s",
                  "degraded", "restarts_window", "window_sec"):
            self.assertIn(k, ex)
        self.assertTrue(ex["daemon_alive"])
        self.assertFalse(ex["tick_fresh"])
        self.assertEqual(ex["daemon_beat_age_s"], 3)
        self.assertEqual(ex["tick_age_s"], 500)
        self.assertEqual(ex["restarts_window"], 2)

    def test_none_ages_are_kept_not_dropped(self):
        """None = "本轮未测量", 与 False("测到了是坏的")语义不同, 必须原样保留。"""
        ex = tw._state_extras(_base_cfg(), None, None, None, None, False, 0, [])
        self.assertIn("daemon_beat_age_s", ex)
        self.assertIsNone(ex["daemon_beat_age_s"])
        self.assertIsNone(ex["daemon_alive"], "未测量不得被归一化成 False（会误判为守护故障）")
        self.assertIsNone(ex["tick_fresh"])

    def test_write_state_keeps_none_fields_and_legacy_keys(self):
        with tempfile.TemporaryDirectory() as td:
            out = pathlib.Path(td)
            tw.write_state(out, "armed", "started",
                           tw._state_extras(_base_cfg(), None, None, None, None, False, 0, []))
            st = json.loads((out / "tick_guard_state.json").read_text(encoding="utf-8"))
            for k in ("date", "time", "state", "detail"):
                self.assertIn(k, st, f"旧字段必须保留: {k}")
            self.assertIn("daemon_beat_age_s", st)
            self.assertIsNone(st["daemon_beat_age_s"])
            self.assertIsNone(st["daemon_alive"])


class GuardStateReadingTests(unittest.TestCase):
    """下游读者口径: 不能只看 state=restart_exhausted。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._td.name)
        self.out = self.td / "outputs"
        (self.out / "intraday").mkdir(parents=True)
        self.addCleanup(self._td.cleanup)

    def _write(self, payload):
        (self.out / "intraday" / "tick_guard_state.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_acceptance_flags_state_isolated(self):
        import collect_daily_acceptance as cda
        day = "2026-09-11"
        self._write({"date": day, "state": "restart_exhausted", "degraded": True})
        with mock.patch.object(cda, "BASE", self.td):
            gs = cda.read_json(self.out / "intraday" / "tick_guard_state.json") or {}
        failed = bool(gs.get("date") == day and (
            str(gs.get("state")) in ("restart_exhausted",) or gs.get("degraded") is True
            or gs.get("daemon_alive") is False or gs.get("tick_fresh") is False))
        self.assertTrue(failed)

    def test_daemon_dead_is_flagged_even_with_benign_state(self):
        """state 被后续写入覆盖成 ok, 但 daemon_alive=False 仍须判失败。"""
        day = "2026-09-11"
        self._write({"date": day, "state": "ok", "daemon_alive": False, "tick_fresh": False})
        gs = json.loads((self.out / "intraday" / "tick_guard_state.json").read_text(encoding="utf-8"))
        failed = bool(gs.get("date") == day and (
            str(gs.get("state")) in ("restart_exhausted",) or gs.get("degraded") is True
            or gs.get("daemon_alive") is False or gs.get("tick_fresh") is False))
        self.assertTrue(failed)

    def test_healthy_state_is_not_flagged(self):
        day = "2026-09-11"
        self._write({"date": day, "state": "ok", "daemon_alive": True, "tick_fresh": True})
        gs = json.loads((self.out / "intraday" / "tick_guard_state.json").read_text(encoding="utf-8"))
        failed = bool(gs.get("date") == day and (
            str(gs.get("state")) in ("restart_exhausted",) or gs.get("degraded") is True
            or gs.get("daemon_alive") is False or gs.get("tick_fresh") is False))
        self.assertFalse(failed)


class NoiseFilterTests(unittest.TestCase):
    """降噪: 看门狗生命周期事件不得进逐条告警流(9/11 有 7 条 restart 告警)。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = pathlib.Path(self._td.name)
        self.out = self.td / "outputs"
        (self.out / "intraday").mkdir(parents=True)
        self.addCleanup(self._td.cleanup)

    def _rows(self, day="2026-09-11"):
        import notify_trading_events as nte
        with mock.patch.object(nte, "BASE", self.td):
            return nte._risk_rows(day)

    def test_watch_lifecycle_events_filtered(self):
        day = "2026-09-11"
        events = [
            {"date": day, "trigger": "watch_start", "time": "09:30:05", "action": "info"},
            {"date": day, "trigger": "watch_restart", "time": "09:32:50", "action": "info"},
            {"date": day, "trigger": "watch_exit", "time": "15:03:21", "action": "info"},
            {"date": day, "trigger": "watch_recovered", "time": "10:40:00", "action": "info"},
            {"date": day, "trigger": "duplicate_watcher", "time": "09:30:04", "action": "info"},
        ]
        (self.out / "intraday" / "risk_events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
        self.assertEqual(self._rows(day), [])

    def test_real_risk_and_failure_events_are_kept(self):
        day = "2026-09-11"
        events = [
            {"date": day, "trigger": "stop_loss", "time": "09:41:08", "sym": "300468",
             "action": "execute", "px": 23.31},
            {"date": day, "trigger": "watch_limit", "time": "10:37:20", "action": "halt",
             "detail": "budget exhausted"},
            {"date": day, "trigger": "daemon_crash", "time": "10:35:42", "action": "halt",
             "detail": "boom"},
        ]
        (self.out / "intraday" / "risk_events.jsonl").write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
        rules = {r["rule"] for r in self._rows(day)}
        self.assertIn("stop_loss", rules)
        self.assertIn("watch_limit", rules, "真故障必须保留")
        self.assertIn("daemon_crash", rules, "守护崩溃必须保留")

    def test_other_day_events_ignored(self):
        (self.out / "intraday" / "risk_events.jsonl").write_text(
            json.dumps({"date": "2026-09-10", "trigger": "stop_loss", "time": "10:00:00"},
                       ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(self._rows("2026-09-11"), [])


class ContractTests(unittest.TestCase):
    """源码级契约: 心跳必须在循环内且早于所有 continue 守卫。"""

    def test_heartbeat_written_before_any_guard(self):
        src = (ROOT / "scripts" / "tick_monitor.py").read_text(encoding="utf-8")
        i_loop = src.index("while a.rounds==0 or rounds<a.rounds:")
        i_beat = src.index("write_beat(n, interval)")
        # 2026-09-12 R0.8: 时段守卫由写死字面量 `('09:30'<=hm2<='15:05')` 收敛为共用函数
        # `in_exec_window(hm2)`（三处调用点曾各写一遍）。断言**意图不变**（心跳早于时段守卫），
        # 只把锚点换到新表达式 —— 别改成断言字面量，那会再次把"窗口被改错"判为通过。
        i_guard = src.index("if a.daemon and not in_exec_window(hm2):")
        i_risk = src.index("for sym,pos in list(")
        self.assertGreater(i_beat, i_loop, "心跳必须写在循环体内")
        self.assertLess(i_beat, i_guard, "心跳必须早于时段守卫")
        self.assertLess(i_beat, i_risk, "心跳必须早于风控循环")

    def test_heartbeat_file_is_not_pos_live(self):
        """心跳必须与数据文件分离, 否则又回到"数据卡住=进程死"的老问题。"""
        self.assertNotEqual(tw.BEAT, tw.POS)
        src = (ROOT / "scripts" / "tick_monitor.py").read_text(encoding="utf-8")
        self.assertIn("_tick_daemon.beat", src)

    def test_degraded_is_cleared_somewhere(self):
        """degraded 必须有"被置回 False"的路径(9/11 的核心缺陷是一置位永不复位)。"""
        src = (ROOT / "scripts" / "_tick_watch.py").read_text(encoding="utf-8")
        self.assertIn("degraded = False", src)
        self.assertIn("degraded cleared", src)

    def test_daemon_has_crash_logging(self):
        src = (ROOT / "scripts" / "tick_monitor.py").read_text(encoding="utf-8")
        self.assertIn("daemon_crash", src)
        self.assertIn("traceback", src)

    def test_runner_script_writes_beat_before_connect(self):
        """心跳必须出现在**主循环体内**, 且不晚于循环内任何 TDX 取数调用。

        连接尝试在循环外(启动期 SERVERS 探测), 故以"风控取数那行"作为循环内的锚点。
        """
        src = (ROOT / "scripts" / "tick_monitor.py").read_text(encoding="utf-8")
        i_loop = src.index("while a.rounds==0 or rounds<a.rounds:")
        i_beat = src.index("write_beat(n, interval)")
        i_fetch = src.index("bars=api.get_security_bars(", i_loop)
        self.assertGreater(i_beat, i_loop, "心跳必须在循环体内")
        self.assertLess(i_beat, i_fetch, "心跳必须早于循环内的 TDX 取数")


if __name__ == "__main__":
    unittest.main(verbosity=2)
