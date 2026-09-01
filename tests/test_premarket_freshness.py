from __future__ import annotations
import hashlib,json,pathlib,sys,tempfile,unittest
from datetime import datetime
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import premarket
class PremarketFreshnessTests(unittest.TestCase):
 def test_plan_records_snapshot_hash_and_refresh_time(self):
  with tempfile.TemporaryDirectory() as td:
   base=pathlib.Path(td);(base/'outputs'/'plans').mkdir(parents=True)
   plan=base/'outputs'/'plans'/'2026-08-31_plan.json';plan.write_text('{"date":"2026-08-31","picks":[{"sym":"000001"}]}',encoding='utf-8')
   raw=b'{"usINX":{"chg_pct":1.2}}'
   def run(*args,**kwargs):(base/'outputs'/'global_snapshot.json').write_bytes(raw);return mock.Mock(returncode=0)
   fixed=mock.Mock(wraps=datetime);fixed.now.return_value=datetime(2026,8,31,8,50,3)
   with mock.patch.object(premarket,'BASE',base),mock.patch.object(premarket,'datetime',fixed),mock.patch.object(premarket.subprocess,'run',side_effect=run),mock.patch.object(sys,'argv',['premarket.py','--date','2026-08-31']):
    self.assertEqual(premarket.main(),0)
   data=json.loads(plan.read_text(encoding='utf-8'))
   self.assertEqual(data['premarket_refreshed_at'],'2026-08-31 08:50:03')
   self.assertEqual(data['global_snapshot_sha256'],hashlib.sha256(raw).hexdigest())
   self.assertEqual(data['global'],json.loads(raw))
 def test_preflight_requires_content_hash(self):
  source=(ROOT/'scripts'/'preflight.py').read_text(encoding='utf-8')
  self.assertIn("premarket_refreshed_at",source);self.assertIn("global_snapshot_sha256",source);self.assertIn("hashlib.sha256(snapshot.read_bytes())",source)
if __name__=='__main__':unittest.main(verbosity=2)
