"""环境自检：配置/依赖/数据库/案例库

用法: python scripts/verify_env.py
（akshare 检查需要 PYTHONPATH 指向 py_libs；缺失时降级提示不影响其余检查）
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

OK, WARN, FAIL = "OK", "WARN", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
        results.append((name, OK, ""))
    except Exception as e:  # noqa: BLE001
        results.append((name, FAIL, f"{type(e).__name__}: {str(e)[:120]}"))


def main():
    import platform

    results.append(("python", OK, platform.python_version()))

    def _config():
        import config

        assert len(config.rules()) >= 30, f"rules={len(config.rules())}"
    check("config/parameters.toml", _config)

    def _store():
        from data.store import Store

        s = Store()
        s.close()
    check("sqlite store", _store)

    def _cases():
        p = ROOT / "tests" / "cases.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        assert len(data["cases"]) >= 15, f"cases={len(data['cases'])}"
    check("tests/cases.json", _cases)

    def _envscore():
        import pandas as pd

        from core.env_score import env_score

        df = pd.DataFrame({
            "close": [i * 1.01 for i in range(100)],
            "volume": [1e8] * 100,
        })
        r = env_score(df, limit_up_count=120, max_board_height=5, red_pct=0.7)
        assert r["regime"] == "strong", r
    check("env_score smoke", _envscore)

    def _akshare():
        import akshare  # noqa: F401
    try:
        check("akshare import", _akshare)
    except Exception:
        results.append(("akshare import", WARN, "未设置 PYTHONPATH=../py_libs 或 py_libs 不可读（沙箱权限）"))

    print("=== 环境自检 ===")
    for name, status, msg in results:
        print(f"  [{status}] {name}" + (f" — {msg}" if msg else ""))
    fails = [r for r in results if r[1] == FAIL]
    print("\n结论:", "全部通过" if not fails else f"{len(fails)} 项失败，请修复后继续")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
