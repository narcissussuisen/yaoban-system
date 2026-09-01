from __future__ import annotations
import json,pathlib,sys,tempfile,unittest
from datetime import datetime,timedelta
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import scan_and_confirm as scan
class CompanionHealthTests(unittest.TestCase):
 def test_opening_grace(self):self.assertTrue(scan.companion_health('2026-08-31',datetime(2026,8,31,9,35))[0])
 def test_missing_companions_fail(self):
  with tempfile.TemporaryDirectory() as td:
   with mock.patch.object(scan,'BASE',pathlib.Path(td)),mock.patch.object(scan,'OUT',pathlib.Path(td)/'outputs'/'intraday'):
    self.assertFalse(scan.companion_health('2026-08-31',datetime(2026,8,31,10))[0])
 def test_fresh_companions_pass(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);now=datetime(2026,8,31,10);logs=base/'outputs'/'task_logs'/'2026-08-31';logs.mkdir(parents=True);out=base/'outputs'/'intraday';out.mkdir(parents=True)
   ts=now-timedelta(minutes=2);stamp=ts.strftime('%Y%m%d_%H%M%S_000');(logs/f'{stamp}_monitor.json').write_text(json.dumps({'date':'2026-08-31','exit_code':0,'finished_at':ts.strftime('%Y-%m-%d %H:%M:%S.000')}),encoding='utf-8')
   (out/'pos_live.json').write_text(json.dumps({'date':'2026-08-31','time':'09:59:30'}),encoding='utf-8')
   with mock.patch.object(scan,'BASE',base),mock.patch.object(scan,'OUT',out):self.assertEqual(scan.companion_health('2026-08-31',now),(True,'ok'))
if __name__=='__main__':unittest.main(verbosity=2)
