from __future__ import annotations
import json,pathlib,sys,tempfile,unittest
from datetime import datetime
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import notify_trading_events as events
class WatchdogDeliveryStateTests(unittest.TestCase):
 def test_failed_gap_delivery_is_not_committed(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);now=datetime(2026,8,31,9,45)
   with mock.patch.object(events,'BASE',base),mock.patch.object(events,'monitoring_gaps',return_value={'scan':'stale'}),mock.patch.object(events,'send_text',return_value=False):
    self.assertEqual(events.notify_monitoring_health('2026-08-31',now),(0,1))
   state=json.loads((base/'outputs'/'notifications'/'monitor_health_2026-08-31.json').read_text(encoding='utf-8'));self.assertEqual(state['active'],{})
 def test_failed_recovery_keeps_active_episode(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);p=base/'outputs'/'notifications';p.mkdir(parents=True);f=p/'monitor_health_2026-08-31.json';f.write_text(json.dumps({'active':{'scan':'stale'}}),encoding='utf-8')
   with mock.patch.object(events,'BASE',base),mock.patch.object(events,'monitoring_gaps',return_value={}),mock.patch.object(events,'send_text',return_value=False):events.notify_monitoring_health('2026-08-31',datetime(2026,8,31,10))
   self.assertIn('scan',json.loads(f.read_text(encoding='utf-8'))['active'])
if __name__=='__main__':unittest.main(verbosity=2)
