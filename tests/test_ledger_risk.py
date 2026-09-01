from __future__ import annotations
import copy, json, pathlib, sys, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portfolio'))
import ledger

class LedgerRiskTests(unittest.TestCase):
 def state(self):
  s=ledger._default_state(); s['policy']['require_human_decision']=True; return s
 def approve(self,s,sym='000001',lo=9,hi=11):
  rid=ledger.record_signal_request(s,{'sym':sym,'kind':'test'})
  return ledger.record_human_decision(s,rid,'approve','逐妖','test',lo,hi)
 def test_human_decision_required(self):
  s=self.state()
  with self.assertRaisesRegex(ValueError,'人工确认'): ledger.buy(s,'000001','2026-08-31 09:40',10,100,'P')
 def test_45pct_and_90pct(self):
  s=self.state(); did=self.approve(s)
  ledger.buy(s,'000001','2026-08-31 09:40',10,4000,'P',decision_id=did)
  with self.assertRaisesRegex(ValueError,'单票权重'): ledger.buy(s,'000001','2026-08-31 09:45',10,600,'P',decision_id=did)
 def test_daily_one_new_symbol(self):
  s=self.state(); d1=self.approve(s,'000001'); ledger.buy(s,'000001','2026-08-31 09:40',10,1000,'P',decision_id=d1)
  d2=self.approve(s,'000002')
  with self.assertRaisesRegex(ValueError,'当日新买入'): ledger.buy(s,'000002','2026-08-31 10:00',10,100,'P',decision_id=d2)
 def test_transition_reduce_only(self):
  s=self.state(); s['policy']['transition_reduce_only']=True; d=self.approve(s)
  with self.assertRaisesRegex(ValueError,'只减不增'): ledger.buy(s,'000001','2026-08-31 09:40',10,100,'P',decision_id=d)
 def test_t1_old_bottom_can_sell_after_t_buy(self):
  s=ledger._default_state(); s['policy']['require_human_decision']=False; s['policy']['account_mode']='autonomous_paper'
  s['account']['cash']=100000; ledger.buy(s,'000001','2026-08-28 09:40',10,1000,'P')
  ledger.t_buy(s,'000001','2026-08-31 09:40',9,100)
  self.assertEqual(ledger.sellable_qty(s,'000001','2026-08-31'),1000)
  ledger.t_sell(s,'000001','2026-08-31 10:10',10,1000)
  self.assertEqual(s['account']['positions']['000001']['qty'],100)
 def test_equity_upsert(self):
  s=ledger._default_state(); s['account']['cash']=100
  ledger.equity(s,'2026-08-31',{}); ledger.equity(s,'2026-08-31',{})
  self.assertEqual(len(s['account']['equity_curve']),1)

if __name__=='__main__': unittest.main(verbosity=2)
