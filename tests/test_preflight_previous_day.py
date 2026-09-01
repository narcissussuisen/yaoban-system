from __future__ import annotations
import pathlib,sys,unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
class PreviousTradingDayContractTests(unittest.TestCase):
 def test_preflight_uses_strict_previous_day(self):
  source=(ROOT/'scripts'/'preflight.py').read_text(encoding='utf-8')
  self.assertIn("get('previous_trading_day','')",source)
  self.assertNotIn("pday=(cal_info or {}).get('last_trading_day'",source)
  self.assertIn("pday<day",source)
 def test_current_plan_cutoff_is_previous_session(self):
  import json
  plan=json.loads((ROOT/'outputs'/'plans'/'2026-08-31_plan.json').read_text(encoding='utf-8'))
  self.assertIn('输入≤2026-08-28',plan['mode'])
if __name__=='__main__':unittest.main(verbosity=2)
