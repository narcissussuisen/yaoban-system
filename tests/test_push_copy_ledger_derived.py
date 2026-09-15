"""护栏测试：推送文案中的本金与风控纪律必须**派生自账本**，不得硬编码。

背景（2026-09-15 缺陷）
----------------------
R0.2 主账本于 2026-09-14 迁移至 50 万（用户裁决 8.1），但盘前汇报文案仍写死
「10万元」，且纪律行写死「单票≤45%」——而 policy 已于 2026-09-13 裁决为
max_single_weight=0.30。结果：对外推送口径与生产闸口长期不一致，且账本迁移
不会自动纠正它。

本测试锁死三件事：
1. 文案金额 == 账本 start_cash；
2. 纪律行数值 == 账本 policy（policy 一改，文案必须跟着改）；
3. 源码里不得再出现硬编码的金额/阈值字面量（防回退）。
"""
from __future__ import annotations
import json, pathlib, sys, tempfile, unittest
from unittest import mock
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import feishu_notify


def _write_plan(base: pathlib.Path, day: str) -> None:
 d = base / 'outputs' / 'plans'; d.mkdir(parents=True, exist_ok=True)
 (d / f'{day}_plan.json').write_text(json.dumps(
  {'date': day, 'mode': 'test-mode', 'emotion': {'temp': 'T', 'stage': 'S'}, 'picks': []},
  ensure_ascii=False), encoding='utf-8')


def _write_ledger(path: pathlib.Path, capital: float, policy: dict) -> None:
 path.write_text(json.dumps({'start_cash': capital, 'policy': policy}, ensure_ascii=False),
                 encoding='utf-8')


DEFAULT_POLICY = {'max_positions': 2, 'max_single_weight': 0.30,
                  'max_gross_exposure': 0.90, 'max_daily_loss_pct': 5.0}


class PushCopyDerivedTests(unittest.TestCase):
 def test_capital_and_discipline_come_from_ledger(self):
  with tempfile.TemporaryDirectory() as td:
   base = pathlib.Path(td); day = '2026-09-15'
   _write_plan(base, day)
   led = base / 'ledger.json'
   _write_ledger(led, 500000.0, DEFAULT_POLICY)
   with mock.patch.object(feishu_notify, 'BASE', base), mock.patch.object(feishu_notify, 'LEDGER', led):
    msg = feishu_notify._plan_message(day)
   self.assertIn('50万元', msg)
   self.assertNotIn('10万元', msg)
   self.assertIn('单票≤30%', msg)
   self.assertNotIn('45%', msg)
   self.assertIn('最多持有2只', msg)
   self.assertIn('总敞口≤90%', msg)

 def test_discipline_tracks_policy_change(self):
  """policy 一改，文案必须跟着改 —— 防「改了 policy、文案不动」复发。"""
  with tempfile.TemporaryDirectory() as td:
   base = pathlib.Path(td); day = '2026-09-15'
   _write_plan(base, day)
   led = base / 'ledger.json'
   _write_ledger(led, 200000.0, {'max_positions': 3, 'max_single_weight': 0.45,
                                 'max_gross_exposure': 0.80, 'max_daily_loss_pct': 7.0})
   with mock.patch.object(feishu_notify, 'BASE', base), mock.patch.object(feishu_notify, 'LEDGER', led):
    msg = feishu_notify._plan_message(day)
   self.assertIn('20万元', msg)
   self.assertIn('单票≤45%', msg)
   self.assertIn('总敞口≤80%', msg)
   self.assertIn('单日-7%熔断', msg)
   self.assertIn('最多持有3只', msg)

 def test_unreadable_ledger_degrades_without_breaking_push(self):
  """账本读不到时只省掉数字，绝不让文案渲染异常拖垮推送本身。"""
  with tempfile.TemporaryDirectory() as td:
   base = pathlib.Path(td); day = '2026-09-15'
   _write_plan(base, day)
   with mock.patch.object(feishu_notify, 'BASE', base), \
        mock.patch.object(feishu_notify, 'LEDGER', base / 'missing.json'):
    msg = feishu_notify._plan_message(day)
   self.assertIn('A股全自主模拟盘', msg)
   self.assertNotIn('万元', msg)
   self.assertIn('关键数据失效则禁止新仓', msg)

 def test_no_hardcoded_literals_left_in_push_sources(self):
  """只看**会进入推送正文的字符串字面量**。

  注释与 docstring 不计入：它们只用于说明缺陷背景，不参与渲染。
  若按整文件文本断言，注释里写「原为硬编码 10万元」就会误报 —— 那不是缺陷。
  """
  import ast
  for rel in ('scripts/feishu_notify.py', 'scripts/notify_trading_events.py'):
   tree = ast.parse((ROOT / rel).read_text(encoding='utf-8'))
   docstrings = set()
   for node in ast.walk(tree):
    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
     body = getattr(node, 'body', []) or []
     if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
        and isinstance(body[0].value.value, str):
      docstrings.add(id(body[0].value))
   for node in ast.walk(tree):
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
     self.assertNotIn('10万元', node.value, msg=f'{rel} 字面量残留硬编码本金: {node.value[:80]}')
     self.assertNotIn('单票≤45%', node.value, msg=f'{rel} 字面量残留过期阈值')


if __name__ == '__main__':
 unittest.main(verbosity=2)
