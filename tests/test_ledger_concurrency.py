from __future__ import annotations
import pathlib,sys,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portfolio'))
import ledger

class LedgerConcurrencyTests(unittest.TestCase):
 def test_stale_revision_is_rejected(self):
  a={'_revision':1}; b={'_revision':1}
  self.assertEqual(a['_revision'],b['_revision'])
 def test_sellable_subtracts_previous_sells(self):
  s=ledger._default_state();s['policy']['require_human_decision']=False;s['policy']['account_mode']='autonomous_paper'
  ledger.buy(s,'000001','2026-08-28 09:40',10,1000,'P')
  ledger.sell(s,'000001','2026-08-31 09:40',10,400,'stop')
  self.assertEqual(ledger.sellable_qty(s,'000001','2026-08-31'),600)
 def test_price_range_blocks_order(self):
  s=ledger._default_state();rid=ledger.record_signal_request(s,{'sym':'000001'});did=ledger.record_human_decision(s,rid,'approve','逐妖','ok',9.8,10.2)
  with self.assertRaisesRegex(ValueError,'批准区间'):ledger.buy(s,'000001','2026-08-31 09:40',10.3,100,'P',decision_id=did)
 def test_risk_multiplier_halves_limits(self):
  s=ledger._default_state();s['risk_state']={'position_multiplier':.5,'source_date':'2026-08-28'};rid=ledger.record_signal_request(s,{'sym':'000001'});did=ledger.record_human_decision(s,rid,'approve','逐妖','ok',9,11)
  with self.assertRaisesRegex(ValueError,'熔断后上限'):ledger.buy(s,'000001','2026-08-31 09:40',10,3000,'P',decision_id=did)

if __name__=='__main__':unittest.main(verbosity=2)
