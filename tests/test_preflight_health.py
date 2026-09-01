from __future__ import annotations
import pathlib, sys, unittest
from unittest import mock
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import preflight

class HealthContractTests(unittest.TestCase):
   def test_tdx_health_fails_over_after_empty_bars(self):
    class FakeApi:
     def __init__(self, heartbeat=False): self.host=None
     def connect(self, host, port, time_out=5): self.host=host; return True
     def get_security_bars(self, *_args):
      if self.host == 'bad': return []
      return [{'datetime':'2026-08-28 15:00','open':1,'high':1,'low':1,'close':1}]*2
     def disconnect(self): pass
    servers=[('bad',7709),('good',7709)]
    # P0-7 修复后 tdx_health 先做 TCP 预筛(_tcp_alive)，测试主机名不可解析会误判 connect_false；
    # mock 预筛全通过，聚焦"空 bars 后自动切换"的 failover 逻辑本身
    with mock.patch.object(preflight,'_tcp_alive',return_value=servers):
     result=preflight.tdx_health(FakeApi, servers)
    self.assertTrue(result['ok'])
    self.assertEqual(result['server'],'good:7709')
    self.assertIn('bad:7709=empty_or_invalid_bars',result['failures'])

   def test_tdx_health_fast_fails_when_tcp_prescreen_empty(self):
    class FakeApi:
     def __init__(self, heartbeat=False): pass
     def connect(self, host, port, time_out=5): return True
     def get_security_bars(self, *_args):
      return [{'datetime':'2026-08-28 15:00','open':1,'high':1,'low':1,'close':1}]*2
     def disconnect(self): pass
    # TCP 预筛全挂(模拟死节点) → 快速失败，不做连接验证（P0-7 行为）
    with mock.patch.object(preflight,'_tcp_alive',return_value=[]):
     result=preflight.tdx_health(FakeApi, [('bad',7709)])
    self.assertFalse(result['ok'])
    self.assertIn('bad:7709=connect_false',result['failures'])

   def test_current_vibe_health_shape(self):
    self.assertTrue(preflight.health_ok(200,{'ok':True,'runtime':'function_calling'}))
   def test_legacy_health_shape(self):
    self.assertTrue(preflight.health_ok(200,{'status':'healthy'}))
   def test_http_or_body_failure(self):
    self.assertFalse(preflight.health_ok(503,{'ok':True}))
    self.assertFalse(preflight.health_ok(200,{'ok':False}))

if __name__=='__main__':unittest.main(verbosity=2)
