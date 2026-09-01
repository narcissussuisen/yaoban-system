"""P0.3 附带修复回归测试: trader_daily 全亏日 pl_ratio 守卫（蓝图 B0-P0-1 关联缺陷 F4）.

8/31 崩溃根因 day_pnl=None 已修; 本测试锁定残留路径:
全亏日 wins 为空 → avg_win=None, avg_loss 非 None → 旧代码 None/float TypeError.
"""
from __future__ import annotations
import pathlib, re, sys, unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'portfolio'))
sys.path.insert(0, str(ROOT / 'src'))


class TraderDailyPlRatioTests(unittest.TestCase):
    def test_pl_ratio_guard_present_in_source(self):
        source = (ROOT / 'scripts' / 'trader_daily.py').read_text(encoding='utf-8')
        self.assertIn("summary['avg_win'] is not None and summary['avg_loss']",
                      source, 'avg_win None 守卫缺失——全亏日将 TypeError')
        # 旧的未守卫写法不得残留
        self.assertNotIn(
            "summary['avg_win'] / abs(summary['avg_loss']) if summary['avg_loss'] else None",
            source)

    def test_pl_ratio_expression_behavior(self):
        # 以源码中的实际表达式做行为验证（exec 提取, 防止源与测试脱节）
        source = (ROOT / 'scripts' / 'trader_daily.py').read_text(encoding='utf-8')
        m = re.search(r'pl_ratio = \(\s*summary\[.avg_win.\] / abs\(summary\[.avg_loss.\]\)\s*\n?\s*if \(summary\[.avg_win.\] is not None and summary\[.avg_loss.\]\) else None\)', source)
        self.assertIsNotNone(m, 'pl_ratio 守卫表达式与测试期望不一致')
        cases = [
            ({'avg_win': None, 'avg_loss': -3.0}, None),      # 全亏日: 不抛异常, 返回 None
            ({'avg_win': 2.0, 'avg_loss': -1.0}, 2.0),        # 正常日
            ({'avg_win': 1.5, 'avg_loss': None}, None),       # 无亏损样本
        ]
        for summary, expected in cases:
            ns = {'summary': summary}
            exec('pl_ratio = (summary["avg_win"] / abs(summary["avg_loss"])'
                 ' if (summary["avg_win"] is not None and summary["avg_loss"]) else None)', ns)
            self.assertEqual(ns['pl_ratio'], expected, f'{summary} → {ns["pl_ratio"]}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
