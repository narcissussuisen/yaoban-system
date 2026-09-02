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


class TickRiskSellPathTests(unittest.TestCase):
 """C1(计划批次C1/§3.4 真实路径测试): tick 卖出 provenance。

 驱动从 tick_monitor.py 提取的模块级 execute_tick_risk_sell(替代原 mut 闭包), 断言:
 ① autonomous_decisions[did] 存在、sym 匹配、signal_ts 合法;
 ② 产生的 fill decision_id 可从账本反查 decision;
 ③ decision_id 不匹配 dec-tick-* 模式(人工拼接 ID 已退役);
 辅助回归锚: 源码中登记调用位置早于 sell( 调用(登记在卖出前)。
 """
 @staticmethod
 def _import_tick_monitor():
  import importlib.util
  fp=pathlib.Path(ledger.__file__).resolve().parents[1]/'scripts'/'tick_monitor.py'
  spec=importlib.util.spec_from_file_location('tick_monitor_under_test',fp)
  m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  return m

 def setUp(self):
  self.tm=self._import_tick_monitor()
  self.s=_auto_state()
  self.s['account']['positions']['000001']={'qty':1000,'cost':10.0,
   'entry_ts':'2026-08-28 10:06','stop_px':9.5,'days':0}
  # 前日买入 → T+1 可卖(sellable_qty 依据 fills 的 buy/sell 差额)
  self.s['account']['fills'].append({'date':'2026-08-28','ts':'2026-08-28 10:06',
   'sym':'000001','side':'buy','qty':1000,'px':10.0,'reason':'P',
   'plan_ref':'plan-x','decision_id':'','recorded_at':''})

 def test_register_before_sell_and_fill_traceable(self):
  res=self.tm.execute_tick_risk_sell(self.s,'000001','2026-08-29','09:40:00',9.8,400,'stop_loss')
  did=res['decision_id']
  self.assertTrue(did.startswith('dec-auto-'))              # 登记返回合法 dec-auto-*
  self.assertNotRegex(did,r'^dec-tick-')                    # ③ 人工拼接 ID 已退役
  self.assertIn(did,self.s['autonomous_decisions'])         # ① 决策登记可反查
  dec=self.s['autonomous_decisions'][did]
  self.assertEqual(dec['sym'],'000001')
  self.assertEqual(dec['signal_ts'],'2026-08-29 09:40:00')
  self.assertEqual(dec['rule'],'tick_risk:stop_loss')
  self.assertEqual(dec['plan_ref'],'tick-risk')
  fill=self.s['account']['fills'][-1]                       # ② fill 可反查 decision
  self.assertEqual(fill['decision_id'],did)
  self.assertEqual(fill['side'],'sell')
  self.assertEqual(fill['qty'],400)
  self.assertEqual(fill['px'],9.8)
  self.assertEqual(fill['signal_ts'],'2026-08-29 09:40:00')
  self.assertEqual(fill['decision_ts'],'2026-08-29 09:40:00')

 def test_qty_floored_to_board_lot(self):
  res=self.tm.execute_tick_risk_sell(self.s,'000001','2026-08-29','09:40:00',9.8,450,'stop_loss')
  self.assertEqual(res['qty'],400)  # min(450,1000)//100*100

 def test_no_sellable_qty_rejected(self):
  s=_auto_state()  # 无持仓无 fills
  with self.assertRaisesRegex(ValueError,'无可卖份额'):
   self.tm.execute_tick_risk_sell(s,'000001','2026-08-29','09:40:00',9.8,400,'stop_loss')

 def test_non_autonomous_mode_rejected(self):
  s=ledger._default_state()  # require_human_decision=True
  s['account']['positions']['000001']={'qty':1000,'cost':10.0,'entry_ts':'2026-08-28 10:06','days':0}
  with self.assertRaisesRegex(ValueError,'autonomous_paper'):
   self.tm.execute_tick_risk_sell(s,'000001','2026-08-29','09:40:00',9.8,400,'stop_loss')

 def test_source_anchor_register_before_sell(self):
  import re
  src=pathlib.Path(self.tm.__file__).read_text(encoding='utf-8')
  m_reg=re.search(r'\brecord_autonomous_decision\(',src)
  m_sell=re.search(r'\bsell\(',src)  # \b 排除 execute_tick_risk_sell/sellable_qty 内嵌
  self.assertIsNotNone(m_reg);self.assertIsNotNone(m_sell)
  self.assertLess(m_reg.start(),m_sell.start(),
   '辅助回归锚: 登记调用必须出现在 sell( 调用之前')

 def test_timezone_pinned_shanghai(self):
  src=pathlib.Path(self.tm.__file__).read_text(encoding='utf-8')
  self.assertIn("ZoneInfo('Asia/Shanghai')",src)  # C1 顺带: L52/L77 时区加固(A1b 同口径)
  self.assertNotIn('datetime.now()',src)           # 裸 datetime.now() 清零


if __name__ == '__main__':
    unittest.main(verbosity=2)
