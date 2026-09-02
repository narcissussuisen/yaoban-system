from __future__ import annotations
import json, pathlib, sys, tempfile, unittest
from datetime import datetime
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import collect_daily_acceptance as acceptance

def _no_tasks(name):
 return {'last_run':'','last_result':-1,'next_run':''}

class AcceptanceContractTests(unittest.TestCase):
 def test_required_tasks_exclude_disabled_and_self(self):
  # F7 回归防护: 已禁用的 YaobanDailyRebuild 与采集器自身不得出现在必需清单
  self.assertNotIn('YaobanDailyRebuild',acceptance.REQUIRED_TASKS)
  self.assertNotIn('YaobanDailyAcceptance',acceptance.REQUIRED_TASKS)
 def test_tick_daemon_is_required(self):
  self.assertIn('YaobanTickDaemon',acceptance.REQUIRED_TASKS)
 def test_acceptance_requires_auction_evidence(self):
  source=(ROOT/'scripts'/'collect_daily_acceptance.py').read_text(encoding='utf-8')
  for token in ("'auction_latest'","'auction_freeze'","'auction_delivery'"):
   self.assertIn(token,source)
 def test_acceptance_requires_both_deliveries_and_ticks(self):
  source=(ROOT/'scripts'/'collect_daily_acceptance.py').read_text(encoding='utf-8')
  for token in ("'premarket_delivery'","'close_delivery'","'live_tick'","'next_plan'","'rebuild_products'"):
   self.assertIn(token,source)
 def test_post_close_chain_passes_final(self):
  chain=(ROOT/'scripts'/'post_close_chain.ps1').read_text(encoding='utf-8')
  self.assertIn('--date $Day --final',chain)
 def test_rebuild_completeness_uses_products_not_task(self):
  # F7: rebuild 完成性走产物证据, 不依赖已禁用任务
  source=(ROOT/'scripts'/'collect_daily_acceptance.py').read_text(encoding='utf-8')
  self.assertIn('rebuild_products_ok',source)
  self.assertIn('REBUILT_DIR',source)

class AcceptanceFinalProbeTests(unittest.TestCase):
 """P0.5 双轨(final/probe)与三重门槛行为测试."""
 day='2026-08-05'  # 合成历史日: 各输入文件不存在, 结果确定性 incomplete

 def _run(self,argv,now):
  with mock.patch.object(acceptance,'task_info',side_effect=_no_tasks),\
       tempfile.TemporaryDirectory() as td:
   out=pathlib.Path(td)
   with mock.patch.object(acceptance,'OUT',out):
    rc=acceptance.main(argv=argv,now=now)
    files={f.name:json.loads(f.read_text(encoding='utf-8')) for f in out.glob('*.json')}
   return rc,files

 def test_final_without_explicit_date_downgrades_to_probe(self):
  rc,files=self._run(['--final'],datetime(2026,8,5,16,30))
  self.assertEqual(rc,3)
  self.assertIn('acceptance_2026-08-05_probe.json',files)
  rep=files['acceptance_2026-08-05_probe.json']
  self.assertFalse(rep['final']);self.assertEqual(rep['probe_reason'],'final_requires_explicit_date')
  self.assertNotIn('acceptance_2026-08-05.json',files)

 def test_final_outside_window_downgrades_to_probe(self):
  # 盘中(10:00)请求 final → 自动降级 probe（消灭凌晨垃圾报告类缺陷）
  rc,files=self._run(['--final','--date',self.day],datetime(2026,8,5,10,0))
  self.assertEqual(rc,3)
  rep=files[f'acceptance_{self.day}_probe.json']
  self.assertFalse(rep['final']);self.assertEqual(rep['probe_reason'],'outside_final_window')

 def test_final_in_window_but_missing_inputs_is_incomplete(self):
  # 窗口内 final: 输入缺失 → incomplete(3) 而非 fail(2)——缺输入≠真失败
  rc,files=self._run(['--final','--date',self.day],datetime(2026,8,5,16,30))
  self.assertEqual(rc,3)
  rep=files[f'acceptance_{self.day}.json']
  self.assertTrue(rep['final']);self.assertEqual(rep['status'],'incomplete')
  self.assertIn('preflight_infra',rep['inputs'])
  self.assertIn('ok',rep['inputs'].values())
  self.assertIn('missing',rep['inputs'].values())

 def test_final_refuses_to_overwrite_pass_report(self):
  with mock.patch.object(acceptance,'task_info',side_effect=_no_tasks),\
       tempfile.TemporaryDirectory() as td:
   out=pathlib.Path(td)
   official=out/f'acceptance_{self.day}.json'
   official.write_text(json.dumps({'final':True,'status':'pass'}),encoding='utf-8')
   with mock.patch.object(acceptance,'OUT',out):
    rc=acceptance.main(argv=['--final','--date',self.day,'--force'],now=datetime(2026,8,5,16,30))
   self.assertEqual(rc,4)  # final pass 报告: force 也不得覆盖
   self.assertEqual(json.loads(official.read_text(encoding='utf-8'))['status'],'pass')

 def test_final_force_overwrites_non_pass_with_regenerated(self):
  with mock.patch.object(acceptance,'task_info',side_effect=_no_tasks),\
       tempfile.TemporaryDirectory() as td:
   out=pathlib.Path(td)
   official=out/f'acceptance_{self.day}.json'
   official.write_text(json.dumps({'final':True,'status':'fail'}),encoding='utf-8')
   with mock.patch.object(acceptance,'OUT',out):
    rc=acceptance.main(argv=['--final','--date',self.day,'--force'],now=datetime(2026,8,5,16,30))
   rep=json.loads(official.read_text(encoding='utf-8'))
   self.assertTrue(rep['regenerated']);self.assertEqual(rep['status'],'incomplete')

 def test_probe_never_touches_official_file(self):
  with mock.patch.object(acceptance,'task_info',side_effect=_no_tasks),\
       tempfile.TemporaryDirectory() as td:
   out=pathlib.Path(td)
   official=out/f'acceptance_{self.day}.json'
   official.write_text(json.dumps({'final':True,'status':'pass','marker':'official'}),encoding='utf-8')
   with mock.patch.object(acceptance,'OUT',out):
    acceptance.main(argv=['--date',self.day],now=datetime(2026,8,6,1,47))  # 凌晨垃圾运行时点
   self.assertEqual(json.loads(official.read_text(encoding='utf-8'))['marker'],'official')
   self.assertTrue((out/f'acceptance_{self.day}_probe.json').exists())

