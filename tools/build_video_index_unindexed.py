# -*- coding: utf-8 -*-
"""R2.6：给 library_scan 的 #1–#6 六期建立**独立**编号注册表。

为什么独立、而不是并进 video_index.json
-------------------------------------
`persona/video_index.json` 属 **R1.5 v0 的 frozen 组**（人格定义的一部分）。
往里加内容会触发版本纪律（frozen 漂移 ⇒ 必须出 v1）。
但这 6 期**本就在 vNN 体系之外**（它们占据清单 #1–#6，正是 `vNN = 序号 − 6` 偏移的成因），
且它们承载的是**数据层溯源**而非人格定义 → 故单列一个注册表，**不进 frozen 组**。

编号约定（关键：不可重排）
------------------------
    u01 … u06  =  library_scan 清单 #1–#6（后加入的 6 期）
    v01 … v76  =  library_scan 清单 #7–#82（原 vNN 体系，被 52 条规则的 src 引用）

⚠️ **绝不把 uNN 重排成 vNN** —— 会作废 52 条规则的 src。
uNN 与 vNN 也不可能混淆：现有校验器用 `\\bv(\\d{1,2})\\b`，而 `u01` 不匹配该模式。

产出：persona/video_index_unindexed.json（含每期的实物路径 + 帧目录 + 分析成果文档）
"""

from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(r"C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\EvoAlpha")
YS = ROOT / "yaoban-system"
STUDY = ROOT / "选手学习资料"
TEAR = STUDY / "妖板选手视频拆解"
ALL = ROOT.parent / "evoalpha_all"
TECH = ALL / "妖板选手方法论拆解"
OUT = YS / "persona/video_index_unindexed.json"


def rel(p: pathlib.Path) -> str:
    """相对路径。注意帧目录在 `evoalpha_all/` 下（**不在 EvoAlpha 内**）→ 需回退到共同父目录。"""
    for base in (ROOT, ROOT.parent):
        try:
            return str(p.relative_to(base)).replace("\\", "/")
        except ValueError:
            continue
    return str(p)


# 六期的编号与语义（清单 #1–#6）
SPEC = [
    (1, "u01", "2026-08-19"),
    (2, "u02", "2026-08-20"),
    (3, "u03", "2026-08-24"),
    (4, "u04", "2026-08-25"),
    (5, "u05", "2026-08-26"),
    (6, "u06", ""),  # 清单 #6 = 小白炒股知识点：成交量（文件名无日期前缀）
]

FRAMES_DIRS = {
    "u01": "new_frames_pil/v1", "u02": "new_frames_pil/v2", "u03": "new_frames_pil/v3",
    "u04": "new_frames_pil/v4", "u05": "new_frames_pil/v5", "u06": "new_frames_pil/v6",
}


def parse_scan():
    rows = []
    row_re = re.compile(r"^\|\s*(\d+)\s*\|(.+?)\|(.+?)\|\s*(\d+)\s*\|(.+?)\|\s*([\d.]+)\s*\|\s*$")
    for line in (TEAR / "_meta/library_scan.md").read_text(encoding="utf-8").splitlines():
        m = row_re.match(line.strip())
        if not m:
            continue
        rows.append(dict(idx=int(m.group(1)), subdir=m.group(2).strip().strip("`"),
                         mp4=m.group(3).strip().strip("`"), dur=int(m.group(4)),
                         res=m.group(5).strip(), mb=float(m.group(6))))
    return rows


