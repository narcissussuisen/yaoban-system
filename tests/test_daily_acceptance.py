from __future__ import annotations
import pathlib,sys,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import collect_daily_acceptance as acceptance
class AcceptanceContractTests(unittest.TestCase):
 def test_collector_does_not_require_itself(self):
  source=(ROOT/'scripts'/'collect_daily_acceptance.py').read_text(encoding='utf-8')
  required=source.split('required_task_names=',1)[1].split('\n',1)[0]
  self.assertNotIn('YaobanDailyAcceptance',required)
 def test_tick_daemon_is_required(self):
  source=(ROOT/'scripts'/'collect_daily_acceptance.py').read_text(encoding='utf-8')
  required=source.split('required_task_names=',1)[1].split('\n',1)[0]
  self.assertIn('YaobanTickDaemon',required)
 def test_task_time_is_iso_encoded(self):
  source=(ROOT/'scripts'/'collect_daily_acceptance.py').read_text(encoding='utf-8')
  self.assertIn("ToString('yyyy-MM-dd HH:mm:ss')",source)
 def test_acceptance_requires_auction_evidence(self):
  source=(ROOT/'scripts'/'collect_daily_acceptance.py').read_text(encoding='utf-8')
  for token in ("'auction_latest'","'auction_freeze'","'auction_delivery'",'YaobanAuctionMonitor'):
   self.assertIn(token,source)
 def test_acceptance_requires_both_deliveries_and_ticks(self):
  source=(ROOT/'scripts'/'collect_daily_acceptance.py').read_text(encoding='utf-8')
  for token in ("'premarket_delivery'","'close_delivery'","'live_tick'","'next_plan'","'tasks'"):
   self.assertIn(token,source)
if __name__=='__main__':unittest.main(verbosity=2)
