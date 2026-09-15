# -*- coding: utf-8 -*-
"""R1.1 前置：建立 `vNN` → 视频实物 的溯源索引，并校验 parameters.toml 全部出处。

背景
----
`yaoban-system/config/parameters.toml` 的 37 条规则用 `vNN@MM:SS` 标注出处，
但 `vNN` 的编号约定此前**未被任何文档定义**。本脚本从
`选手学习资料/妖板选手视频拆解/_meta/library_scan.md` 的 82 期清单反解编号：

    vNN = 清单总序号 - 6

（`-20260826/` 子目录的 6 期是后加入的，占据 #1–#6；原编号只覆盖
`-260818/` 的 72 期与根目录的 4 期，即 v01..v76。）

交叉验证用的 6 组独立证据（编号 → 日期 → 主题是否吻合 + 时间戳是否落在时长内）：
    v51 → #57 = 2026-07-12 主力洗盘三招        （洗盘类规则，02:40 ≤ 211s）
    v70 → #76 = 2026-08-16 仙人指路选漂战法    （仙人指路类规则，04:10 ≤ 293s）
    v14 → #20 = 2026-05-17 散户专属做T秘籍     （做T类规则，02:22 ≤ 182s）
    v15 → #21 = 2026-05-20 仓位管理技巧        （仓位类规则，00:43 ≤ 141s）
    v57 → #63 = 2026-07-24 成交额分析法        （环境-成交额规则，02:10 ≤ 220s）
    v07 → #13 = 2026-04-28 涨停回踩低吸        （涨停回踩类规则，01:46 ≤ 166s）

本脚本做三件事：
 1. 解析 82 期清单 → `video_index.json`（vNN/日期/文件名/时长/分辨率/子目录）
 2. 把每期匹配到 `妖板选手视频拆解/<分类>/<YYYYMMDD>_<标题>/` 的 record.md 与 frames/
 3. **校验** parameters.toml 全部 37 条的出处：vNN 是否在 1..76、时间戳是否 ≤ 时长、
    多视频来源是否逐个可解析。落盘校验报告。

输出（均 UTF-8，不经 shell 重定向 —— 本机 PowerShell 会写成 UTF-16）：
  yaoban-system/persona/video_index.json
  yaoban-system/persona/_build_video_index.report.txt
"""

import json
import pathlib
import re
import tomllib

ROOT = pathlib.Path(r"C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\EvoAlpha")
SCAN_MD = ROOT / "选手学习资料/妖板选手视频拆解/_meta/library_scan.md"
DETAIL_ROOT = ROOT / "选手学习资料/妖板选手视频拆解"
PARAMS = ROOT / "yaoban-system/config/parameters.toml"
OUT_DIR = ROOT / "yaoban-system/persona"
OUT_JSON = OUT_DIR / "video_index.json"
OUT_REPORT = OUT_DIR / "_build_video_index.report.txt"

V_OFFSET = 6  # vNN = 清单总序号 - V_OFFSET

buf = []
w = buf.append


# ---------------------------------------------------------------- 1. 解析清单
def parse_scan(text: str):
    """解析 library_scan.md 的「逐期清单」表 → [{idx, subdir, mp4, date, title, dur_s, res, mb}]"""
    rows = []
    row_re = re.compile(r"^\|\s*(\d+)\s*\|(.+?)\|(.+?)\|\s*(\d+)\s*\|(.+?)\|\s*([\d.]+)\s*\|\s*$")
    for line in text.splitlines():
        m = row_re.match(line.strip())
        if not m:
            continue
        idx = int(m.group(1))
        subdir = m.group(2).strip().strip("`")
        mp4 = m.group(3).strip().strip("`")
        dur = int(m.group(4))
        res = m.group(5).strip()
        mb = float(m.group(6))
        base = mp4.split("/")[-1]
        dm = re.match(r"(\d{4}-\d{2}-\d{2})[ _]*(.*?)(?:\.mp4)?$", base)
        date = dm.group(1) if dm else ""
        title = (dm.group(2) if dm else base).strip()
        rows.append(
            dict(idx=idx, subdir=subdir, mp4=mp4, date=date, title=title,
                 dur_s=dur, res=res, mb=mb)
        )
    return rows


# ------------------------------------------------- 2. 匹配拆解目录（record/frames）
def build_detail_map():
    """date(YYYYMMDD) -> [{dir, record, frames_count}]；同时收集日期区间目录。"""
    exact, ranged = {}, []
    for cat in sorted(p for p in DETAIL_ROOT.iterdir() if p.is_dir() and p.name[0].isdigit()):
        for d in sorted(p for p in cat.iterdir() if p.is_dir()):
            name = d.name
            rec = d / "record.md"
            fdir = d / "frames"
            info = dict(
                category=cat.name,
                dir=str(d.relative_to(ROOT)).replace("\\", "/"),
                record=str(rec.relative_to(ROOT)).replace("\\", "/") if rec.exists() else None,
                frames=sorted(p.name for p in fdir.glob("*")) if fdir.is_dir() else [],
            )
            # 目录名有三种形态：
            #   YYYYMMDD_标题                单日
            #   YYYYMMDD-YYYYMMDD_标题       区间（完整）
            #   YYYYMMDD-MMDD_标题           区间（第二段仅月日）—— 早期写法，易漏
            m = re.match(r"^(\d{8})(?:-(\d{2,8}))?_", name)
            if not m:
                continue
            start = m.group(1)
            end = m.group(2)
            if end:
                if len(end) == 4:            # MMDD → 补年份
                    end = start[:4] + end
                ranged.append((start, end, info))
            else:
                exact.setdefault(start, []).append(info)
    return exact, ranged