def main():
    scan = parse_scan()
    mp4_dir = STUDY / "人工录入资料/妖板选手"
    out = {
        "scheme": "uNN = library_scan 逐期清单 #1–#6（后加入的 6 期，不参与 vNN 体系）",
        "why_separate": "video_index.json（vNN 体系）属 R1.5 v0 的 frozen 组；这 6 期承载数据层溯源而非人格定义，"
                        "故单列注册表，不进 frozen 组 —— 避免因补编号而触发人格版本变更。",
        "warning": "绝不把 uNN 重排成 vNN（会作废 52 条规则的 src）。uNN 与 vNN 不会混淆："
                   "现有校验用 \\bv(\\d{1,2})\\b，u01 不匹配。",
        "vnn_offset": "vNN = library_scan 清单序号 − 6（故 #1–#6 无 vNN）",
        "entries": [],
    }
    problems = []
    for idx, uid, date in SPEC:
        r = next((x for x in scan if x["idx"] == idx), None)
        if r is None:
            problems.append(f"清单缺 #{idx}")
            continue
        base = r["mp4"].split("/")[-1]
        # 磁盘实物：先按完整文件名精确匹配；失败再按日期数字前缀回退
        cands = [p for p in mp4_dir.rglob("*.mp4") if p.name == base]
        if not cands and date:
            digits = re.sub(r"\D", "", date)[:8]
            if len(digits) == 8:
                cands = [p for p in mp4_dir.rglob("*.mp4")
                         if re.sub(r"\D", "", p.name[:10]) == digits]
        frames_rel = FRAMES_DIRS.get(uid, "")
        frames = TECH / frames_rel if frames_rel else None
        n_frames = len(list(frames.glob("*"))) if frames and frames.is_dir() else 0
        e = dict(id=uid, list_index=idx, declared_date=date, title=base,
                 list_mp4=r["mp4"], duration_s=r["dur"], resolution=r["res"], size_mb=r["mb"],
                 disk_mp4=rel(cands[0]) if cands else None,
                 disk_matches=len(cands),
                 frames_dir=rel(frames) if (frames and frames.is_dir()) else None,
                 frames_count=n_frames)
        if not cands:
            problems.append(f"{uid}: 磁盘找不到 {base}")
        if n_frames == 0:
            problems.append(f"{uid}: 帧目录为空或缺失（{frames_rel}）")
        out["entries"].append(e)

    out["analysis_docs"] = []
    for p in (TECH / "docs/NEW_VIDEOS_READ_2026-08-26.md",
              STUDY / "成交量六种形态-学习笔记.md",
              TECH / "ocr_text"):
        rel_p = rel(p) if p.exists() else None
        out["analysis_docs"].append({"path": rel_p, "kind": "dir" if p.is_dir() else "file",
                                     "exists": p.exists()})
    # ocr_text 里那 6 个空文件（OCR 未完成的证据）
    empties = []
    for f in sorted((TECH / "ocr_text").glob("NEW_*.md")) if (TECH / "ocr_text").is_dir() else []:
        empties.append({"file": f.name, "bytes": f.stat().st_size})
    out["ocr_status"] = {
        "note": "这 6 期的 OCR 任务启动过但**未完成** → ocr_text/NEW_v*.md 全为空文件；"
                "现有成果只有人工逐帧识读（NEW_VIDEOS_READ_2026-08-26.md）",
        "files": empties,
    }
    out["coverage"] = dict(
        entries=len(out["entries"]),
        with_disk_mp4=sum(1 for e in out["entries"] if e["disk_mp4"]),
        with_frames=sum(1 for e in out["entries"] if e["frames_count"] > 0),
        total_frames=sum(e["frames_count"] for e in out["entries"]),
        problems=problems,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"OK u01–u06 注册表 → {OUT.name}")
    print(f"   有磁盘实物 {out['coverage']['with_disk_mp4']}/6 ；"
          f"有帧 {out['coverage']['with_frames']}/6 ；帧总数 {out['coverage']['total_frames']}")
    for e in out["entries"]:
        print(f"   {e['id']}  #{e['list_index']}  {e['declared_date']:<22} "
              f"{e['title'][:44]:<46} frames={e['frames_count']:<3} disk={bool(e['disk_mp4'])}")
    if problems:
        print("   ⚠️ problems:")
        for p in problems:
            print("      -", p)
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
