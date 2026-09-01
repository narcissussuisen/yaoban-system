from __future__ import annotations
import pathlib,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class GateFreshnessTests(unittest.TestCase):
 def test_failure_message_includes_gate_details(self):
  source=(ROOT/'scripts'/'run_trading_task.ps1').read_text(encoding='utf-8')
  for token in ('Failed checks:', 'Report:', '$j.results', 'critical'):
   self.assertIn(token,source)

 def test_wrapper_rejects_stale_gate(self):
  source=(ROOT/'scripts'/'run_trading_task.ps1').read_text(encoding='utf-8')
  for token in ("ParseExact","generated.ToString('yyyy-MM-dd') -ne $Day","gate-stale-","$j.stage -ne $Stage","$j.date -ne $Day"):
   self.assertIn(token,source)
 def test_wrapper_has_no_unicode_path_literal(self):
  source=(ROOT/'scripts'/'run_trading_task.ps1').read_text(encoding='utf-8')
  self.assertIn('root.txt',source)
  self.assertNotIn('方法论与研究文档',source)
 def test_post_plan_expires_after_close(self):
  source=(ROOT/'scripts'/'run_trading_task.ps1').read_text(encoding='utf-8')
  self.assertIn("$afterClose=$Stage -eq 'post_plan'",source)
  self.assertIn("-gt '15:05'",source)
 def test_infra_is_short_lived(self):
  source=(ROOT/'scripts'/'run_trading_task.ps1').read_text(encoding='utf-8')
  self.assertIn("if($Stage -eq 'infra'){30}else{480}",source)
if __name__=='__main__':unittest.main(verbosity=2)
