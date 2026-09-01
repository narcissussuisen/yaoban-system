from __future__ import annotations
import json,pathlib,sys,unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import preflight

def sample_report():
 return {'date':'2026-09-01','stage':'post_plan','time':'2026-09-01 08:55:00',
  'results':[
   {'name':'交易日历','ok':True,'detail':'day=2026-09-01','critical':True,'category':'日历'},
   {'name':'TDX行情','ok':False,'detail':'all_servers_failed','critical':True,'category':'行情'},
   {'name':'任务历史','ok':False,'detail':'unproven=[YaobanPostCloseChain]','critical':False,'category':'计划任务'},
  ],
  'summary':{'pass':1,'fail':1,'warn':1},'status':'fail'}

class ReportContractTests(unittest.TestCase):
 def test_json_contract_fields_preserved(self):
  rep=sample_report()
  for key in ('date','stage','time','results','summary','status'):
   self.assertIn(key,rep)
  for r in rep['results']:
   for key in ('name','ok','detail','critical'):
    self.assertIn(key,r)

 def test_markdown_report_contains_sections(self):
  md=preflight.build_report(sample_report(),'计划验收')
  for token in ('# 逐妖盘前自检报告','## 总体结果','## 行情','## 异常项与建议处理措施','TDX行情','建议'):
   self.assertIn(token,md)

 def test_markdown_report_no_anomaly_section_when_clean(self):
  rep=sample_report();rep['results']=[{'name':'A','ok':True,'detail':'ok','critical':True,'category':'日历'}]
  rep['summary']={'pass':1,'fail':0,'warn':0};rep['status']='pass'
  md=preflight.build_report(rep,'基础设施')
  self.assertIn('## 无异常 ✨',md);self.assertNotIn('异常项与建议',md)

 def test_suggestions_cover_known_checks(self):
  names=['交易日历','TDX行情','腾讯快照','账本/持仓','净值守恒','情绪表','候选表','任务Action','任务历史','看板','残留进程','脚本语法','当日计划','外盘新鲜度','CPU 使用率','内存使用率','磁盘 C: 使用率','磁盘 F: 使用率']
  for n in names:
   self.assertNotEqual(preflight._suggestion(n),preflight._suggestion('未知项'),msg=n)
  self.assertEqual(preflight._suggestion('未知项'),'查看对应日志排查；如持续异常请人工介入。')

 def test_status_card_shape(self):
  card=preflight._build_status_card(sample_report(),'计划验收')
  self.assertEqual(card['msg_type'],'interactive')
  self.assertEqual(card['card']['header']['template'],'red')
  self.assertIn('逐妖盘前自检',card['card']['header']['title']['content'])
  texts=json.dumps(card,ensure_ascii=False)
  self.assertIn('关键组件健康检查',texts);self.assertIn('异常项及处理建议',texts)
  self.assertIn('TDX行情',texts);self.assertIn('建议',texts)

 def test_status_card_green_when_pass(self):
  rep=sample_report();rep['summary']={'pass':3,'fail':0,'warn':0};rep['status']='pass'
  rep['results']=[{**r,'ok':True} for r in rep['results']]
  card=preflight._build_status_card(rep,'基础设施')
  self.assertEqual(card['card']['header']['template'],'green')

 def test_push_card_audits_delivery(self):
  import tempfile
  out=pathlib.Path(tempfile.mkdtemp(dir=str(ROOT)))
  class Resp:
   status=200
   def __enter__(self):return self
   def __exit__(self,*a):return False
   def read(self,n=-1):return b'{"code":0}'
  sec=out/'secret.txt';sec.write_text('https://open.feishu.cn/open-apis/bot/v2/hook/test-value',encoding='utf-8')
  with mock.patch('urllib.request.urlopen',return_value=Resp()), \
       mock.patch.object(preflight,'OUT',out), \
       mock.patch.object(preflight,'SECRET_FILE',sec):
   self.assertTrue(preflight._push_card({'msg_type':'interactive','card':{'header':{'title':{'content':'x'}},'elements':[]}},'selfcheck:2026-09-01:post_plan'))
  audits=list((out/'notifications').glob('delivery_*.jsonl'))
  self.assertEqual(len(audits),1)
  row=json.loads(audits[0].read_text(encoding='utf-8').splitlines()[0])
  self.assertEqual(row['kind'],'selfcheck');self.assertEqual(row['event_key'],'selfcheck:2026-09-01:post_plan');self.assertTrue(row['ok'])
  import shutil;shutil.rmtree(out,ignore_errors=True)

 def test_system_resources_parses_powershell_output(self):
  with mock.patch.object(preflight,'run_capture',return_value=(0,'cpu=12.5 mem=60.1\ndisk=C: 85.4 40.7\ndisk=F: 78.2 120.3')):
   r=preflight._system_resources()
   self.assertEqual(r['cpu'],12.5);self.assertEqual(r['mem'],60.1)
   self.assertEqual(r['disk_c'],85.4);self.assertEqual(r['disk_c_free_gb'],40.7)
   self.assertEqual(r['disk_f'],78.2);self.assertEqual(r['disk_f_free_gb'],120.3)
  with mock.patch.object(preflight,'run_capture',return_value=(1,'')):
   self.assertEqual(preflight._system_resources(),{})

if __name__=='__main__':unittest.main(verbosity=2)
