"""C1b append-only provenance reconciliation 测试（计划 toasty-pulse-turing-ZXjccYo0 v2.1 终审 P1 + v2.2 终审二轮 3/8）。

覆盖（C1b 行验证列：dry-run 清单人工核对 + 记录字段完整性单测）：
- 三层枚举分类（窗口外 legacy / 窗口内可解析 valid / 窗口内异常 reconciled_needed）；
- reconciliation 记录八条语义逐项：原 fill 保留原样、retroactive=true、
  decision_status=unresolved|reconstructed、原始 fill 快照+sha256、
  dec-auto-* 仅作映射（mapped_decision_id）、不冒充原始时点证据
  （original_provenance_valid=false）、原 decision_id 保留、G6 由 reconciliation 状态单独判定；
- append-only 回归锚：全程零修改原 fills（含 --reconstruct 路径）；
- G6 三层计数与整体判定；
- main() dry-run 不落盘 / --commit 落盘幂等（同 original_fill_hash 去重）。
"""
from __future__ import annotations
import copy
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _import_rf():
    """以独立模块名加载 scripts/reconcile_fills.py（不污染 sys.path 的 scripts 目录）。"""
    spec = importlib.util.spec_from_file_location(
        'reconcile_fills_under_test', ROOT / 'scripts' / 'reconcile_fills.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


rf = _import_rf()

WS, WE = '2026-09-02', '2026-09-08'
VALID_DID = 'dec-auto-20260903094100-abcdefgh'


def _state():
    """生产形态最小 state：2 笔窗口外 legacy + 1 笔窗口内异常（自造 dec-tick-*）+ 1 笔窗口内合法。"""
    return {
        'account': {
            'fills': [
                {'date': '2026-08-31', 'ts': '2026-08-31 10:00', 'sym': '300489',
                 'side': 'buy', 'qty': 100, 'px': 247.0, 'reason': 'e4_support',
                 'plan_ref': 'plan-2026-08-31', 'decision_id': '',
                 'recorded_at': '2026-08-31 09:59:09'},
                {'date': '2026-09-01', 'ts': '2026-09-01 10:05', 'sym': '300468',
                 'side': 'buy', 'qty': 1800, 'px': 24.58, 'reason': 'e4_support',
                 'plan_ref': 'plan-2026-09-01', 'decision_id': '',
                 'recorded_at': '2026-09-01 13:12:08'},
                {'date': '2026-09-02', 'ts': '2026-09-02 09:40:04', 'sym': '300489',
                 'side': 'sell', 'qty': 100, 'px': 229.81, 'reason': 'stop_loss',
                 'plan_ref': 'tick-risk', 'decision_id': 'dec-tick-20260902-300489',
                 'recorded_at': '2026-09-02 09:40:04'},
                {'date': '2026-09-03', 'ts': '2026-09-03 09:41:00', 'sym': '000001',
                 'side': 'sell', 'qty': 100, 'px': 10.5, 'reason': 'stop_loss',
                 'plan_ref': 'tick-risk', 'decision_id': VALID_DID,
                 'recorded_at': '2026-09-03 09:41:00'},
            ],
        },
        'autonomous_decisions': {
            VALID_DID: {'decision_id': VALID_DID, 'sym': '000001',
                        'decided_at': '2026-09-03 09:40:59'},
        },
        'human_decisions': {},
    }


class EnumerateTests(unittest.TestCase):
    def test_three_way_classification(self):
        out = rf.enumerate_fills(_state(), WS, WE)
        # 窗口外（date < 2026-09-02）→ legacy_exempt
        self.assertEqual([i for i, _ in out['legacy_exempt']], [0, 1])
        # 窗口内 + decision_id 可在 autonomous/human decisions 解析 → valid
        self.assertEqual([i for i, _ in out['valid']], [3])
        # 窗口内 + decision_id 不可解析（自造 dec-tick-* 或空）→ reconciled_needed
        self.assertEqual([i for i, _ in out['reconciled_needed']], [2])


class ReconciliationRowTests(unittest.TestCase):
    def setUp(self):
        self.s = _state()
        self.before = copy.deepcopy(self.s['account']['fills'])
        self.entries = rf.enumerate_fills(self.s, WS, WE)
        # 隔离生产 risk_events：注入受控 execute 事件（rule 增强语义验证）
        self._orig_idx = rf._load_risk_execute_index
        rf._load_risk_execute_index = lambda: {
            ('300489', '2026-09-02', '09:40:04'): {
                'date': '2026-09-02', 'time': '09:40:04', 'sym': '300489',
                'trigger': 'stop_loss', 'px': 229.81, 'qty': 100,
                'action': 'execute', 'blocked_reason': ''},
        }

    def tearDown(self):
        rf._load_risk_execute_index = self._orig_idx

    def test_row_field_completeness(self):
        rows = rf.build_reconciliation_rows(self.s, self.entries['reconciled_needed'], WS, WE)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        # —— 终审二轮 3 的八条语义逐项 ——
        self.assertEqual(r['original_fill_ref'], 'account.fills[2]')
        self.assertEqual(r['original_decision_id'], 'dec-tick-20260902-300489')  # 原 decision_id 原样保留
        self.assertEqual(r['original_fill_ts'], '2026-09-02 09:40:04')
        self.assertEqual((r['sym'], r['side'], r['qty'], r['px']), ('300489', 'sell', 100, 229.81))
        self.assertEqual(r['reason'], 'stop_loss')
        self.assertEqual(r['plan_ref'], 'tick-risk')
        self.assertTrue(r['retroactive'])                       # retroactive=true
        self.assertEqual(r['decision_status'], 'unresolved')    # 默认不补建
        self.assertFalse(r['original_provenance_valid'])        # 不冒充原始时点证据
        self.assertEqual(r['window'], {'start': WS, 'end': WE})
        self.assertEqual(r['rule'], 'tick_risk:stop_loss')       # risk_events execute 增强
        self.assertEqual(r['risk_event_ref']['trigger'], 'stop_loss')
        self.assertEqual(r['risk_event_ref']['action'], 'execute')
        # 原始 fill 完整快照 + sha256（规范化 JSON，键排序）
        self.assertEqual(r['original_fill_snapshot'], self.before[2])
        self.assertEqual(r['original_fill_hash'], rf._fill_hash(self.before[2]))
        self.assertEqual(len(r['original_fill_hash']), 64)
        # reconciled_at 为上海时区时间戳字符串
        self.assertRegex(r['reconciled_at'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')

    def test_original_fill_untouched_append_only(self):
        rf.build_reconciliation_rows(self.s, self.entries['reconciled_needed'], WS, WE)
        self.assertEqual(self.s['account']['fills'], self.before)  # append-only 回归锚
        self.assertEqual(self.s['account']['fills'][2]['decision_id'],
                         'dec-tick-20260902-300489')  # 不回写原 fill 的 decision_id

    def test_reconstruct_maps_dec_auto_only(self):
        rows = rf.build_reconciliation_rows(self.s, self.entries['reconciled_needed'],
                                            WS, WE, reconstruct=True)
        r = rows[0]
        self.assertEqual(r['decision_status'], 'reconstructed')
        self.assertTrue(r['mapped_decision_id'].startswith('dec-auto-'))  # 新 ID 仅作映射关联
        self.assertEqual(r['original_decision_id'], 'dec-tick-20260902-300489')  # 原值不动
        # 映射 decision 可从账本反查，且带"非原始时点证据"声明
        self.assertIn(r['mapped_decision_id'], self.s['autonomous_decisions'])
        mapped = self.s['autonomous_decisions'][r['mapped_decision_id']]
        self.assertEqual(mapped['sym'], '300489')
        self.assertIn('not original-time evidence', mapped.get('retroactive_note', ''))
        # --reconstruct 路径下原 fill 仍然零修改（append-only 回归锚）
        self.assertEqual(self.s['account']['fills'], self.before)

    def test_rule_fallback_without_risk_event(self):
        # risk_events 无匹配 execute → rule 回退为 fill.reason
        rf._load_risk_execute_index = lambda: {}
        rows = rf.build_reconciliation_rows(self.s, self.entries['reconciled_needed'], WS, WE)
        self.assertEqual(rows[0]['rule'], 'stop_loss')
        self.assertNotIn('risk_event_ref', rows[0])


class ExemptTests(unittest.TestCase):
    def test_exempt_entries(self):
        s = _state()
        entries = rf.enumerate_fills(s, WS, WE)
        es = rf.build_exempt_entries(entries['legacy_exempt'])
        self.assertEqual(len(es), 2)
        self.assertEqual(es[0]['fill_ref'], 'account.fills[0]')
        self.assertEqual(es[0]['date'], '2026-08-31')
        self.assertEqual(es[0]['sym'], '300489')
        self.assertEqual(es[0]['side'], 'buy')
        self.assertEqual(es[0]['reason_code'], 'pre_p02_no_provenance_contract')
        self.assertEqual(es[0]['original_fill_hash'], rf._fill_hash(s['account']['fills'][0]))
        self.assertEqual(es[1]['fill_ref'], 'account.fills[1]')
        self.assertEqual(es[1]['date'], '2026-09-01')


class G6VerdictTests(unittest.TestCase):
    def test_g6_pass_with_reconciliation(self):
        s = _state()
        entries = rf.enumerate_fills(s, WS, WE)
        rows = rf.build_reconciliation_rows(s, entries['reconciled_needed'], WS, WE)
        v = rf.g6_verdict(s, entries, rows)
        self.assertEqual(v['original_provenance_valid'], 1)
        self.assertEqual(v['retroactive_reconciled'], 1)
        self.assertEqual(v['unresolved_legacy_in_window'], 0)
        self.assertEqual(v['legacy_exempt_out_of_window'], 2)
        self.assertTrue(v['g6_pass'])
        self.assertIn('pass-with-exception', v['g6_note'])

    def test_g6_fail_when_unreconciled_left(self):
        s = _state()
        entries = rf.enumerate_fills(s, WS, WE)
        v = rf.g6_verdict(s, entries, [])  # 未生成 reconciliation → ③>0 → fail
        self.assertEqual(v['unresolved_legacy_in_window'], 1)
        self.assertFalse(v['g6_pass'])


class MainTests(unittest.TestCase):
    """main() 行为：dry-run 不落盘 / --commit 落盘幂等 / 全程零触生产 ledger（load_ledger 打桩）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self._tmp.name)
        self._orig = (rf.RECONCILED, rf.EXEMPT, rf.RISK_EVENTS, rf.load_ledger)
        rf.RECONCILED = tmp / 'reconciled_fills.jsonl'
        rf.EXEMPT = tmp / 'legacy_fills_exempt.json'
        rf.RISK_EVENTS = tmp / 'missing_risk_events.jsonl'  # 不存在 → 空 execute 索引
        self._state = _state()
        rf.load_ledger = lambda: self._state
        self._argv = sys.argv[:]

    def tearDown(self):
        rf.RECONCILED, rf.EXEMPT, rf.RISK_EVENTS, rf.load_ledger = self._orig
        sys.argv = self._argv
        self._tmp.cleanup()

    def test_dry_run_writes_nothing(self):
        sys.argv = ['reconcile_fills.py', '--window-start', WS, '--window-end', WE]
        self.assertEqual(rf.main(), 0)
        self.assertFalse(rf.RECONCILED.exists())
        self.assertFalse(rf.EXEMPT.exists())
        # dry-run 亦不修改内存 state 的 fills
        self.assertEqual(self._state['account']['fills'], _state()['account']['fills'])

    def test_commit_appends_and_idempotent(self):
        sys.argv = ['reconcile_fills.py', '--window-start', WS, '--window-end', WE, '--commit']
        self.assertEqual(rf.main(), 0)
        lines = rf.RECONCILED.read_text(encoding='utf-8').strip().splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row['original_decision_id'], 'dec-tick-20260902-300489')
        self.assertTrue(row['retroactive'])
        self.assertFalse(row['original_provenance_valid'])
        ex = json.loads(rf.EXEMPT.read_text(encoding='utf-8'))
        self.assertEqual(len(ex['entries']), 2)
        self.assertEqual(ex['window'], {'start': WS, 'end': WE})
        # 第二次 commit：同 original_fill_hash 幂等跳过，不重复追加
        self.assertEqual(rf.main(), 0)
        lines2 = rf.RECONCILED.read_text(encoding='utf-8').strip().splitlines()
        self.assertEqual(len(lines2), 1)
        self.assertEqual(lines2[0], lines[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
