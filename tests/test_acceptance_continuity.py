from __future__ import annotations
import json,pathlib,sys,tempfile,unittest
from datetime import datetime,timedelta
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import collect_daily_acceptance as acc
class ContinuityTests(unittest.TestCase):
 def write(self,base,mode,dt,code=0):
  d=base/'outputs'/'task_logs'/'2026-08-31';d.mkdir(parents=True,exist_ok=True);stamp=dt.strftime('%Y%m%d_%H%M%S_000')
  (d/f'{stamp}_{mode}.json').write_text(json.dumps({'date':'2026-08-31','started_at':stamp,'exit_code':code}),encoding='utf-8')
 def test_complete_sessions_pass(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);day=datetime(2026,8,31)
   for m in range(15,30):self.write(base,'auction',day.replace(hour=9,minute=m))
   for mode in ('scan','monitor'):
    for start,end in ((day.replace(hour=9,minute=30),day.replace(hour=11,minute=30)),(day.replace(hour=13),day.replace(hour=15))):
     t=start
     while t<=end:self.write(base,mode,t);t+=timedelta(minutes=10)
   t=day.replace(hour=9,minute=15)
   while t<=day.replace(hour=15):self.write(base,'notify',t);t+=timedelta(minutes=10)
   with mock.patch.object(acc,'BASE',base):checks,_=acc.continuity('2026-08-31')
   self.assertTrue(all(checks.values()),checks)
 def test_monitor_gap_fails(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);day=datetime(2026,8,31)
   for t in (day.replace(hour=9,minute=30),day.replace(hour=10),day.replace(hour=11,minute=25),day.replace(hour=13),day.replace(hour=14,minute=55)):self.write(base,'scan',t)
   with mock.patch.object(acc,'BASE',base):checks,_=acc.continuity('2026-08-31')
   self.assertFalse(checks['scan'])
if __name__=='__main__':unittest.main(verbosity=2)
