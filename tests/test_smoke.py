"""冒烟测试：配置加载 / 存储读写 / 环境打分 / 案例库完整性"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))


class TestConfig(unittest.TestCase):
    def test_rules(self):
        import config

        rules = config.rules()
        self.assertGreaterEqual(len(rules), 30)
        ids = [r["id"] for r in rules]
        self.assertEqual(len(ids), len(set(ids)), "规则 id 必须唯一")

    def test_strategies(self):
        import config

        for name in ("huigui", "zt_huicai", "qu_shi_fanbao", "xianren"):
            self.assertIn(name, config.CONFIG["strategy"], f"缺少 strategy.{name}")


class TestCases(unittest.TestCase):
    def test_cases_json(self):
        p = ROOT / "tests" / "cases.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["cases"]), 15)
        symbols = {c["symbol"] for c in data["cases"]}
        self.assertGreaterEqual(len(symbols), 10)
        for c in data["cases"]:
            for field in ("id", "video", "symbol", "pattern", "expected", "outcome"):
                self.assertIn(field, c, f"case {c.get('id')} 缺少字段 {field}")


class TestStore(unittest.TestCase):
    def test_roundtrip(self):
        import pandas as pd

        from data.store import Store

        s = Store(":memory:")
        df = pd.DataFrame({
            "date": ["2026-08-01", "2026-08-02"],
            "open": [1, 2], "high": [3, 4], "low": [0.5, 1.5],
            "close": [2.5, 3.5], "volume": [100, 200],
        })
        s.upsert_index_daily(df, "sh000001")
        rows = s.get_index("sh000001")
        self.assertEqual(len(rows), 2)
        s.upsert_index_daily(df, "sh000001")  # 幂等
        self.assertEqual(len(s.get_index("sh000001")), 2)
        s.close()


class TestTickSnapshotContract(unittest.TestCase):
    def test_tick_is_explicit_unique_risk_executor(self):
        source = (ROOT / "scripts" / "tick_monitor.py").read_text(encoding="utf-8")
        self.assertIn("--execute-risk", source)
        self.assertIn("transact", source)
        self.assertIn("sellable_qty", source)
        self.assertIn("risk_events.jsonl", source)
        self.assertIn("跌停/无量/无可卖份额", source)
        self.assertIn("tick-risk", source)
        self.assertIn("atomic_json(OUT/'pos_live.json'", source)
        self.assertIn("if a.execute_risk and not blocked", source)
        self.assertNotIn("save(st)", source)

    def test_scan_scopes_autonomy_to_paper_account(self):
        source = (ROOT / "scripts" / "scan_and_confirm.py").read_text(encoding="utf-8")
        self.assertIn("record_signal_request", source)
        self.assertIn("account_mode", source)
        self.assertIn("autonomous_paper", source)
        self.assertIn("require_human_decision", source)
        self.assertIn("participation_cap", source)
        self.assertIn("is_authorized_symbol", source)
        self.assertIn('("600", "601", "603", "605", "000", "001", "002", "003", "300", "301")', source)
        self.assertNotIn("save(st)", source)

    def test_close_pipeline_is_audit_only(self):
        source = (ROOT / "scripts" / "close_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("收盘流水线不得重复执行订单", source)
        self.assertNotIn("buy(st", source)
        self.assertNotIn("sell(st", source)


class TestBoardRefreshContract(unittest.TestCase):
    def test_board_refresh_registration_uses_wrapper_with_both_windows(self):
        runner = (ROOT / 'scripts' / 'run_board_refresh.ps1').read_text(encoding='utf-8')
        self.assertIn("build_board.py", runner)
        self.assertIn("RedirectStandardOutput", runner)
        self.assertIn("RedirectStandardError", runner)
        self.assertIn("exit $process.ExitCode", runner)
        registration = (ROOT / 'scripts' / 'register_board_refresh.ps1').read_text(encoding='utf-8')
        self.assertIn("run_board_refresh.ps1", registration)
        self.assertIn("-At '09:35'", registration)
        self.assertIn("-At '13:05'", registration)
        self.assertIn("-RepetitionInterval (New-TimeSpan -Minutes 3)", registration)
        self.assertIn("-RepetitionDuration (New-TimeSpan -Minutes 111)", registration)
        self.assertIn("-MultipleInstances IgnoreNew", registration)
        build = (ROOT / 'scripts' / 'build_board.py').read_text(encoding='utf-8')
        self.assertIn("'build_time': _dt.now().strftime('%Y-%m-%d %H:%M:%S')", build)
        self.assertIn("_atomic_write(BOARD / 'board.json',", build)
        self.assertIn("_atomic_write(BOARD / 'index.html',", build)
        self.assertIn("os.fsync(fh.fileno())", build)
        self.assertIn("os.replace(tmp, path)", build)
        self.assertIn("f'.{os.getpid()}.tmp'", build)
        self.assertNotIn("(BOARD / 'board.json').write_text", build)
        self.assertNotIn("(BOARD / 'index.html').write_text", build)
        self.assertNotIn("portfolio.ledger.save", build)
        self.assertNotIn("ledger.save(", build)
        tick = (ROOT / 'scripts' / 'tick_monitor.py').read_text(encoding='utf-8')
        self.assertIn("exec_window=('09:30'<=hm2<='11:30')or('13:00'<=hm2<='15:00')", tick)
        self.assertIn("if exec_window and trig and (sym,trig) not in fired:", tick)


class TestIntradayAlertContract(unittest.TestCase):
    def test_new_alerts_are_neutral_events(self):
        source = (ROOT / "scripts" / "monitor_intraday.py").read_text(encoding="utf-8")
        self.assertIn("'event_kind': 'risk'", source)
        self.assertIn("'event_kind': 'candidate'", source)
        self.assertIn("'event_label': '候选信号规则事件'", source)
        for forbidden in ("'type': 'SELL'", "'type': 'BUY'", "→ 卖出", "可买（", "破均价线减半"):
            self.assertNotIn(forbidden, source)


class TestCandidatePlanContract(unittest.TestCase):
    def test_plan_output_is_neutral_and_cutoff_is_dynamic(self):
        source = (ROOT / "scripts" / "plan_daily.py").read_text(encoding="utf-8")
        self.assertIn('f"候选观察(输入≤{pday}收盘)"', source)
        for forbidden in ('"position_cap"', '"sell_list"', '"cond"', '"limit_px"', '"prev_close"', '输入≤8/26收盘'):
            self.assertNotIn(forbidden, source)
        self.assertIn("os.fsync(f.fileno())", source)
        self.assertIn("os.replace(tmp_path, plan_path)", source)

    def test_cutoff_uses_latest_real_source_day_not_weekday_guess(self):
        from scripts.plan_daily import latest_source_day
        self.assertEqual(latest_source_day(["2026-09-30", "2026-10-09"], "2026-10-09"), "2026-09-30")
        with self.assertRaises(RuntimeError):
            latest_source_day(["2026-10-09"], "2026-10-09")

    def test_shared_ledger_save_is_atomic(self):
        source = (ROOT / "portfolio" / "ledger.py").read_text(encoding="utf-8")
        self.assertIn("os.fsync(f.fileno())", source)
        self.assertIn("os.replace(tmp, LEDGER)", source)


class TestEnvScore(unittest.TestCase):
    def test_regime(self):
        import pandas as pd

        from core.env_score import env_score

        up = pd.DataFrame({
            "close": [i * 1.01 for i in range(100)],
            "volume": [1e8] * 100,
        })
        r = env_score(up, limit_up_count=120, max_board_height=5, red_pct=0.7)
        self.assertEqual(r["regime"], "strong")
        self.assertGreaterEqual(r["score"], 8)

        down = pd.DataFrame({
            "close": [100 - i * 1.01 for i in range(100)],
            "volume": [1e7] * 100,
        })
        r2 = env_score(down, limit_up_count=30, max_board_height=2, red_pct=0.2)
        self.assertEqual(r2["regime"], "weak")
        self.assertLessEqual(r2["score"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
