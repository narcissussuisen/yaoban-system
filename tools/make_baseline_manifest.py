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
    "tools",         # 2026-09-12 补入：run_intraday_counterfactual / snapshot_minute / _diag_* 等关键研究代码
    "config",        # parameters.toml / universe.toml / concepts.json
    "portfolio",     # ledger.json 权威账本 + ledger.py + 备份账本
    "data",          # yaoban.db / journal.db / 行业与股票名映射
    "docs",          # 2026-09-12 由 docs/loops 扩为全量：含 INCIDENT_LOG / reviews / 复盘结论
    "templates",
    "tests",
]
ROOT_FILES = ["README.md", "vendor_astock_skill.md"]
ROOT_GLOBS = ["*.py"]                    # 根目录 _r7p_* 等探针脚本

# 2026-09-12 更新：与 scripts/register_schedule.ps1 的 $Schedule 表对齐（19 项，单一真相源）
TASK_NAMES = [
    "YaobanPreflight",
    "YaobanSelfHeal",
    "YaobanPremarket",
    "YaobanPlanGate",
    "YaobanMorningCheck",
    "YaobanTdxProbe",
    "YaobanAuctionMonitor",
    "YaobanEventNotify",
    "YaobanTickDaemon",
    "YaobanScanConfirm",
    "YaobanIntradayMonitor",
    "YaobanClosePipeline",
    "YaobanPostCloseChain",
    "YaobanEveningCheck",
    "YaobanTdxServerVerify",
    "YaobanBoardRefresh",
    "YaobanStatusPush",
    "VibeResearchDashboardServices",
    "VibeResearchLiveTickValidation",
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


def _decode_console(raw: bytes) -> str:
    """schtasks 按控制台代码页（zh-CN 为 GBK）输出，直接按 utf-8 解码会得到乱码。"""
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# 兼容英文与 zh-CN 本地化的字段标签（不依赖系统语言）
_TASK_KEEP_KEYS = (
    "TaskName", "Task To Run", "Schedule Type", "Start Time", "Status",
    "Last Run Time", "Last Result", "Task To Run As",
    "任务名", "要运行的任务", "计划类型", "开始时间", "状态",
    "上次运行时间", "上次结果", "运行用户", "下次运行时间",
)


def snapshot_tasks(out_path: Path) -> None:
    lines = ["# Windows 计划任务注册状态快照", 
             f"# generated_at: {dt.datetime.now().isoformat(timespec='seconds')}", ""]
    for name in TASK_NAMES:
        try:
            r = subprocess.run(
                ["schtasks", "/query", "/tn", name, "/fo", "LIST", "/v"],
                capture_output=True, timeout=30)
            status = "REGISTERED" if r.returncode == 0 else "NOT_FOUND"
            lines.append(f"== {name} == status: {status}")
            if r.returncode == 0:
                stdout = _decode_console(r.stdout or b"")
                keep = [ln for ln in stdout.splitlines()
                        if any(k in ln for k in _TASK_KEEP_KEYS)]
                lines.extend(keep)
            else:
                lines.append(_decode_console(r.stderr or b"").strip() or "(no detail)")
            lines.append("")
        except Exception as exc:  # pragma: no cover
            lines.append(f"== {name} == ERROR: {exc}")
            lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def git_provenance() -> dict:
    """代码来源存证：HEAD commit / 分支 / 是否有未提交改动。"""
    out: dict = {}
    for key, args in (("head", ["git", "rev-parse", "HEAD"]),
                      ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
                      ("dirty", ["git", "status", "--porcelain"])):
        try:
            r = subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True,
                               text=True, timeout=30, encoding="utf-8", errors="replace")
            val = (r.stdout or "").strip()
            if key == "dirty":
                out["dirty_files"] = len([ln for ln in val.splitlines() if ln.strip()])
            else:
                out[key] = val or None
        except Exception as exc:  # pragma: no cover
            out[key] = f"ERROR: {exc}"
    return out


def collect_freeze_window() -> dict:
    """记录 decision_digest 重放用的冻结基线窗口。

    ledger_window = 主账本 equity_curve 的日期区间与点数（净值判定起算窗口）。
    日线/分钟数据窗口由 R2 数据层另行登记（数据不在本 repo 内，此处不猜路径）。
    """
    out: dict = {}
    ledger_path = REPO_ROOT / "portfolio" / "ledger.json"
    try:
        st = json.loads(ledger_path.read_text(encoding="utf-8"))
        acct = st.get("account") or {}
        curve = acct.get("equity_curve") or []
        dates = sorted({r.get("date") for r in curve if isinstance(r, dict) and r.get("date")})
        out["ledger_window"] = {
            "start": dates[0] if dates else None,
            "end": dates[-1] if dates else None,
            "points": len(dates),
            "start_cash": st.get("start_cash"),
            "start_date": st.get("start_date"),
            "fills": len(acct.get("fills") or []),
            "positions": len(acct.get("positions") or {}),
            "revision": st.get("_revision"),
            "risk_state": st.get("risk_state"),
        }
    except Exception as exc:
        out["ledger_window"] = {"error": str(exc)}
    return out




def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-id", required=True, help="如 baseline-v2-pre-restructure")
    ap.add_argument("--purpose", default="P0.1 缺陷版存证 manifest（Phase 0 修复前冻结）")
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
        "purpose": args.purpose,
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
        "git": git_provenance(),
        "freeze_window": collect_freeze_window(),
        "acceptance": "同一输入可重放同一 Top 5",
        "replay_note": "freeze_window.ledger_window 的 [start, end] 即 decision_digest 重放的冻结基线窗口；"
                       "日线/分钟数据窗口由 R2 数据层另行登记（数据不在本 repo 内）。",
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK files={len(entries)} bytes={total_bytes} manifest_hash={manifest_hash}")
    print(f"OUT {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
