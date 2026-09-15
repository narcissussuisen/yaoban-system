from __future__ import annotations
import copy, json, pathlib, sys, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portfolio'))
import ledger

def _abuy(s,sym,ts,px,qty,reason,signal_ts=None,decision_ts=None,plan_match=None,off_plan_reason=None):
 """autonomous_paper 合规买入辅助（P0.2/P0.4 后必须带决策登记+时序契约）。"""
 if signal_ts is None: signal_ts=ts[:10]+' '+ts[11:16]
 if decision_ts is None: decision_ts=signal_ts+':30' if len(signal_ts)==16 else signal_ts
 did=ledger.record_autonomous_decision(s,{'sym':sym,'signal_ts':signal_ts,'rule':reason,
  'candidates_ref':'test','plan_pick_ref':(plan_match or {}).get('pick_id')})
 ledger.buy(s,sym,ts,px,qty,reason,decision_id=did,signal_ts=signal_ts,decision_ts=decision_ts,
  plan_match=plan_match or {'in_plan':False,'pick_id':None},
  off_plan_reason=off_plan_reason or {'code':'test','detail':'unit'})
 return did

class LedgerRiskTests(unittest.TestCase):
 def state(self):
  s=ledger._default_state(100000.0); s['policy']['require_human_decision']=True; return s
 def approve(self,s,sym='000001',lo=9,hi=11):
  rid=ledger.record_signal_request(s,{'sym':sym,'kind':'test','signal_ts':'2026-08-31 09:39'})
  return ledger.record_human_decision(s,rid,'approve','EvoAlpha','test',lo,hi)
 def _hbuy(self,s,sym,ts,px,qty,reason,did):
  ledger.buy(s,sym,ts,px,qty,reason,decision_id=did,signal_ts='2026-08-31 09:39',
   decision_ts='2026-08-31 09:39:30',plan_match={'in_plan':False,'pick_id':None},
   off_plan_reason={'code':'test','detail':'unit'})
 def test_human_decision_required(self):
  s=self.state()
  with self.assertRaisesRegex(ValueError,'decision_id 缺失'): ledger.buy(s,'000001','2026-08-31 09:40',10,100,'P')
 def test_single_weight_cap(self):
  """单票权重上限边界。

  2026-09-13：上限 0.45 → 0.30（对齐 SOP「单票 ≤30%」，见 `ledger.DEFAULT_POLICY` 注释）。
  ⚠️ 本测试原名 `test_45pct_and_90pct`，但代码只测了单票、从未测毛敞口 ——
  因为 `max_positions=2 × max_single_weight=0.30 = 60% < max_gross_exposure=0.90`，
  **毛敞口上限在当前参数下不可达（死约束）**，故只保留单票边界。
  ⚠️ 数值须含成本：`buy_net(10) = 10 × (1+0.00025+0.001) = 10.0125`（裸算 3000 股会刚好越界）。
  """
  s=self.state(); did=self.approve(s)
  # 10 万权益：2900 股 @10.0125 = 29.04% → 通过
  self._hbuy(s,'000001','2026-08-31 09:40',10,2900,'P',did)
  # 再 200 股 → 31.04% > 30% → 拒绝
  with self.assertRaisesRegex(ValueError,'单票权重'): self._hbuy(s,'000001','2026-08-31 09:45',10,200,'P',did)
 def test_daily_one_new_symbol(self):
  s=self.state(); d1=self.approve(s,'000001'); self._hbuy(s,'000001','2026-08-31 09:40',10,1000,'P',d1)
  d2=self.approve(s,'000002')
  with self.assertRaisesRegex(ValueError,'当日新买入'): self._hbuy(s,'000002','2026-08-31 10:00',10,100,'P',d2)
 def test_transition_reduce_only(self):
  s=self.state(); s['policy']['transition_reduce_only']=True; d=self.approve(s)
  with self.assertRaisesRegex(ValueError,'只减不增'): self._hbuy(s,'000001','2026-08-31 09:40',10,100,'P',d)
 def test_t1_old_bottom_can_sell_after_t_buy(self):
  s=ledger._default_state(100000.0); s['policy']['require_human_decision']=False; s['policy']['account_mode']='autonomous_paper'
  s['account']['cash']=100000; _abuy(s,'000001','2026-08-28 09:40',10,1000,'P')
  ledger.t_buy(s,'000001','2026-08-31 09:40',9,100)
  self.assertEqual(ledger.sellable_qty(s,'000001','2026-08-31'),1000)
  ledger.t_sell(s,'000001','2026-08-31 10:10',10,1000)
  self.assertEqual(s['account']['positions']['000001']['qty'],100)
 def test_equity_upsert(self):
  s=ledger._default_state(100000.0); s['account']['cash']=100
  ledger.equity(s,'2026-08-31',{}); ledger.equity(s,'2026-08-31',{})
  self.assertEqual(len(s['account']['equity_curve']),1)

if __name__=='__main__': unittest.main(verbosity=2)
