# -*- coding: utf-8 -*-
"""回归护栏：**含中文的 .ps1 必须带 UTF-8 BOM**。

背景（2026-09-13 实证，详见 docs/D11_BRAIN_SELL_ACTIVATION_2026-09-13.html §6）：
PowerShell 5.1 读**无 BOM** 文件时按系统 ANSI（中文系统 = CP936/GBK）解码；UTF-8 中文三字节
序列的末字节常落在 GBK 前导区（尤其 `。`(E3 80 82)、`）`(EF BC 89)），解码器见前导字节后
**无条件吞掉下一字节**——即使它是换行符 0x0A。结果：换行被吞、下一行代码被并进本行注释、
**静默不执行、零报错**。

已实测后果（修复前）：
  · `launch.ps1` 第 7 行 `$runId=('run-'+$stamp)` 被吞 ⇒ `task_log.run_id` 4171/4171 恒为 null，
    设计中的「task_log.run_id == close_decision.run_id」精确等值核对**在生产上恒不成立**；
  · `run_board_refresh.ps1` / `run_daily_iteration.ps1` 的交易日历检查行被吞 ⇒ **非交易日静默守卫失效**；
  · `register_daily_iteration.ps1` 的 `param(...)` 被吞 ⇒ 脚本实际不接受 `-VerifyOnly` / `-Unregister`。

⚠️ 这类缺陷**源码字符串断言拦不住**（文本在、执行被吞）。本测试改为钉**编码契约**：
含非 ASCII 且无 BOM ⇒ FAIL。纯 ASCII 文件不受编码影响，不做要求。
"""
import pathlib
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
EXTRA_ROOTS = [pathlib.Path(r"C:\Users\YZP\WorkBuddy\yaoban_tasks")]
SKIP = ("node_modules", ".git", "__pycache__", "outputs", "Vibe-Research", "py_libs", "data")
BOM = b"\xef\xbb\xbf"


def _ps1_files() -> list:
    out = []
    for root in [REPO, *EXTRA_ROOTS]:
        if root.exists():
            for f in root.rglob("*.ps1"):
                if not any(s in f.parts for s in SKIP):
                    out.append(f)
    return sorted(set(out))


class Ps1BomGuard(unittest.TestCase):
    def test_files_found(self):
        # 防止「扫描路径写错 → 0 个文件 → 测试恒绿」的假通过
        self.assertGreaterEqual(len(_ps1_files()), 10, "未扫到 .ps1，检查扫描根路径")

    def test_non_ascii_ps1_must_have_bom(self):
        bad = []
        for f in _ps1_files():
            b = f.read_bytes()
            if b[:3] == BOM:
                continue
            try:
                b.decode("ascii")
            except UnicodeDecodeError:
                bad.append(str(f))
        self.assertEqual(
            bad, [],
            "以下 .ps1 含非 ASCII 字符但缺 UTF-8 BOM —— PowerShell 5.1 会按 GBK 解码并吞行，"
            "把下一行代码静默并进注释（详见模块 docstring）。修复：在文件头加 EF BB BF。\n"
            + "\n".join(bad))

    def test_bom_not_stripped_from_known_critical_files(self):
        """关键入口文件的 BOM 单独钉死（防被「重写全文」的编辑器/工具顺手去掉）。"""
        critical = [REPO / "scripts" / "run_trading_task.ps1",
                    REPO / "scripts" / "post_close_chain.ps1",
                    pathlib.Path(r"C:\Users\YZP\WorkBuddy\yaoban_tasks\launch.ps1")]
        missing = [str(p) for p in critical if p.exists() and p.read_bytes()[:3] != BOM]
        self.assertEqual(missing, [], "关键 .ps1 的 UTF-8 BOM 丢失：" + str(missing))


if __name__ == "__main__":
    unittest.main()
