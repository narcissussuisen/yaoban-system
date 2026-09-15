from __future__ import annotations
import pathlib, sys, unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'portfolio'));sys.path.insert(0,str(ROOT/'src'))
import scan_and_confirm as scan

class ScanFailClosedTests(unittest.TestCase):
 """scan_and_confirm.main() 的 fail-closed 契约测试。

 ⚠️ 2026-09-12 修复（原测试长期红灯，根因＝**日期耦合**）：
   `main()` 在 ``scan_and_confirm.py:160-163`` 有一道**计划门禁** ——
   `_load_day_plan(day)` 为 None 就立刻 `return 7`，**根本走不到**温度/行情分支。
   而这两个用例原先只 mock 了 `load` / `load_universe`，**没 mock 计划门禁** →
   只要跑测试那天没有当日计划文件（例如周六、或计划尚未生成），就一律返回 7，
   与期望的 3 / 4 不符 → 长期假红。
   **修法＝显式 mock `_load_day_plan`**，让它不依赖磁盘上的「当日」计划文件，
   测试从此与真实日期**完全解耦**（不是改断言去迁就今天）。

   这类红灯最坏的地方是「**在交易日可能自己变绿**」—— 时红时绿会让「是否回归」无法判断。
 """
 def account(self):
  return {'account':{'positions':{}},'policy':{'account_mode':'autonomous_paper','require_human_decision':False}}
 @mock.patch.object(scan,'load_universe',return_value=['000001'])
 @mock.patch.object(scan,'load',return_value={'account':{'positions':{}}})
 @mock.patch('pandas.read_csv',side_effect=OSError('missing sentiment'))
 def test_missing_temperature_fails_closed(self,_pd,_load,_universe):
  # 先放行计划门禁（return 7 的前置），才能测到「温度缺失 → 3」
  with mock.patch.object(scan,'_load_day_plan',return_value={'picks':[]}), \
       mock.patch.object(sys,'argv',['scan','--force','--temp-ladder']):
   self.assertEqual(scan.main(),3)
 @mock.patch.object(scan,'fetch_batch',side_effect=OSError('feed down'))
 @mock.patch.object(scan,'load_universe',return_value=['000001'])
 @mock.patch.object(scan,'load',return_value={'account':{'positions':{}}})
 def test_incomplete_quote_batch_fails_closed(self,_load,_universe,_fetch):
  # 同上：放行计划门禁，才能测到「行情批次不完整 → 4」
  with mock.patch.object(scan,'_load_day_plan',return_value={'picks':[]}), \
       mock.patch.object(sys,'argv',['scan','--force']):
   self.assertEqual(scan.main(),4)
 def test_plan_gate_fails_closed(self):
  """⭐ 计划门禁本身也要有测试：当日计划缺失 → **必须** fail-closed 返回 7。

  原先这个行为只以「两个用例意外返回 7」的形式被观察到（属副作用），
  没有独立断言；门禁一旦被改坏（例如误放行），反而没人发现。
  """
  with mock.patch.object(scan,'_load_day_plan',return_value=None), \
       mock.patch.object(scan,'load_universe',return_value=['000001']), \
       mock.patch.object(scan,'load',return_value={'account':{'positions':{}}}), \
       mock.patch.object(sys,'argv',['scan','--force']):
   self.assertEqual(scan.main(),7)
 def test_authorized_universe(self):
  for symbol in ('600000','601001','603001','605001','000001','001001','002001','003001','300001','301001'):
   self.assertTrue(scan.is_authorized_symbol(symbol),symbol)
  for symbol in ('688001','689001','430001','830001','900001','200001','510300'):
   self.assertFalse(scan.is_authorized_symbol(symbol),symbol)

if __name__=='__main__':unittest.main(verbosity=2)
