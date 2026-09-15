"""reviews 审计标记回归测试（INC-2026-09-11-01 附带发现）

背景: `close_pipeline.py` 重建"规则全天会怎么打"作为盘后审计(counterfactual), 只记录不执行
(`--execute` 被 return 5 守卫永久禁用, 且从不调用 fill()), 故 `reviews[*].sells` 与
`account.fills` 系统性不一致(9/11 审计: 08-31/09-03/09-04 均有 reviews.sells 而无对应成交)——
属设计如此, 不是账本错误。

隐患: reviews[*] 的 buys/sells 字段结构与真实成交同名(同含 kind='P/D/B'、reason='止损'),
后来者或未来代码据以推断持仓/已实现盈亏会双重计算。故补机器可读标记。

覆盖:
  - ledger.record_review 为 review 补齐 kind/executed/authority 三键
  - setdefault 语义: 既有键不被覆盖(历史条目与显式标注不被静默改写)
  - close_pipeline 的 decisions/close_decision 文档同带标记(执行权威仍指向 account.fills)
"""
from __future__ import annotations
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'portfolio'))
sys.path.insert(0, str(ROOT / 'src'))
# 注: 不注入 ROOT.parent/'py_libs'(本机该目录被 ACL 拒绝读取, 见 test_tick_lock_reclaim.py 说明)。

import ledger  # noqa: E402
import close_pipeline as cp  # noqa: E402


class RecordReviewMarkerTests(unittest.TestCase):
    def test_marker_added(self):
        st = {'reviews': {}}
        ledger.record_review(st, '2026-09-11', {'buys': [], 'sells': [{'sym': '300468', 'qty': 1800, 'reason': '止损'}]})
        r = st['reviews']['2026-09-11']
        self.assertEqual(r['kind'], 'audit_counterfactual')
        self.assertIs(r['executed'], False)
        self.assertEqual(r['authority'], 'account.fills')

    def test_existing_keys_not_overwritten(self):
        """setdefault 语义: 显式给定的值必须保留(不静默改写历史/自定义条目)。"""
        st = {'reviews': {}}
        ledger.record_review(st, '2026-09-11', {'kind': 'manual', 'executed': True, 'authority': 'other'})
        r = st['reviews']['2026-09-11']
        self.assertEqual(r['kind'], 'manual')
        self.assertIs(r['executed'], True)
        self.assertEqual(r['authority'], 'other')

    def test_marker_does_not_disturb_audit_payload(self):
        st = {'reviews': {}}
        sells = [{'sym': '300468', 'ts': '15:00', 'reason': '炸板卖出', 'px': 27.5, 'qty': 1800}]
        ledger.record_review(st, '2026-09-04', {'buys': [], 'sells': sells, 'close': {'300468': 27.5}})
        r = st['reviews']['2026-09-04']
        self.assertEqual(r['sells'], sells, '审计内容本身不得被改写')
        self.assertEqual(r['close'], {'300468': 27.5})


class ClosePipelineMarkerTests(unittest.TestCase):
    """close_decision 文档由 dict(decisions) 派生, 标记随 decisions 一并进入。"""

    def test_build_close_decision_carries_marker(self):
        decisions = {'date': '2026-09-11', 'buys': [], 'sells': [], 't': [], 'notes': [],
                     'kind': 'audit_counterfactual', 'executed': False, 'authority': 'account.fills'}
        st = {'_revision': 7, 'account': {'positions': {'300394': {'qty': 100}}}}
        doc = cp.build_close_decision(decisions, st, 100000.0, {'300394': 267.0})
        self.assertEqual(doc['kind'], 'audit_counterfactual')
        self.assertIs(doc['executed'], False)
        self.assertEqual(doc['authority'], 'account.fills')
        self.assertEqual(doc['ledger_revision'], 7)
        self.assertEqual(doc['positions']['300394']['market_value'], 26700.0)

    def test_close_pipeline_source_sets_marker_on_decisions(self):
        """源码级守卫: decisions 必须带上标记, 否则 close_decision 文档会退回无标记状态。"""
        src = (ROOT / 'scripts' / 'close_pipeline.py').read_text(encoding='utf-8')
        self.assertIn("'kind': 'audit_counterfactual'", src)
        self.assertIn("'executed': False", src)
        self.assertIn("'authority': 'account.fills'", src)

    def test_execute_still_guarded(self):
        """保留性测试: 若有人放开 --execute, 执行权威标记的前提就不成立了。"""
        src = (ROOT / 'scripts' / 'close_pipeline.py').read_text(encoding='utf-8')
        self.assertIn('close_pipeline --execute 已禁用', src)


if __name__ == '__main__':
    unittest.main(verbosity=2)
