from __future__ import annotations
import json,pathlib,shutil,sys,unittest
from datetime import datetime
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'))
import notify_trading_events as events

def _scratch(tag):
 """工作区内一次性目录 —— 不使用 tempfile: 本机沙箱禁止写 %TEMP%
 (PermissionError [WinError 5], 见 INC-2026-09-11-01 §7.7)。
 """
 d=ROOT/'outputs'/f'_pytest_tmp_notify_{tag}'
 shutil.rmtree(d,ignore_errors=True)
 (d/'outputs'/'notifications').mkdir(parents=True,exist_ok=True)
 return d

class TradingEventTests(unittest.TestCase):
 def test_reads_tick_risk_and_data_failure(self):
  d=ROOT/'outputs'/'_pytest_tmp_notify_risk'
  try:
   shutil.rmtree(d,ignore_errors=True);intra=d/'outputs'/'intraday';intra.mkdir(parents=True)
   rows=[{'date':'2026-08-31','time':'10:00:00','sym':'000001','trigger':'stop_loss','action':'execute'}, {'date':'2026-08-31','time':'10:01:00','sym':'000002','trigger':'data_failure','action':'halt','detail':'feed down'}, {'date':'2026-08-30','time':'10:02:00','sym':'000003','trigger':'old','action':'halt'}]
   (intra/'risk_events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')
   with mock.patch.object(events,'BASE',d): got=events._risk_rows('2026-08-31')
   self.assertEqual([x['rule'] for x in got],['stop_loss','data_failure'])
   self.assertEqual(got[1]['detail'],'feed down')
  finally:
   shutil.rmtree(d,ignore_errors=True)

 # ---- 2026-09-11 假恢复修复的回归保护 ----
 def test_checked_windows_cover_sessions_only(self):
  """窗口表必须只在盘中时段为真: 11:31 午休、15:01 收盘后都不得判定任何模块"在检查中"。"""
  cases=(('09:19',set()),('09:20',{'auction'}),('09:29',{'auction'}),
         ('09:35',{'tick'}),('09:40',{'tick','scan','monitor'}),('11:30',{'tick','scan','monitor'}),
         ('11:31',set()),('13:04',set()),('13:05',{'tick'}),('13:10',{'tick','scan','monitor'}),
         ('15:00',{'tick','scan','monitor'}),('15:01',set()))
  for hm,exp in cases:
   got={m for m,on in events.checked_windows(hm).items() if on}
   self.assertEqual(got,exp,f'hm={hm}')

 def test_no_false_recovery_outside_window(self):
  """实录 2026-09-11: tick 自 10:37 死到收盘, 却在 11:31(午休) 与 15:01(收盘后) 各推一条
  monitor-recovered, monitor_health.active 被清成 {}。窗口外必须保持 active 且零推送。
  """
  d=_scratch('outside')
  try:
   p=d/'outputs'/'notifications'/'monitor_health_2026-09-11.json'
   p.write_text(json.dumps({'date':'2026-09-11',
                            'active':{'tick':'持仓tick最近2分钟无新鲜快照'}}),encoding='utf-8')
   with mock.patch.object(events,'BASE',d), mock.patch.object(events,'send_text',return_value=True) as st:
    sent,failed=events.notify_monitoring_health('2026-09-11',datetime(2026,9,11,11,31,0))
   self.assertEqual((sent,failed),(0,0))
   self.assertEqual(st.call_count,0,'窗口外不得发出任何恢复通知')
   state=json.loads(p.read_text(encoding='utf-8'))
   self.assertEqual(state['active'],{'tick':'持仓tick最近2分钟无新鲜快照'},'窗口外必须保持 active 不清除')

   with mock.patch.object(events,'BASE',d), mock.patch.object(events,'send_text',return_value=True) as st2:
    sent,failed=events.notify_monitoring_health('2026-09-11',datetime(2026,9,11,15,1,18))
   self.assertEqual((sent,failed),(0,0))
   self.assertEqual(st2.call_count,0)
   self.assertEqual(json.loads(p.read_text(encoding='utf-8'))['active'],
                    {'tick':'持仓tick最近2分钟无新鲜快照'})
  finally:
   shutil.rmtree(d,ignore_errors=True)

 def test_recovery_declared_inside_window(self):
  """窗口内且缺口消失时才允许宣告恢复, 并清空对应 active。"""
  d=_scratch('inside')
  try:
   p=d/'outputs'/'notifications'/'monitor_health_2026-09-11.json'
   p.write_text(json.dumps({'date':'2026-09-11',
                            'active':{'tick':'持仓tick最近2分钟无新鲜快照'}}),encoding='utf-8')
   now=datetime(2026,9,11,13,10,0)
   # scan/monitor 也已在窗口内(13:10): 由 _latest_success 返回"刚刚成功", 隔离出 tick 的恢复路径
   with mock.patch.object(events,'BASE',d), mock.patch.object(events,'send_text',return_value=True) as st, \
        mock.patch.object(events,'_tick_time',return_value=now), \
        mock.patch.object(events,'_latest_success',return_value=now):
    sent,failed=events.notify_monitoring_health('2026-09-11',now)
   self.assertEqual((sent,failed),(1,0))
   self.assertEqual(st.call_args[1]['event_key'],'monitor-recovered:2026-09-11:tick:1310')
   self.assertEqual(json.loads(p.read_text(encoding='utf-8'))['active'],{})
  finally:
   shutil.rmtree(d,ignore_errors=True)

 def test_gap_still_reported_and_stays_active_outside_then_inside(self):
  """窗口外发现不了缺口是设计(不检查); 进入窗口后必须重新检出并只推一次中断。"""
  d=_scratch('gap')
  try:
   now=datetime(2026,9,11,13,10,0)
   with mock.patch.object(events,'BASE',d), mock.patch.object(events,'send_text',return_value=True) as st, \
        mock.patch.object(events,'_tick_time',return_value=datetime(2026,9,11,10,35,41)), \
        mock.patch.object(events,'_latest_success',return_value=None):
    sent,failed=events.notify_monitoring_health('2026-09-11',now)
   keys=sorted(c[1]['event_key'] for c in st.call_args_list)
   self.assertIn('monitor-gap:2026-09-11:tick:1310',keys)
   self.assertIn('monitor-gap:2026-09-11:scan:1310',keys)
   self.assertIn('monitor-gap:2026-09-11:monitor:1310',keys)
   self.assertFalse([k for k in keys if k.startswith('monitor-recovered')])
   state=json.loads((d/'outputs'/'notifications'/'monitor_health_2026-09-11.json').read_text(encoding='utf-8'))
   self.assertEqual(set(state['active']),{'tick','scan','monitor'})
  finally:
   shutil.rmtree(d,ignore_errors=True)

if __name__=='__main__':unittest.main(verbosity=2)
