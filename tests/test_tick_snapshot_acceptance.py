from __future__ import annotations
import json,pathlib,sys,tempfile,unittest
from datetime import datetime
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import collect_daily_acceptance as acc
class TickSnapshotAcceptanceTests(unittest.TestCase):
 def test_source_requires_tick_snapshot(self):
  source=(ROOT/'scripts'/'collect_daily_acceptance.py').read_text(encoding='utf-8')
  self.assertIn("'tick_snapshot'",source);self.assertIn("pos_live.json",source)
 def test_fresh_snapshot_shape(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);p=base/'outputs'/'intraday';p.mkdir(parents=True);(p/'pos_live.json').write_text(json.dumps({'date':'2026-08-31','time':'14:59:55'}),encoding='utf-8')
   row=acc.read_json(p/'pos_live.json');self.assertEqual(row['date'],'2026-08-31');self.assertTrue(row['time'])
if __name__=='__main__':unittest.main(verbosity=2)
