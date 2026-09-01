from __future__ import annotations
import json,pathlib,sys,tempfile,unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import notify_trading_events as events
class TradingEventTests(unittest.TestCase):
 def test_reads_tick_risk_and_data_failure(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);d=base/'outputs'/'intraday';d.mkdir(parents=True)
   rows=[{'date':'2026-08-31','time':'10:00:00','sym':'000001','trigger':'stop_loss','action':'execute'}, {'date':'2026-08-31','time':'10:01:00','sym':'000002','trigger':'data_failure','action':'halt','detail':'feed down'}, {'date':'2026-08-30','time':'10:02:00','sym':'000003','trigger':'old','action':'halt'}]
   (d/'risk_events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
   with mock.patch.object(events,'BASE',base): got=events._risk_rows('2026-08-31')
   self.assertEqual([x['rule'] for x in got],['stop_loss','data_failure'])
   self.assertEqual(got[1]['detail'],'feed down')
if __name__=='__main__':unittest.main(verbosity=2)
