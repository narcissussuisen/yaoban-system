from __future__ import annotations
import json, pathlib, sys, tempfile, unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import feishu_notify

class Response:
 def __init__(self): self.status=200
 def __enter__(self): return self
 def __exit__(self,*args): return False
 def read(self,n=-1): return b'{"code":0,"msg":"ok"}'

class FeishuNotifyTests(unittest.TestCase):
 def test_success_is_audited_without_webhook(self):
  with tempfile.TemporaryDirectory() as td:
   out=pathlib.Path(td); secret=out/'secret.txt'; secret.write_text('https://open.feishu.cn/open-apis/bot/v2/hook/test-value',encoding='utf-8')
   with mock.patch.object(feishu_notify,'OUT',out), mock.patch.object(feishu_notify,'SECRET_FILE',secret), mock.patch('urllib.request.urlopen',return_value=Response()):
    self.assertTrue(feishu_notify.send_text('hello',event_key='k1',kind='test'))
    self.assertTrue(feishu_notify.send_text('hello',event_key='k1',kind='test'))
   joined=''.join(p.read_text(encoding='utf-8') for p in out.glob('*') if p.is_file() and p != secret)
   self.assertNotIn('test-value',joined)
   audits=list(out.glob('delivery_*.jsonl'));self.assertEqual(len(audits),1)
   self.assertEqual(len(audits[0].read_text(encoding='utf-8').splitlines()),1)
 def test_missing_secret_fails_closed(self):
  with tempfile.TemporaryDirectory() as td:
   with mock.patch.object(feishu_notify,'SECRET_FILE',pathlib.Path(td)/'missing'), mock.patch.dict('os.environ',{},clear=True):
    with self.assertRaises(RuntimeError): feishu_notify._webhook()

if __name__=='__main__': unittest.main(verbosity=2)
