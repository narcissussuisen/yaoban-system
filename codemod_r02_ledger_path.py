# -*- coding: utf-8 -*-
"""R0.2 账本路径收敛 codemod。

把 8 个脚本里硬编码的 `BASE / "portfolio" / "ledger.json"` 收敛到
`from ledger import LEDGER`（ledger.py 已支持 EVOALPHA_LEDGER 环境变量）。

原则：
- 每个替换必须**精确命中预期次数**，否则整体中止（不静默跳过、不部分写入）
- 保留原文件 BOM 状态
- 已存在 import 的文件视为已完成（幂等）
- 写完逐文件 py_compile 校验
"""
import py_compile
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
SCRIPTS = REPO / "scripts"

IMPORT_BLOCK = (
    'sys.path.insert(0, str(BASE / "portfolio"))\n'
    'from ledger import LEDGER  # noqa: E402  env-aware (EVOALPHA_LEDGER) — R0.2 单一真相源\n'
)
SENTINEL = "from ledger import LEDGER"

EDITS = [
    {
        "file": "feishu_notify.py",
        "repl": [(
            'ledger = json.loads((BASE / "portfolio" / "ledger.json").read_text(encoding="utf-8"))',
            'ledger = json.loads(LEDGER.read_text(encoding="utf-8"))',
            1,
        )],
    },
    {
        "file": "evening_check.py",
        "repl": [
            ('        led = _json(BASE / "portfolio" / "ledger.json") or {}',
             '        led = _json(LEDGER) or {}', 1),
            ('    led = _json(BASE / "portfolio" / "ledger.json") or {}',
             '    led = _json(LEDGER) or {}', 1),
        ],
    },
    {
        "file": "day_timeline.py",
        "extra_insert": (
            'try:\n'
            '    _LEDGER_LABEL = str(LEDGER.relative_to(BASE))\n'
            'except ValueError:\n'
            '    _LEDGER_LABEL = str(LEDGER)\n'
        ),
        "repl": [
            (' led = _json(BASE / "portfolio" / "ledger.json")',
             ' led = _json(LEDGER)', 1),
            ('  d.missing.append("portfolio/ledger.json")',
             '  d.missing.append(_LEDGER_LABEL)', 1),
            ('        "portfolio/ledger.json · account.fills")',
             '        f"{_LEDGER_LABEL} · account.fills")', 1),
        ],
    },
    {
        "file": "notify_trading_events.py",
        "repl": [(
            " ledger=_json(BASE/'portfolio'/'ledger.json',{})",
            " ledger=_json(LEDGER,{})",
            1,
        )],
    },
    {
        "file": "collect_static_readiness.py",
        "repl": [(
            "'ledger_exists':(BASE/'portfolio'/'ledger.json').exists()",
            "'ledger_exists':LEDGER.exists()",
            1,
        )],
    },
    {
        "file": "collect_daily_acceptance.py",
        "repl": [(
            "ledger=read_json(BASE/'portfolio'/'ledger.json') or {}",
            "ledger=read_json(LEDGER) or {}",
            1,
        )],
    },
    {
        "file": "status_push.py",
        "repl": [(
            '    ledger = _json(BASE / "portfolio" / "ledger.json", {}) or {}',
            '    ledger = _json(LEDGER, {}) or {}',
            2,
        )],
    },
    {
        "file": "preflight.py",
        "repl": [(
            "  led=json.loads((BASE/'portfolio'/'ledger.json').read_text(encoding='utf-8'))",
            "  led=json.loads(LEDGER.read_text(encoding='utf-8'))",
            1,
        )],
    },
]


def patch(name: str, spec: dict) -> str:
    p = SCRIPTS / name
    raw = p.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    if SENTINEL in text:
        return f"SKIP(already) {name}"
    if not re.search(r"^\s*import\s+[^\n]*\bsys\b", text, re.M):
        need_sys = True
    else:
        need_sys = False

    for old, new, expect in spec["repl"]:
        got = text.count(old)
        if got != expect:
            raise AssertionError(f"{name}: 期望命中 {expect} 次，实得 {got} 次 -> {old[:70]!r}")
        text = text.replace(old, new)

    # 在 BASE 定义行之后插入 import 块
    lines = text.splitlines(keepends=True)
    idx = None
    for i, ln in enumerate(lines):
        if ln.startswith("BASE") and "pathlib.Path(__file__)" in ln:
            idx = i
            break
    if idx is None:
        raise AssertionError(f"{name}: 未找到 BASE = pathlib.Path(__file__) 定义行")
    ins = ("import sys\n" if need_sys else "") + IMPORT_BLOCK + spec.get("extra_insert", "")
    lines.insert(idx + 1, ins)
    text = "".join(lines)

    out = text.encode("utf-8")
    if bom:
        out = b"\xef\xbb\xbf" + out
    p.write_bytes(out)

    # 编译校验
    py_compile.compile(str(p), doraise=True, cfile=str(p) + ".codemod.pyc")
    Path(str(p) + ".codemod.pyc").unlink(missing_ok=True)
    return f"OK {name}"


def main() -> int:
    results = []
    for spec in EDITS:
        try:
            results.append(patch(spec["file"], spec))
        except Exception as exc:
            print("\n".join(results))
            print(f"FAIL {spec['file']}: {exc}")
            return 1
    print("\n".join(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
