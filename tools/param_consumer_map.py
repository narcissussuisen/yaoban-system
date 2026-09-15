"""parameters.toml 消费者地图 —— 可重跑（R5.0「唯一真相源」治理工具）。

用途：回答「这个参数到底谁在读」。任何"删参数/改参数/收敛单一源"的动作，
      都必须先跑本工具确认消费者，**禁止凭印象删除**。

输出：persona/_param_consumer_map.report.txt（+ stdout）

三张表：
  ① 段级消费者映射（`section("x")` 调用点 → 文件:行）
  ② 模块级 `X = section(...)` 变量的**真实使用统计** —— 用来识别「定义后未使用」的死变量
  ③ 旁路读取路径（`[strategy.*]` 不走 `section()`，而走 `config.strategy(name)` + `rules.rule_params()`）

⚠️ 局限（如实标注）：静态分析，抓不到动态取键（如 `section(s)[k]`）；`[strategy.*]` 的
   子键消费者需结合 `src/iteration/rules.py::rule_params` 人工确认。
"""
from __future__ import annotations

import collections
import pathlib
import re
import sys
import tomllib

BASE = pathlib.Path(__file__).resolve().parent.parent
PARAMS = BASE / "config" / "parameters.toml"
OUT = BASE / "persona" / "_param_consumer_map.report.txt"

SKIP_PARTS = {"py_libs", "node_modules", ".venv", "venv", "__pycache__", ".git"}

SEC = re.compile(r"section\(['\"]([a-z_]+)['\"]\)")
VAR = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*section\(['\"]([a-z_]+)['\"]\)", re.M)


def main() -> int:
    if not PARAMS.exists():
        print(f"FAIL: {PARAMS} 不存在", file=sys.stderr)
        return 2
    params = tomllib.loads(PARAMS.read_text(encoding="utf-8"))

    seg_keys = {k: sorted(v.keys()) for k, v in params.items() if isinstance(v, dict)}

    py_files = [p for p in BASE.rglob("*.py") if not (SKIP_PARTS & set(p.parts))]

    calls, module_vars, bypass = [], [], []
    for p in py_files:
        try:
            src = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        rel = p.relative_to(BASE)
        for i, ln in enumerate(src.splitlines(), 1):
            m = SEC.search(ln)
            if m:
                calls.append((m.group(1), rel, i))
            if ("rule_params(" in ln or '["strategy' in ln or "['strategy" in ln
                    or 'get("strategy"' in ln):
                bypass.append((rel, i, ln.strip()[:96]))
        for m in VAR.finditer(src):
            var, seg = m.group(1), m.group(2)
            # 减去定义行本身的一次出现
            uses = len(re.findall(rf"\b{var}\b", src)) - 1
            module_vars.append((var, seg, rel, uses))

    L = ["=" * 78, "parameters.toml 消费者地图", "=" * 78, ""]

    L.append("── 段清单 ──")
    for k, ks in seg_keys.items():
        L.append(f"  [{k}]  {len(ks)} keys" + (f"   e.g. {ks[:4]}" if ks else ""))
    L.append("")

    by_seg = collections.defaultdict(list)
    for seg, f, i in calls:
        by_seg[seg].append(f"{f}:{i}")
    L.append("── ① 段级消费者（section 调用点）──")
    for seg in sorted(by_seg):
        L.append(f"  [{seg:<12}] {', '.join(by_seg[seg])}")
    unread = [s for s in seg_keys if s not in by_seg]
    L.append("")
    L.append(f"  ⚠️ 无 section() 调用的段：{unread}（可能走旁路，见 ③）")
    L.append("")

    L.append("── ② 模块级变量：是否真被使用 ──")
    for var, seg, f, n in sorted(module_vars, key=lambda x: x[3]):
        flag = "⚠️ **定义后未使用（死变量）**" if n <= 0 else f"使用 {n} 次"
        L.append(f"  {var:<10} = section(\"{seg}\")   {f}   {flag}")
    L.append("")

    L.append("── ③ 旁路读取路径（不经 section()）──")
    seen = set()
    for f, i, t in bypass:
        if (str(f), t) in seen:
            continue
        seen.add((str(f), t))
        L.append(f"  {f}:{i}  {t}")
    L.append("")
    L.append("提示：`[strategy.*]` 经 `src/config.py::strategy(name)` 与")
    L.append("      `src/iteration/rules.py::rule_params(name, config)` 消费。")

    out = "\n".join(L)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(out, encoding="utf-8")
    print(out)
    print(f"\n→ {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