def match_detail(date_iso: str, exact, ranged):
    if not date_iso:
        return []
    ymd = date_iso.replace("-", "")
    hits = list(exact.get(ymd, []))
    for a, b, info in ranged:
        if a <= ymd <= b:
            hits.append(info)
    return hits


# ------------------------------------------------------- 3. 校验 parameters.toml
SRC_TOKEN = re.compile(r"v(\d{1,2})(?:@(\d{2}):(\d{2}))?")


def main():
    index_rows = parse_scan(SCAN_MD.read_text(encoding="utf-8"))
    exact, ranged = build_detail_map()

    by_v = {}
    for r in index_rows:
        v = r["idx"] - V_OFFSET
        if v < 1:
            continue
        entry = dict(
            v=v,
            list_index=r["idx"],
            date=r["date"],
            title=r["title"],
            mp4=r["mp4"],
            duration_s=r["dur_s"],
            resolution=r["res"],
            size_mb=r["mb"],
            details=match_detail(r["date"], exact, ranged),
        )
        by_v[v] = entry

    w("=" * 78)
    w("R1.1 溯源索引构建报告")
    w("=" * 78)
    w(f"清单期数            : {len(index_rows)}")
    w(f"可编号（vNN 1..{max(by_v) if by_v else 0}）: {len(by_v)}")
    w(f"拆解目录（精确日期）: {sum(len(v) for v in exact.values())}")
    w(f"拆解目录（日期区间）: {len(ranged)}")
    miss = [v for v, e in by_v.items() if not e["details"]]
    w(f"未匹配到拆解目录    : {len(miss)} → {miss}")
    w("")

    # ---- 校验 parameters.toml 出处
    params = tomllib.loads(PARAMS.read_text(encoding="utf-8"))
    rules = params.get("rule", [])
    w("-" * 78)
    w(f"parameters.toml 出处校验（{len(rules)} 条规则）")
    w("-" * 78)

    bad, warn, ok = [], [], 0
    for r in rules:
        src = str(r.get("source", ""))
        toks = SRC_TOKEN.findall(src)
        if not toks:
            bad.append((r["id"], src, "无法解析出任何 vNN"))
            continue
        problems = []
        resolved = []
        for v_s, mm, ss in toks:
            v = int(v_s)
            e = by_v.get(v)
            if e is None:
                problems.append(f"v{v:02d} 不在可编号范围 1..76")
                continue
            resolved.append(f"v{v:02d}={e['date']}《{e['title'][:16]}》")
            if mm and ss:
                t = int(mm) * 60 + int(ss)
                if t > e["duration_s"]:
                    problems.append(
                        f"v{v:02d}@{mm}:{ss} 超出时长 {e['duration_s']}s"
                    )
            else:
                warn.append((r["id"], f"v{v:02d} 无时间戳（仅期号）"))
        if problems:
            bad.append((r["id"], src, "; ".join(problems)))
        else:
            ok += 1
            w(f"  OK #{r['id']:<3} {r['name'][:30]:<32} {src}")
            for line in resolved:
                w(f"        └ {line}")

    w("")
    w("-" * 78)
    w(f"校验结果：OK {ok} / WARN {len(warn)} / BAD {len(bad)}")
    if warn:
        w("WARN（无时间戳，可解析到期号但不可定位帧）：")
        for i, msg in warn:
            w(f"  #{i}: {msg}")
    if bad:
        w("BAD：")
        for i, src, msg in bad:
            w(f"  #{i} src={src} → {msg}")
    w("-" * 78)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(
            dict(
                scheme="vNN = library_scan.md 逐期清单序号 - 6",
                scheme_verified_by=[
                    "v51->2026-07-12 主力洗盘三招",
                    "v70->2026-08-16 仙人指路选漂战法",
                    "v14->2026-05-17 散户专属做T秘籍",
                    "v15->2026-05-20 仓位管理技巧",
                    "v57->2026-07-24 成交额分析法",
                    "v07->2026-04-28 涨停回踩低吸",
                ],
                offset=V_OFFSET,
                videos=by_v,
            ),
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    OUT_REPORT.write_text("\n".join(buf), encoding="utf-8")

    print(f"OK videos={len(by_v)} ok={ok} warn={len(warn)} bad={len(bad)}")
    print(f"json  : {OUT_JSON} ({OUT_JSON.stat().st_size} B)")
    print(f"report: {OUT_REPORT} ({OUT_REPORT.stat().st_size} B)")


if __name__ == "__main__":
    main()
