# -*- coding: utf-8 -*-
"""选股窗口/排序键「防静默回退」棘轮（2026-09-15 立）。

【为什么需要这个测试】
2026-09-15 用户报「备选股池以及买入标的无一命中」。根因之一是一次**静默回退**：
    commit ae73dff（9/15 05:48，用户裁定「今天实盘前上线」）把战法池 `lookback 4 → 6`，
    并留下注释「池的召回窗与 picks 窗**不是同一口径**，勿再"对齐"回去」。
    commit f496be6（9/15 09:03）把 lookback **退回 4**，理由注释写成「lookback=4 与下面 win_days 同口径」——
    而 win_days 当时是**硬编码 4** ⇒ 「对齐」在代码上从未成立，却让计划层比池层少看 2 个交易日。
代价（用 2026-09-15 池产物实测）：池内 huigui 1133 只，4 日窗只见 537 只、6 日窗 1131 只（+110.6%）；
    **596 只被窗口结构性排除**，含选手当日**唯一被买入**的 沃特股份(sz002886, sig=2026-09-07)。

⇒ 本测试的作用不是"算得对"，而是**钉住结构性不变量**，让下一次回退在 CI 里立刻失败。

【三条不变量】
1. 常量存在且可取：`PATTERN_LOOKBACK` / `PICKS_SIGNAL_WINDOW`；
2. **`PICKS_SIGNAL_WINDOW >= PATTERN_LOOKBACK`**（备选域不得窄于召回闸门，
   否则池里有的票计划层永远看不到）；
3. 两个窗口**都不得以数字字面量写死**（必须是 Name 引用常量），
   且排序键必须显式声明 + `kind="mergesort"`（原实现用默认不稳定排序 ⇒ 同 L2 并列时"任取 2 只"）。
"""
from __future__ import annotations

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "plan_daily.py"


def _tree() -> ast.Module:
    return ast.parse(SRC.read_text(encoding="utf-8"))


def _module_constants() -> dict:
    out = {}
    for node in _tree().body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name):
                try:
                    out[t.id] = ast.literal_eval(node.value)
                except Exception:
                    pass
    return out


class ConstantsTests(unittest.TestCase):

    def test_constants_exist_and_are_ints(self):
        c = _module_constants()
        for k in ("PATTERN_LOOKBACK", "PICKS_SIGNAL_WINDOW"):
            self.assertIn(k, c, f"{k} 必须作为模块级常量存在（勿退回字面量）")
            self.assertIsInstance(c[k], int, f"{k} 必须是 int")

    def test_picks_window_not_narrower_than_pool_lookback(self):
        """核心不变量：备选域不得窄于召回闸门（本次"无一命中"的直接结构原因）。"""
        c = _module_constants()
        self.assertGreaterEqual(
            c["PICKS_SIGNAL_WINDOW"], c["PATTERN_LOOKBACK"],
            "picks 信号窗 < 池召回窗 ⇒ 池内近若干交易日的信号在计划层不可见（回归！）",
        )


