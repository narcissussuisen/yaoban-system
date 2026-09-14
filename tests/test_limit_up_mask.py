"""`limit_up_mask` 板块分档 —— **测试先行**（P1-1，本期只写测试、不改 indicators.py）。

背景（缺陷锚，`src/core/indicators.py:54-57`）：
    def limit_up_mask(df, pct_threshold: float = 9.8):
        \"\"\"涨停判定（按涨跌幅阈值，默认 9.8% 兼容 10% 与 ST 5% 场景的宽松匹配）\"\"\"
⇒ **注释与实现不符**：只有一个 9.8 阈值，不可能"兼容 10% 与 ST 5%"。
   真实后果：创业板/科创（300/301/688/689，涨停 20%）的 **+10% 会被误判为涨停**；
   北交所（4/8/92，30%）同理。现成真相源已有：`core/sell.limit_pct_of()`（`sell.py:79-84`）。

本文件的设计：**新行为的断言挂在签名探测上** —— `limit_up_mask` 一旦支持 `symbol`
参数，这些用例**自动开始生效**，无需回头改测试；在此之前它们显示为 skip（不污染全量回归）。
"""
from __future__ import annotations
import inspect, pathlib, sys, unittest

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT.parent / 'py_libs'))

from core.indicators import limit_up_mask
from core.sell import limit_pct_of


def frame(prev_close: float, close: float) -> pd.DataFrame:
    return pd.DataFrame({"open": [prev_close, close], "high": [prev_close, close],
                         "low": [prev_close, close],
                         "close": [prev_close, close], "volume": [1.0, 1.0]})


_SUPPORTS_SYMBOL = "symbol" in inspect.signature(limit_up_mask).parameters
_SKIP = "待 P1-1：limit_up_mask 支持 symbol 参数后本用例自动启用"


class LimitPctOfTests(unittest.TestCase):
    """先钉住真相源本身（`sell.py:79-84`）—— 它是修复的依据，不该在这里被弄坏。"""

    def test_boards(self):
        self.assertEqual(limit_pct_of("600644"), 0.10)
        self.assertEqual(limit_pct_of("002436"), 0.10)
        self.assertEqual(limit_pct_of("300124"), 0.20)
        self.assertEqual(limit_pct_of("301071"), 0.20)
        self.assertEqual(limit_pct_of("688981"), 0.20)
        self.assertEqual(limit_pct_of("830799"), 0.30)
        self.assertEqual(limit_pct_of("920001"), 0.30)

    def test_st_has_no_branch_documented_limitation(self):
        # ⚠️ 已知限制（修复时必须知道）：limit_pct_of 无 ST 5% 分支。
        #    目前靠「池层已剔 ST」（pattern_pool.py:149）+「scan 实时名称过滤」（scan_and_confirm.py:388）兜底，
        #    残留误判只影响情绪统计，不影响选股。此处把限制**钉住**，避免日后被当成 bug 误修。
        self.assertEqual(limit_pct_of("600001"), 0.10)


class CurrentBehaviourTests(unittest.TestCase):
    """当前实现（无 symbol）必须保持向后兼容 —— 这批断言**修复后仍须通过**。"""

    def test_10cm_limit_up_detected(self):
        self.assertTrue(bool(limit_up_mask(frame(10.0, 11.0)).iloc[-1]))

    def test_below_threshold_not_detected(self):
        self.assertFalse(bool(limit_up_mask(frame(10.0, 10.5)).iloc[-1]))

    def test_explicit_threshold_still_honoured(self):
        self.assertTrue(bool(limit_up_mask(frame(10.0, 10.5), 5.0).iloc[-1]))

    def test_default_signature_is_backward_compatible(self):
        # 修复后 `symbol` 必须是**可选**参数，否则所有既有调用方（strategies.py 等）静默改行为
        self.assertEqual(inspect.signature(limit_up_mask).parameters.get("pct_threshold").default, 9.8)


@unittest.skipUnless(_SUPPORTS_SYMBOL, _SKIP)
class SymbolAwareTests(unittest.TestCase):
    """修复目标：按板块判定，`sell.limit_pct_of` 为唯一真相源（留 0.2pp 容差）。"""

    def test_20cm_board_plus_10pct_is_not_limit_up(self):
        self.assertFalse(bool(limit_up_mask(frame(10.0, 11.0), symbol="300124").iloc[-1]))

    def test_20cm_board_plus_20pct_is_limit_up(self):
        self.assertTrue(bool(limit_up_mask(frame(10.0, 12.0), symbol="300124").iloc[-1]))

    def test_30cm_board_plus_10pct_is_not_limit_up(self):
        self.assertFalse(bool(limit_up_mask(frame(10.0, 11.0), symbol="830799").iloc[-1]))

    def test_30cm_board_plus_30pct_is_limit_up(self):
        self.assertTrue(bool(limit_up_mask(frame(10.0, 13.0), symbol="830799").iloc[-1]))

    def test_main_board_unchanged(self):
        self.assertTrue(bool(limit_up_mask(frame(10.0, 11.0), symbol="600644").iloc[-1]))
        self.assertFalse(bool(limit_up_mask(frame(10.0, 10.9), symbol="600644").iloc[-1]))

    def test_symbol_none_falls_back_to_legacy_threshold(self):
        self.assertTrue(bool(limit_up_mask(frame(10.0, 11.0), symbol=None).iloc[-1]))


if __name__ == '__main__':
    unittest.main(verbosity=2)
