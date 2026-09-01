"""P0.2 时序契约测试（蓝图 B0-P0-2）——以两笔真实成交为锚定案例。

- 300489（2026-08-31, ledger 实录）: 合规——必须通过
- 300468（2026-09-01, ledger 实录）: 违规（signal 10:05 → decision 13:12:08, 滞后 3h07m）——必须拒绝
"""
from __future__ import annotations
import pathlib, sys, unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'portfolio'))
from timing_contract import (validate_buy_timing, signal_fresh,
                             FRESHNESS_SECONDS, BAR_TOLERANCE_SECONDS)

# ---- 锚定案例（来自 portfolio/ledger.json 真实成交, pre-fix 证据）----
CASE_300489 = {  # 合规: signal 09:59 → decision 09:59:09 → exec bar 10:00 → recorded 09:59:09
    'signal_ts': '2026-08-31 09:59', 'decision_ts': '2026-08-31 09:59:09',
    'exec_bar_ts': '2026-08-31 10:00', 'recorded_at': '2026-08-31 09:59:09',
}
CASE_300468 = {  # 违规: signal 10:05 → decision 13:12:08（回溯成交, B0-P0-2 实锤）
    'signal_ts': '2026-09-01 10:05', 'decision_ts': '2026-09-01 13:12:08',
    'exec_bar_ts': '2026-09-01 10:06', 'recorded_at': '2026-09-01 13:12:08',
}


class TimingContractAnchorTests(unittest.TestCase):
    def test_300489_compliant_case_passes(self):
        ok, reason = validate_buy_timing(**CASE_300489)
        self.assertTrue(ok, f'300489 合规案例被误拒: {reason}')

    def test_300468_retroactive_case_rejected(self):
        ok, reason = validate_buy_timing(**CASE_300468)
        self.assertFalse(ok, '300468 回溯成交未被拒绝')
        self.assertIn('R1', reason)


class TimingContractBoundaryTests(unittest.TestCase):
    def test_freshness_boundary(self):
        # age == 120s 通过; 121s 拒绝
        self.assertTrue(signal_fresh('2026-09-01 10:05:00', '2026-09-01 10:07:00')[0])
        self.assertFalse(signal_fresh('2026-09-01 10:05:00', '2026-09-01 10:07:01')[0])

    def test_inflight_bar_is_fresh(self):
        # 在途 bar（标签 > decision）age 为负 → 放行
        self.assertTrue(signal_fresh('2026-09-01 10:07', '2026-09-01 10:06:30')[0])

    def test_recorded_at_tolerance_boundary(self):
        base = {'signal_ts': '2026-09-01 10:05', 'decision_ts': '2026-09-01 10:05:00',
                'exec_bar_ts': '2026-09-01 10:06'}
        # recorded = exec_bar - 60s → 通过（恰一根 bar 容差, 300489 模式）
        ok, _ = validate_buy_timing(**base, recorded_at='2026-09-01 10:05:00')
        self.assertTrue(ok)
        # recorded = exec_bar - 61s → 拒绝
        ok, reason = validate_buy_timing(**base, recorded_at='2026-09-01 10:04:59')
        self.assertFalse(ok)
        self.assertIn('R3', reason)

    def test_r2_causality_violation(self):
        # decision 早于 exec bar 窗口起点超过 60s → 拒绝
        ok, reason = validate_buy_timing(signal_ts='2026-09-01 10:05', decision_ts='2026-09-01 10:05:30',
                                         exec_bar_ts='2026-09-01 10:08', recorded_at='2026-09-01 10:05:31')
        self.assertFalse(ok)
        self.assertIn('R2', reason)

    def test_r3_monotonic_violation(self):
        # recorded 早于 decision → 拒绝
        ok, reason = validate_buy_timing(signal_ts='2026-09-01 10:05', decision_ts='2026-09-01 10:05:30',
                                         exec_bar_ts='2026-09-01 10:05', recorded_at='2026-09-01 10:05:00')
        self.assertFalse(ok)
        self.assertIn('R3', reason)

    def test_constants_match_blueprint(self):
        self.assertEqual(FRESHNESS_SECONDS, 120)
        self.assertEqual(BAR_TOLERANCE_SECONDS, 60)


if __name__ == '__main__':
    unittest.main(verbosity=2)
