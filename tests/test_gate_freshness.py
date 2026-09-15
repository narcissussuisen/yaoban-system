from __future__ import annotations
import pathlib,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class GateFreshnessTests(unittest.TestCase):
 def test_failure_message_includes_gate_details(self):
  # 2026-09-11 更新: Send-Failure 的文案已按用户裁定本地化(品牌 EvoAlpha + 「字段：值」中文风格),
  # 旧断言锁的是英文串 'Failed checks:'/'Report:' —— 文案改了但断言没跟上, 属"断言过期"。
  # 这里改为断言**语义**仍在: 失败项明细、报告路径、以及按 critical 过滤的结构。
  source=(ROOT/'scripts'/'run_trading_task.ps1').read_text(encoding='utf-8')
  for token in ('未通过检查：', '报告：', '$j.results', 'critical'):
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
