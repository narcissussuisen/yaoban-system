from __future__ import annotations
import json,pathlib,sys,tempfile,unittest
from datetime import datetime,timedelta
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import notify_trading_events as events
class MonitorWatchdogTests(unittest.TestCase):
 def write_log(self,base,mode,finished):
  d=base/'outputs'/'task_logs'/'2026-08-31';d.mkdir(parents=True,exist_ok=True);stamp=finished.strftime('%Y%m%d_%H%M%S_000')
  (d/f'{stamp}_{mode}.json').write_text(json.dumps({'date':'2026-08-31','started_at':stamp,'finished_at':finished.strftime('%Y-%m-%d %H:%M:%S.000'),'exit_code':0}),encoding='utf-8')
 def test_grace_period_has_no_false_alarm(self):
  with tempfile.TemporaryDirectory() as td:
   with mock.patch.object(events,'BASE',pathlib.Path(td)):self.assertEqual(events.monitoring_gaps('2026-08-31',datetime(2026,8,31,9,16)),{})
 def test_stale_opening_modules_are_reported(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);now=datetime(2026,8,31,9,45)
   for mode in ('scan','monitor'):self.write_log(base,mode,now-timedelta(minutes=16))
   with mock.patch.object(events,'BASE',base):gaps=events.monitoring_gaps('2026-08-31',now)
   self.assertEqual(set(gaps),{'scan','monitor','tick'})
 def test_fresh_modules_are_healthy(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);now=datetime(2026,8,31,10,0)
   for mode in ('scan','monitor'):self.write_log(base,mode,now-timedelta(minutes=2))
   d=base/'outputs'/'intraday';d.mkdir(parents=True);(d/'pos_live.json').write_text(json.dumps({'date':'2026-08-31','time':'09:59:30'}),encoding='utf-8')
   with mock.patch.object(events,'BASE',base):self.assertEqual(events.monitoring_gaps('2026-08-31',now),{})
 def test_runner_has_single_notifier_writer(self):
  source=(ROOT/'scripts'/'run_trading_task.ps1').read_text(encoding='utf-8')
  self.assertEqual(source.count('$NotifyEvents --date $Day'),1)
if __name__=='__main__':unittest.main(verbosity=2)
