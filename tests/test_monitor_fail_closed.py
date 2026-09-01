from __future__ import annotations
import json,pathlib,sys,tempfile,unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'portfolio'))
import monitor_intraday as monitor

class FakeNow:
 @classmethod
 def now(cls):
  from datetime import datetime
  return datetime(2026,8,31,10,0)

class MonitorFailClosedTests(unittest.TestCase):
 def test_missing_daily_plan_fails_closed(self):
  with tempfile.TemporaryDirectory() as td, mock.patch.object(monitor,'BASE',pathlib.Path(td)), mock.patch.object(monitor,'datetime',FakeNow), mock.patch.object(monitor,'load_ledger',return_value={'account':{'positions':{}}}):
   self.assertEqual(monitor.main(),2)
 def test_source_contract_reads_plan_file(self):
  source=(ROOT/'scripts'/'monitor_intraday.py').read_text(encoding='utf-8')
  self.assertIn("outputs' / 'plans'",source)
  self.assertIn('unavailable',source)
  self.assertIn('raise SystemExit(main())',source)
 def test_fill_uses_execution_timestamp(self):
  source=(ROOT/'scripts'/'scan_and_confirm.py').read_text(encoding='utf-8')
  self.assertIn("'exec_ts': exec_ts",source)
  self.assertIn('tg["exec_ts"]',source)

if __name__=='__main__':unittest.main(verbosity=2)
