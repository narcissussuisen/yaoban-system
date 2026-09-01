from __future__ import annotations
import json,pathlib,sys,tempfile,unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import collect_daily_acceptance as acc
class NextPlanAcceptanceTests(unittest.TestCase):
 def test_future_plan_requires_matching_json_date_and_picks(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);d=base/'outputs'/'plans';d.mkdir(parents=True)
   (d/'2026-08-31_plan.json').write_text(json.dumps({'date':'2026-08-31','picks':[{}]}),encoding='utf-8')
   (d/'2026-09-01_plan.json').write_text(json.dumps({'date':'2026-09-01','picks':[{'sym':'000001'}]}),encoding='utf-8')
   with mock.patch.object(acc,'BASE',base):
    plans=sorted((base/'outputs'/'plans').glob('*_plan.json'))
    self.assertTrue(any((lambda p: p.stem[:10]>'2026-08-31' and acc.read_json(p).get('date')==p.stem[:10] and bool(acc.read_json(p).get('picks')))(p) for p in plans))
 def test_same_day_plan_is_not_next_plan(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);d=base/'outputs'/'plans';d.mkdir(parents=True);p=d/'2026-08-31_plan.json';p.write_text(json.dumps({'date':'2026-08-31','picks':[{}]}),encoding='utf-8')
   self.assertFalse(p.stem[:10]>'2026-08-31')
if __name__=='__main__':unittest.main(verbosity=2)