class ChainOrderAndDualEntryTests(unittest.TestCase):
 """C4(计划批次C4/§3.3): 链序 index 单调断言 + acceptance 双入口参数一致性。

 链序断言防 9/2 事故根因回归(情绪/候选在 rebuild 之前跑 → 永读不到当日日线);
 双入口一致性防单入口修好、另一入口回归(E10: 手动入口裸调用降级 probe 却推 passed 文案)。
 """
 def test_post_close_chain_stage_order_monotonic(self):
  chain=(ROOT/'scripts'/'post_close_chain.ps1').read_text(encoding='utf-8')
  stages=['fetch_daily_minute_rebuild.py','r5p_sentiment_build.py','r6p_candidates_build.py',
          'generate_next_plan.py','collect_daily_acceptance.py']
  idx=[chain.find(s) for s in stages]
  for s,i in zip(stages,idx):
   self.assertGreaterEqual(i,0,f'链脚本缺阶段: {s}')
  for a,b in zip(idx,idx[1:]):
   self.assertLess(a,b,f'链序被调换: 相邻阶段 index {a} >= {b}(9/2 事故根因防回归)')

 def test_run_trading_task_acceptance_has_final_params(self):
  rt=(ROOT/'scripts'/'run_trading_task.ps1').read_text(encoding='utf-8')
  self.assertIn('--date $Day --final',rt)  # C2 回归锚: 手动补跑入口补齐参数

 def test_dual_entry_acceptance_params_consistent(self):
  chain=(ROOT/'scripts'/'post_close_chain.ps1').read_text(encoding='utf-8')
  rt=(ROOT/'scripts'/'run_trading_task.ps1').read_text(encoding='utf-8')
  self.assertIn('--date $Day --final',chain)  # 生产入口(16:30 链)
  self.assertIn('--date $Day --final',rt)     # 手动补跑入口: 双入口一致

 def test_post_close_chain_manifest_observability(self):
  # C7 回归锚: manifest 观测字段齐全(skipped_due_to 显式化, 不以上游失败码冒充)
  chain=(ROOT/'scripts'/'post_close_chain.ps1').read_text(encoding='utf-8')
  for token in ('chain_manifest_','Invoke-ChainStage','skipped_due_to','attempt_no',
                'command_hash','input_manifest_hash','output_paths','run_id'):
   self.assertIn(token,chain,f'C7 manifest 缺要素: {token}')

if __name__=='__main__':unittest.main(verbosity=2)
