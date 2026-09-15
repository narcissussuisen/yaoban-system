# -*- coding: utf-8 -*-
"""R3.1 · 六段决策环引擎的 CLI 入口（实现全在 src/decision_chain/engine.py）。

用法:
    python scripts/run_decision_chain.py --date 2026-09-11
    python scripts/run_decision_chain.py --date 2026-09-11 --replay   # 重放一致性校验（R3 验收项）
"""
from __future__ import annotations

import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

from decision_chain.engine import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
