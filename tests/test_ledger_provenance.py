"""P0.4 决策 provenance 测试（蓝图 B0-P0-4）——ledger 层纵深防御。

覆盖: 空 decision_id 拒单 / 决策标的不一致拒单 / 时序缺失拒单 / 300468 式回溯拒单 /
计划外无理由拒单 / 计划内无 pick_id 拒单 / 合规买入的完整 fill 字段 / 卖出 provenance 透传 /
legacy 卖出（无 provenance）不回归。
"""
from __future__ import annotations
import pathlib, sys, unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'portfolio'))
import ledger


def _auto_state():
    s = ledger._default_state()
    s['policy']['require_human_decision'] = False
    s['policy']['account_mode'] = 'autonomous_paper'
    s['account']['cash'] = 100000.0
    return s


def _valid_provenance(state, sym='000001', signal_ts='2026-09-01 10:05',
                      decision_ts='2026-09-01 10:05:09'):
    did = ledger.record_autonomous_decision(state, {
        'sym': sym, 'signal_ts': signal_ts, 'rule': 'e4_support',
        'candidates_ref': 'deadbeef', 'plan_pick_ref': None})
    return did


class LedgerProvenanceTests(unittest.TestCase):
    def test_empty_decision_id_rejected(self):
        s = _auto_state()
        with self.assertRaisesRegex(ValueError, 'decision_id 缺失'):
            ledger.buy(s, '000001', '2026-09-01 10:06', 10.0, 100, 'P',
                       signal_ts='2026-09-01 10:05', decision_ts='2026-09-01 10:05:09',
                       plan_match={'in_plan': False},
                       off_plan_reason={'code': 'test'})

    def test_unresolvable_decision_id_rejected(self):
        s = _auto_state()
        with self.assertRaisesRegex(ValueError, 'decision_id 缺失或不可解析'):
            ledger.buy(s, '000001', '2026-09-01 10:06', 10.0, 100, 'P', decision_id='dec-auto-x',
                       signal_ts='2026-09-01 10:05', decision_ts='2026-09-01 10:05:09',
                       plan_match={'in_plan': False},
                       off_plan_reason={'code': 'test'})

    def test_decision_symbol_mismatch_rejected(self):
        s = _auto_state()
        did = _valid_provenance(s, sym='000002')  # 决策登记的是 000002
        with self.assertRaisesRegex(ValueError, '标的与订单不一致'):
            ledger.buy(s, '000001', '2026-09-01 10:06', 10.0, 100, 'P', decision_id=did,
                       signal_ts='2026-09-01 10:05', decision_ts='2026-09-01 10:05:09',
                       plan_match={'in_plan': False},
                       off_plan_reason={'code': 'test'})

    def test_missing_timing_rejected(self):
        s = _auto_state()
        did = _valid_provenance(s)
        with self.assertRaisesRegex(ValueError, 'signal_ts/decision_ts'):
            ledger.buy(s, '000001', '2026-09-01 10:06', 10.0, 100, 'P', decision_id=did,
                       plan_match={'in_plan': False}, off_plan_reason={'code': 'test'})

    def test_300468_retroactive_timing_rejected(self):
        s = _auto_state()
        did = _valid_provenance(s, signal_ts='2026-09-01 10:05',
                                decision_ts='2026-09-01 13:12:08')
        with self.assertRaisesRegex(ValueError, '时序契约违规'):
            ledger.buy(s, '000001', '2026-09-01 10:06', 10.0, 100, 'P', decision_id=did,
                       signal_ts='2026-09-01 10:05', decision_ts='2026-09-01 13:12:08',
                       plan_match={'in_plan': False}, off_plan_reason={'code': 'test'})

    def test_off_plan_without_reason_rejected(self):
        s = _auto_state()
        did = _valid_provenance(s)
        with self.assertRaisesRegex(ValueError, 'off_plan_reason'):
            ledger.buy(s, '000001', '2026-09-01 10:06', 10.0, 100, 'P', decision_id=did,
                       signal_ts='2026-09-01 10:05', decision_ts='2026-09-01 10:05:09',
                       plan_match={'in_plan': False})

    def test_in_plan_without_pick_id_rejected(self):
        s = _auto_state()
        did = _valid_provenance(s)
        with self.assertRaisesRegex(ValueError, 'pick_id'):
            ledger.buy(s, '000001', '2026-09-01 10:06', 10.0, 100, 'P', decision_id=did,
                       signal_ts='2026-09-01 10:05', decision_ts='2026-09-01 10:05:09',
                       plan_match={'in_plan': True})

    def test_valid_buy_carries_full_provenance(self):
        s = _auto_state()
        did = _valid_provenance(s)
        pm = {'in_plan': True, 'pick_id': 'plan-2026-09-01#0:000001', 'plan_sha256': 'abc'}
        ledger.buy(s, '000001', '2026-09-01 10:06', 10.0, 100, 'e4_support',
                   plan_ref='plan-2026-09-01#0:000001', decision_id=did,
                   signal_ts='2026-09-01 10:05', decision_ts='2026-09-01 10:05:09',
                   candidates_ref='deadbeef', plan_match=pm)
        fill = s['account']['fills'][-1]
        self.assertEqual(fill['decision_id'], did)
        self.assertEqual(fill['signal_ts'], '2026-09-01 10:05')
        self.assertEqual(fill['decision_ts'], '2026-09-01 10:05:09')
        self.assertEqual(fill['candidates_ref'], 'deadbeef')
        self.assertEqual(fill['plan_match'], pm)
        self.assertTrue(fill['ts'] >= fill['signal_ts'])
        # 决策登记可追溯
        self.assertEqual(s['autonomous_decisions'][did]['sym'], '000001')
        self.assertEqual(s['autonomous_decisions'][did]['candidates_ref'], 'deadbeef')

    def test_sell_with_provenance_passthrough(self):
        s = _auto_state()
        did = _valid_provenance(s)
        ledger.buy(s, '000001', '2026-08-28 10:06', 10.0, 1000, 'P', decision_id=did,
                   signal_ts='2026-08-28 10:05', decision_ts='2026-08-28 10:05:09',
                   plan_match={'in_plan': False}, off_plan_reason={'code': 'test'})
        ledger.sell(s, '000001', '2026-08-29 09:40', 10.5, 400, 'stop_loss',
                    signal_ts='2026-08-29 09:40:00', decision_ts='2026-08-29 09:40:01')
        sell_fill = [f for f in s['account']['fills'] if f['side'] == 'sell'][-1]
        self.assertEqual(sell_fill['signal_ts'], '2026-08-29 09:40:00')
        self.assertEqual(sell_fill['decision_ts'], '2026-08-29 09:40:01')

    def test_legacy_sell_without_provenance_no_regression(self):
        # 卖出（tick/T 路径之外的历史调用形态）不带 provenance 仍可执行, fill 不含新键
        s = _auto_state()
        did = _valid_provenance(s)
        ledger.buy(s, '000001', '2026-08-28 10:06', 10.0, 1000, 'P', decision_id=did,
                   signal_ts='2026-08-28 10:05', decision_ts='2026-08-28 10:05:09',
                   plan_match={'in_plan': False}, off_plan_reason={'code': 'test'})
        ledger.sell(s, '000001', '2026-08-31 09:40', 10.5, 400, 'stop')
        sell_fill = [f for f in s['account']['fills'] if f['side'] == 'sell'][-1]
        self.assertNotIn('signal_ts', sell_fill)
        self.assertNotIn('decision_ts', sell_fill)

    def test_human_path_still_works_with_timing(self):
        # 人工确认路径: 有效 decision_id + 时序契约 → 通过
        s = ledger._default_state()  # require_human_decision=True
        rid = ledger.record_signal_request(s, {'sym': '000001', 'kind': 'P',
                                                'signal_ts': '2026-08-31 09:40'})
        did = ledger.record_human_decision(s, rid, 'approve', '逐妖', 'test', 9, 11)
        ledger.buy(s, '000001', '2026-08-31 09:41', 10.0, 100, 'P', decision_id=did,
                   signal_ts='2026-08-31 09:40', decision_ts='2026-08-31 09:40:30',
                   plan_match={'in_plan': False}, off_plan_reason={'code': 'human'})
        self.assertEqual(len(s['account']['fills']), 1)

    def test_human_path_stale_signal_rejected(self):
        # 人工批准过慢（>120s）→ 时序契约拒绝（蓝图: 旧信号一律拒单）
        s = ledger._default_state()
        rid = ledger.record_signal_request(s, {'sym': '000001', 'kind': 'P',
                                                'signal_ts': '2026-08-31 09:40'})
        did = ledger.record_human_decision(s, rid, 'approve', '逐妖', 'test', 9, 11)
        with self.assertRaisesRegex(ValueError, '时序契约违规'):
            ledger.buy(s, '000001', '2026-08-31 09:55', 10.0, 100, 'P', decision_id=did,
                       signal_ts='2026-08-31 09:40', decision_ts='2026-08-31 09:55:00',
                       plan_match={'in_plan': False}, off_plan_reason={'code': 'human'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
