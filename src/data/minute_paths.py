# -*- coding: utf-8 -*-
"""分钟快照目录的**单一解析器**（R2.4，2026-09-12）。

## 为什么需要
`data/minute/` 被 **5 处**构造路径（`tools/snapshot_minute.py`、`src/iteration/intraday.py`、
`scripts/minute_incremental_snapshot.py`、`tools/_plan_minute_scope.py`、
`tools/run_intraday_counterfactual.py`）。用户已明确「**后续会转移到 F 盘**」——
若 5 处各自硬编码，迁移时必然漏改一处、静默读旧目录。

## 约定
- 环境变量 **`EVOALPHA_MINUTE_SNAPSHOT`** 覆盖根目录；**未设时默认** `<yaoban-system>/data/minute`（C 盘）。
- 迁移 F 盘 = **只改环境变量**（User 级 setx），**不动任何代码**。
- 与项目既有先例一致：`portfolio/ledger.py` 用 `EVOALPHA_LEDGER` 覆盖、LOCK 随 LEDGER 走
  （见 `docs/EVOALPHA_V2_RESTRUCTURE_PLAN.md` §R0.2a）。本模块把同一模式用于分钟快照。

用法:
    from data.minute_paths import snapshot_dir, parquet_path, meta_path, plan_path
    d = snapshot_dir("1m")              # <root>/1m
    p = parquet_path("600519", "1m")    # <root>/1m/600519.parquet
"""
from __future__ import annotations

import os
import pathlib

ENV_VAR = "EVOALPHA_MINUTE_SNAPSHOT"
_DEFAULT_ROOT = pathlib.Path(__file__).resolve().parents[2] / "data" / "minute"


def snapshot_root() -> pathlib.Path:
    """分钟快照根目录（环境变量优先）。"""
    raw = os.environ.get(ENV_VAR, "").strip()
    return pathlib.Path(raw) if raw else _DEFAULT_ROOT


def snapshot_dir(freq: str = "1m") -> pathlib.Path:
    return snapshot_root() / freq


def parquet_path(code: str, freq: str = "1m") -> pathlib.Path:
    return snapshot_dir(freq) / f"{code}.parquet"


def meta_path(code: str, freq: str = "1m") -> pathlib.Path:
    return snapshot_dir(freq) / f"{code}.meta.json"


def plan_path() -> pathlib.Path:
    """`tools/snapshot_minute.py` 的批量计划文件（`_fetch_plan.json`）。"""
    return snapshot_root() / "_fetch_plan.json"


def describe() -> str:
    raw = os.environ.get(ENV_VAR, "").strip()
    return (f"minute snapshot root = {snapshot_root()} "
            f"({'env ' + ENV_VAR if raw else 'default(C-drive)'})")
