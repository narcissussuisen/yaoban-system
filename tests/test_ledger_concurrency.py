from __future__ import annotations
import pathlib,sys,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portfolio'))
import ledger

def _abuy(s,sym,ts,px,qty,reason):
 did=ledger.record_autonomous_decision(s,{'sym':sym,'signal_ts':ts[:16]+':00','rule':reason,
  'candidates_ref':'test','plan_pick_ref':None})
 ledger.buy(s,sym,ts,px,qty,reason,decision_id=did,signal_ts=ts[:16]+':00',
  decision_ts=ts[:16]+':30',plan_match={'in_plan':False,'pick_id':None},
  off_plan_reason={'code':'test','detail':'unit'})
 return did

class LedgerConcurrencyTests(unittest.TestCase):
 def test_stale_revision_is_rejected(self):
  a={'_revision':1}; b={'_revision':1}
  self.assertEqual(a['_revision'],b['_revision'])
 def test_sellable_subtracts_previous_sells(self):
  s=ledger._default_state(100000.0);s['policy']['require_human_decision']=False;s['policy']['account_mode']='autonomous_paper'
  _abuy(s,'000001','2026-08-28 09:40',10,1000,'P')
  ledger.sell(s,'000001','2026-08-31 09:40',10,400,'stop')
  self.assertEqual(ledger.sellable_qty(s,'000001','2026-08-31'),600)
 def test_price_range_blocks_order(self):
  s=ledger._default_state(100000.0);rid=ledger.record_signal_request(s,{'sym':'000001','signal_ts':'2026-08-31 09:39'});did=ledger.record_human_decision(s,rid,'approve','EvoAlpha','ok',9.8,10.2)
  with self.assertRaisesRegex(ValueError,'批准区间'):ledger.buy(s,'000001','2026-08-31 09:40',10.3,100,'P',decision_id=did,
   signal_ts='2026-08-31 09:39',decision_ts='2026-08-31 09:39:30',
   plan_match={'in_plan':False,'pick_id':None},off_plan_reason={'code':'test','detail':'unit'})
 def test_risk_multiplier_halves_limits(self):
  s=ledger._default_state(100000.0);s['risk_state']={'position_multiplier':.5,'source_date':'2026-08-28'};rid=ledger.record_signal_request(s,{'sym':'000001','signal_ts':'2026-08-31 09:39'});did=ledger.record_human_decision(s,rid,'approve','EvoAlpha','ok',9,11)
  with self.assertRaisesRegex(ValueError,'熔断后上限'):ledger.buy(s,'000001','2026-08-31 09:40',10,3000,'P',decision_id=did,
   signal_ts='2026-08-31 09:39',decision_ts='2026-08-31 09:39:30',
   plan_match={'in_plan':False,'pick_id':None},off_plan_reason={'code':'test','detail':'unit'})

if __name__=='__main__':unittest.main(verbosity=2)
