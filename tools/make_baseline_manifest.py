# -*- coding: utf-8 -*-
"""P0.1 baseline manifest 冻结工具。

对 yaoban-system 当前生产真实状态生成不可变存证：
- manifest.jsonl : 每文件一行 {path, size, mtime, sha256}，按 path 排序
- summary.json   : 顶层 manifest_hash(= manifest.jsonl 内容 sha256)、范围、排除规则
- tasks_registered.txt : Windows 计划任务注册状态快照

用法:
    python tools/make_baseline_manifest.py --baseline-id baseline-0-pre-fix
"""
import argparse
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {"__pycache__", ".git", ".mypy_cache", ".pytest_cache"}
EXCLUDE_DIR_PREFIXES = ("tmp",)          # tmphcrsvf6c / tmplvguwe_p 等临时目录
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".lock"}
EXCLUDE_FILES = {"ledger.lock"}          # 运行期锁文件，不具存证语义

# 冻结范围：生产代码 + 配置 + 账本 + 数据 + 状态与结论文档 + 任务定义
SCOPES = [
    "scripts",       # 全部生产/研究脚本与 *.ps1/*.cmd/*.xml 任务定义
    "src",           # 核心库
    "config",        # parameters.toml / universe.toml / concepts.json
    "portfolio",     # ledger.json 权威账本 + ledger.py + 备份账本
    "data",          # yaoban.db / journal.db / 行业与股票名映射
    "docs/loops",    # state.json 与阶段结论（状态机存证）
    "templates",
    "tests",
]
ROOT_FILES = ["README.md", "vendor_astock_skill.md"]
ROOT_GLOBS = ["*.py"]                    # 根目录 _r7p_* 等探针脚本

TASK_NAMES = [
    "YaobanDailyCandidates",
    "YaobanDailySignal",
    "YaobanLoopEngine",
    "YaobanTickCollect",
]


def sha256_file(p: Path, buf_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        while True:
            chunk = f.read(buf_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def collect_files() -> list[Path]:
    files: list[Path] = []
    for scope in SCOPES:
        base = REPO_ROOT / scope
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_dir():
                continue
            if any(part in EXCLUDE_DIRS for part in p.relative_to(REPO_ROOT).parts):
                continue
            if any(part.startswith(EXCLUDE_DIR_PREFIXES) for part in p.relative_to(REPO_ROOT).parts[:-1]):
                continue
            if p.suffix.lower() in EXCLUDE_SUFFIXES or p.name in EXCLUDE_FILES:
                continue
            files.append(p)
    for name in ROOT_FILES:
        p = REPO_ROOT / name
        if p.exists():
            files.append(p)
    for pattern in ROOT_GLOBS:
        files.extend(p for p in REPO_ROOT.glob(pattern) if p.is_file())
    return sorted(set(files), key=lambda p: p.relative_to(REPO_ROOT).as_posix())


def snapshot_tasks(out_path: Path) -> None:
    lines = ["# Windows 计划任务注册状态快照", 
             f"# generated_at: {dt.datetime.now().isoformat(timespec='seconds')}", ""]
    for name in TASK_NAMES:
        try:
            r = subprocess.run(
                ["schtasks", "/query", "/tn", name, "/fo", "LIST", "/v"],
                capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
            status = "REGISTERED" if r.returncode == 0 else "NOT_FOUND"
            lines.append(f"== {name} == status: {status}")
            if r.returncode == 0:
                keep = [ln for ln in (r.stdout or "").splitlines()
                        if any(k in ln for k in ("TaskName", "Task To Run", "Schedule Type",
                                                 "Start Time", "Status", "Last Run Time",
                                                 "Last Result", "Task To Run As"))]
                lines.extend(keep)
            else:
                lines.append(r.stderr.strip() or "(no detail)")
            lines.append("")
        except Exception as exc:  # pragma: no cover
            lines.append(f"== {name} == ERROR: {exc}")
            lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-id", required=True, help="如 baseline-0-pre-fix")
    args = ap.parse_args()

    out_dir = REPO_ROOT / "baseline" / "manifests" / args.baseline_id
    out_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    total_bytes = 0
    for p in collect_files():
        rel = p.relative_to(REPO_ROOT).as_posix()
        st = p.stat()
        total_bytes += st.st_size
        entries.append({
            "path": rel,
            "size": st.st_size,
            "mtime": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "sha256": sha256_file(p),
        })

    manifest_path = out_dir / "manifest.jsonl"
    manifest_bytes = "\n".join(json.dumps(e, ensure_ascii=False, sort_keys=True) for e in entries).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()

    tasks_path = out_dir / "tasks_registered.txt"
    snapshot_tasks(tasks_path)

    by_scope: dict[str, dict[str, int]] = {}
    for e in entries:
        top = e["path"].split("/", 1)[0]
        d = by_scope.setdefault(top, {"files": 0, "bytes": 0})
        d["files"] += 1
        d["bytes"] += e["size"]

    summary = {
        "baseline_id": args.baseline_id,
        "purpose": "P0.1 缺陷版存证 manifest（Phase 0 修复前冻结）",
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "repo_root": str(REPO_ROOT),
        "manifest_hash": manifest_hash,
        "manifest_file": "manifest.jsonl",
        "file_count": len(entries),
        "total_bytes": total_bytes,
        "by_scope": by_scope,
        "exclude_dirs": sorted(EXCLUDE_DIRS),
        "exclude_dir_prefixes": list(EXCLUDE_DIR_PREFIXES),
        "exclude_suffixes": sorted(EXCLUDE_SUFFIXES),
        "exclude_files": sorted(EXCLUDE_FILES),
        "task_names_checked": TASK_NAMES,
        "acceptance": "同一输入可重放同一 Top 5（P0.2-P0.4 完成后另行生成 baseline-0 v1 manifest 作为三轨评价基准）",
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK files={len(entries)} bytes={total_bytes} manifest_hash={manifest_hash}")
    print(f"OUT {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