class NoHardcodedWindowTests(unittest.TestCase):
    """两个窗口都必须是常量引用；排序必须显式 + 稳定。"""

    def test_pattern_lookback_passed_as_name_not_literal(self):
        hits = []
        for node in ast.walk(_tree()):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name == "build_pattern_artifact":
                    for kw in node.keywords:
                        if kw.arg == "lookback":
                            hits.append(kw.value)
        self.assertTrue(hits, "未找到 build_pattern_artifact(..., lookback=...) 调用点")
        for v in hits:
            self.assertIsInstance(
                v, ast.Name,
                "lookback 必须以常量名传入（裸数字会被静默改回 4，见 ae73dff→f496be6 事故）",
            )
            self.assertEqual(v.id, "PATTERN_LOOKBACK")

    def test_win_days_loop_uses_constant(self):
        """`while len(_w) < N` 的 N 必须是 Name（原为硬编码 4）。"""
        found = []
        for node in ast.walk(_tree()):
            if isinstance(node, ast.While):
                test = node.test
                if not isinstance(test, ast.Compare):
                    continue
                left = test.left
                if not (isinstance(left, ast.Call) and getattr(left.func, "id", None) == "len"):
                    continue
                for cmp in test.comparators:
                    found.append(cmp)
        self.assertTrue(found, "未找到 `while len(_w) < ...` 窗口循环")
        for cmp in found:
            self.assertIsInstance(cmp, ast.Name, "窗口长度必须是常量名，不得硬编码")
            self.assertEqual(cmp.id, "PICKS_SIGNAL_WINDOW")

    def test_sort_is_stable_and_declared(self):
        src = SRC.read_text(encoding="utf-8")
        self.assertIn('kind="mergesort"', src,
                      "picks 排序必须显式指定稳定排序（原默认 quicksort 在并列上等价于任取）")
        self.assertIn("PICKS_SORT_KEYS", src, "排序键必须抽成常量，便于 A/B 与裁定")
        self.assertIn("PICKS_SORT_ASC", src, "排序方向必须显式声明")


class RatchetSelfCheckTests(unittest.TestCase):
    """棘轮自身有效性：若源码结构被大改到扫不到东西，测试必须失败而不是静默通过。"""

    def test_source_actually_scanned(self):
        src = SRC.read_text(encoding="utf-8")
        self.assertGreater(len(src), 5000, "plan_daily.py 过小 ⇒ 路径或文件被换掉了")
        self.assertIn("def main(", src)
        self.assertIn("build_pattern_artifact", src)


class CrossModuleDefaultTests(unittest.TestCase):
    """两个建池入口的**默认窗口必须一致**（第三处历史残留，2026-09-15 修）。

    事故形态：`plan_daily` 用的值被静默退回 4，而 `core.pattern_pool` 的**函数默认值**
    长期是 4、`scripts/_legacy/build_pattern_pool.py` 的 `--lookback` 默认却是 6 ——
    **同一件事三个数**。任何一处不同步，都会让"池窗/计划窗"错位再次发生。
    """

    def _load(self):
        import importlib
        import inspect
        for p in (str(ROOT / "src"), str(ROOT), str(ROOT / "scripts")):
            if p not in sys.path:
                sys.path.insert(0, p)
        pp = importlib.import_module("core.pattern_pool")
        pd = importlib.import_module("plan_daily")
        sig = inspect.signature(pp.build_pattern_pool)
        return sig.parameters["lookback"].default, pd.PATTERN_LOOKBACK, pd.PICKS_SIGNAL_WINDOW

    def test_defaults_are_identical(self):
        pool_default, plan_lookback, plan_picks = self._load()
        self.assertEqual(
            pool_default, plan_lookback,
            "core.pattern_pool.build_pattern_pool 的默认 lookback 与 plan_daily.PATTERN_LOOKBACK "
            "不一致 ⇒ 同一件事两个数（历史上正是这样错位的）",
        )
        self.assertGreaterEqual(
            plan_picks, plan_lookback,
            "picks 窗窄于池窗 ⇒ 池内候选对计划层不可见",
        )

    def test_legacy_script_cli_default_matches(self):
        """归档脚本 `--lookback` 的 CLI 默认值也必须一致（不能只有函数默认值对）。"""
        legacy = ROOT / "scripts" / "_legacy" / "build_pattern_pool.py"
        self.assertTrue(legacy.exists(), "归档脚本不在预期路径 ⇒ 请同步本用例")
        src = legacy.read_text(encoding="utf-8")
        pool_default, _, _ = self._load()
        self.assertIn(
            f"default={pool_default}", src,
            f"归档脚本的 --lookback 默认值不是 {pool_default} ⇒ 单独跑会建出不同窗口的池",
        )


if __name__ == "__main__":
    unittest.main()
