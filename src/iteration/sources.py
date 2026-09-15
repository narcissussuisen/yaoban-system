"""资料扫描：把 选手学习资料/ 的当日事实来源做成哈希清单，只读、不写资料目录。

- 与既有 learning/tools/build_source_manifest.py 的差别：本模块只对「学习资料」子树做
  轻量增量哈希（每日批次用），不重建全量 manifest（5 千+ 文件，太重）。
- 新资料判定 = 相对上一份扫描快照新增或内容变更（sha256 变化）。
- 输出：outputs/iteration/<date>/materials_scan.json
"""
from __future__ import annotations

import hashlib
import json
import pathlib

MATERIAL_EXTS = {".md", ".txt", ".json", ".csv", ".png", ".jpg", ".jpeg", ".webp",
                 ".mp4", ".mkv", ".mov", ".docx", ".xlsx", ".pdf"}
# 跳过帧缓存等衍生目录（体积大、非一手事实来源）
SKIP_DIR_PARTS = {"frames", "__pycache__", ".git", "images_tmp"}


def sha256_of(path: pathlib.Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def iter_material_files(root: pathlib.Path):
    if not root.exists():
        return
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in MATERIAL_EXTS:
            continue
        if any(part in SKIP_DIR_PARTS for part in p.parts):
            continue
        yield p


def scan_materials(root: pathlib.Path, hash_limit_bytes: int = 32 << 20) -> dict:
    """扫描资料目录 → {relpath: {size, mtime, sha256?}}。超大视频只记大小+时间（哈希太贵）。"""
    files: dict[str, dict] = {}
    for p in iter_material_files(root):
        rel = p.relative_to(root).as_posix()
        st = p.stat()
        rec = {"size": st.st_size, "mtime": int(st.st_mtime)}
        if st.st_size <= hash_limit_bytes:
            rec["sha256"] = sha256_of(p)
        files[rel] = rec
    return {
        "root": str(root),
        "file_count": len(files),
        "files": files,
    }


def load_previous(out_root: pathlib.Path) -> dict | None:
    """取上一份扫描快照（按日期目录倒序找最近一份）。

    只认 `<date>/materials_scan.json`：`_superseded_*` 归档目录与散落文件不得参与比较，
    否则会把归档误当基线、或把台账文件误当快照目录。
    """
    if not out_root.exists():
        return None
    cands = [d for d in out_root.iterdir() if d.is_dir() and not d.name.startswith("_")]
    for d in sorted(cands, reverse=True):
        f = d / "materials_scan.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - 单份快照损坏不阻塞批次
                continue
    return None


def _changed(pf: dict, cf: dict) -> bool:
    """内容变更判定：有 sha256 用 sha256；超大视频（只记大小/时间）用大小，时间抖动不算变更。"""
    a, b = pf.get("sha256"), cf.get("sha256")
    if a and b:
        return a != b
    return pf.get("size") != cf.get("size")


def diff_scan(prev: dict | None, cur: dict) -> dict:
    """新增/变更/删除三类差异。无快照时全部视为新增（首次运行基线）。"""
    if not prev:
        return {"new": sorted(cur["files"]), "changed": [], "removed": [], "baseline": True}
    pf, cf = prev.get("files", {}), cur["files"]
    new = sorted(k for k in cf if k not in pf)
    changed = sorted(k for k in cf if k in pf and _changed(pf[k], cf[k]))
    removed = sorted(k for k in pf if k not in cf)
    return {"new": new, "changed": changed, "removed": removed, "baseline": False}


def run(workdir: pathlib.Path, materials_root: pathlib.Path, out_dir: pathlib.Path) -> dict:
    """产出 materials_scan.json；返回扫描+差异结果。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    cur = scan_materials(materials_root)
    prev = load_previous(workdir)
    d = diff_scan(prev, cur)
    result = {
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": cur["root"],
        "file_count": cur["file_count"],
        "new": d["new"], "changed": d["changed"], "removed": d["removed"],
        "baseline": d["baseline"],
        "files": cur["files"],
    }
    (out_dir / "materials_scan.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
