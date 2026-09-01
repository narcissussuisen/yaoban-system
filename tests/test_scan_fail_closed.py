from __future__ import annotations
import pathlib, sys, unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'portfolio'));sys.path.insert(0,str(ROOT/'src'))
import scan_and_confirm as scan

class ScanFailClosedTests(unittest.TestCase):
 def account(self):
  return {'account':{'positions':{}},'policy':{'account_mode':'autonomous_paper','require_human_decision':False}}
 @mock.patch.object(scan,'load_universe',return_value=['000001'])
 @mock.patch.object(scan,'load',return_value={'account':{'positions':{}}})
 @mock.patch('pandas.read_csv',side_effect=OSError('missing sentiment'))
 def test_missing_temperature_fails_closed(self,_pd,_load,_universe):
  with mock.patch.object(sys,'argv',['scan','--force','--temp-ladder']):
   self.assertEqual(scan.main(),3)
 @mock.patch.object(scan,'fetch_batch',side_effect=OSError('feed down'))
 @mock.patch.object(scan,'load_universe',return_value=['000001'])
 @mock.patch.object(scan,'load',return_value={'account':{'positions':{}}})
 def test_incomplete_quote_batch_fails_closed(self,_load,_universe,_fetch):
  with mock.patch.object(sys,'argv',['scan','--force']):
   self.assertEqual(scan.main(),4)
 def test_authorized_universe(self):
  for symbol in ('600000','601001','603001','605001','000001','001001','002001','003001','300001','301001'):
   self.assertTrue(scan.is_authorized_symbol(symbol),symbol)
  for symbol in ('688001','689001','430001','830001','900001','200001','510300'):
   self.assertFalse(scan.is_authorized_symbol(symbol),symbol)

if __name__=='__main__':unittest.main(verbosity=2)
