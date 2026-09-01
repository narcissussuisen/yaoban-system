from __future__ import annotations
import pathlib,sys,tempfile,unittest
from datetime import datetime
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'portfolio'))
import auction_monitor as auction

class AuctionMonitorTests(unittest.TestCase):
 def quote(self):return {'000001':{'name':'平安银行','px':10.2,'chg':2.0,'vol':10000,'amt':5000,'turn':1.2}}
 def test_freeze_is_read_only_after_0925(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);(base/'outputs'/'plans').mkdir(parents=True);(base/'outputs'/'auction').mkdir(parents=True)
   (base/'outputs'/'plans'/'2026-08-31_plan.json').write_text('{"picks":[{"sym":"000001"}]}',encoding='utf-8')
   with mock.patch.object(auction,'BASE',base),mock.patch.object(auction,'OUT',base/'outputs'/'auction'),mock.patch.object(auction,'load_universe',return_value=['000001']),mock.patch.object(auction,'fetch_batch',return_value=self.quote()),mock.patch.object(auction,'load',return_value={'account':{'positions':{}}}):
    self.assertEqual(auction.observe(datetime(2026,8,31,9,25)),0)
   import json
   frozen=json.loads((base/'outputs'/'auction'/'auction_freeze_2026-08-31.json').read_text(encoding='utf-8'))
   self.assertFalse(frozen['orders_allowed']);self.assertTrue(frozen['read_only']);self.assertEqual(frozen['picks'][0]['sym'],'000001')
 def test_incomplete_source_raises(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);(base/'outputs'/'plans').mkdir(parents=True);(base/'outputs'/'auction').mkdir(parents=True)
   (base/'outputs'/'plans'/'2026-08-31_plan.json').write_text('{"picks":[]}',encoding='utf-8')
   with mock.patch.object(auction,'BASE',base),mock.patch.object(auction,'OUT',base/'outputs'/'auction'),mock.patch.object(auction,'load_universe',return_value=['000001']),mock.patch.object(auction,'fetch_batch',side_effect=OSError('down')),mock.patch.object(auction,'load',return_value={'account':{'positions':{}}}):
    with self.assertRaises(RuntimeError):auction.observe(datetime(2026,8,31,9,20))
 def test_source_contains_no_order_functions(self):
  source=(ROOT/'scripts'/'auction_monitor.py').read_text(encoding='utf-8')
  self.assertNotIn('from ledger import buy',source);self.assertNotIn('transact(',source);self.assertIn("'orders_allowed':False",source)
if __name__=='__main__':unittest.main(verbosity=2)
