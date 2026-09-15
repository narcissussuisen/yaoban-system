# -*- coding: utf-8 -*-
"""R2.7 重建 data/sw_l2_names.json —— 补齐 6 位行业码的名称，并让缺口**不再静默**。

背景（2026-09-12 R2.7）
--------------------
`test_dashboard_names::test_sw_name_table_covers_board_codes` 长期红：`board_momentum.json` 里的
`750100` 不在名称表内 → 看板该板块名静默显示为空串。

根因（本轮查清，非"漏了几个码"）：
1. 原 `scripts/_align_sw_names.py` 用**申万官方已发布二级行业**（当时 124 个）对齐，
   而官方现在有 **131 个**（新增 7 个：林业Ⅱ/农业综合Ⅱ/其他家电Ⅱ/旅游零售Ⅱ/体育Ⅱ/**油气开采Ⅱ**/医疗美容）
   → 缺的 `750100` 正是新增的 **801961 油气开采Ⅱ**。
2. CSV 的 6 位 L2 码共 162 个（按"每股最新归属"口径），其中大部分未映射码的成员股
   **已退市/不在在市股表** → 属**历史残留**，不是当前有效行业。

修法：
- 用官方 131 个二级行业重跑一对一最优重叠对齐（Jaccard ≥ 0.5，同原方法，可审计）
- 对仍未映射的码做**在市成员占比**判定：`historical`（<30% 在市）／`unresolved`（否则）
- 产出 `sw_l2_names.json`（保持向后兼容字段）+ `sw_l2_coverage_report.json`（缺口显式列出）

⚠️ 网络：东财/新浪在本机被阻断，但**申万研究站点可达**（本脚本只依赖它）。
"""

from __future__ import annotations

import csv
import json
import pathlib
import time
import urllib.parse
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0"}
COMPONENT = ("https://www.swsresearch.com/institute-sw/api/index_publish/"
             "details/component_stocks/")
RATIO_MIN = 0.5          # 与原脚本一致
LIVE_MIN = 0.30          # 在市成员占比 < 此值 → 判为历史残留


def get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()


def decode(raw: bytes) -> str:
    try:
        return raw.decode("gbk")
    except Exception:
        return raw.decode("utf-8", "replace")


def official_second() -> dict:
    """官方申万二级：801xxx -> name。优先用 akshare（已验证可达），失败回落到官网 API。"""
    try:
        import akshare as ak
        df = ak.sw_index_second_info()
        out = {}
        for _, r in df.iterrows():
            c = str(r["行业代码"]).replace(".SI", "").strip()
            if c:
                out[c] = str(r["行业名称"]).strip()
        if out:
            return out
    except Exception as e:
        print(f"[warn] akshare 取申万二级失败，回落官网 API：{type(e).__name__}: {e}", flush=True)
    typ = urllib.parse.quote("二级行业")
    d = json.loads(decode(get(
        "https://www.swsresearch.com/institute-sw/api/index_publish/current/"
        f"?page=1&page_size=300&indextype={typ}")))
    return {r["swindexcode"]: r["swindexname"] for r in d["data"]["results"]}


def main() -> int:
    official = official_second()
    print(f"official 二级行业 = {len(official)}", flush=True)

    # 股票 -> 最新 6 位 L2 码（同原脚本语义：取 start_date 最大的那行）
    latest: dict[str, tuple[str, str]] = {}
    with (BASE / "data/sw_industry_history.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sym = str(row["code"]).strip().zfill(6)
            code = str(row["l2_code"]).strip()
            if not code:
                continue
            started = str(row.get("start_date", "") or "")[:10]
            cur = latest.get(sym)
            if cur is None or started >= cur[0]:
                latest[sym] = (started, code)
    by_code: dict[str, set[str]] = {}
    for sym, (_s, code) in latest.items():
        by_code.setdefault(code, set()).add(sym)
    print(f"6 位 L2 码（每股最新归属）= {len(by_code)}", flush=True)

    # 「在市股集合」：R2.8 起改用只含个股的新表（两段式 → 取 .names，去掉 _meta）
    _ndoc = json.loads(
        (BASE / "data/stock_names_stocks.json").read_text(encoding="utf-8"))
    live = set((_ndoc.get("names", _ndoc) or {}).keys()) - {"_meta"}

    # 官方成员集
    members: dict[str, set[str]] = {}
    for i, sw in enumerate(sorted(official), 1):
        try:
            d = json.loads(decode(get(
                COMPONENT + f"?swindexcode={sw}&page=1&page_size=10000")))
            data = d.get("data")
            rows = data["results"] if isinstance(data, dict) else (data or [])
            members[sw] = {str(r["stockcode"]).zfill(6) for r in rows if r.get("stockcode")}
        except Exception as e:
            print(f"  member fail {sw}: {e}", flush=True)
            members[sw] = set()
        if i % 30 == 0:
            print(f"  members {i}/{len(official)}", flush=True)
        time.sleep(0.25)

    # 一对一最优重叠
    code_best: dict[str, tuple[float, str, str]] = {}
    report = {}
    for sw, name in official.items():
        mem = members.get(sw) or set()
        bc, br = None, 0.0
        for code, syms in by_code.items():
            u = len(mem | syms)
            r = (len(mem & syms) / u) if u else 0.0
            if r > br:
                bc, br = code, r
        if bc and br >= RATIO_MIN:
            prev = code_best.get(bc)
            if prev is None or br > prev[0]:
                code_best[bc] = (br, name, sw)
        report[sw] = {"name": name, "ratio": round(br, 3), "code": bc}

    table = {c: n for c, (_r, n, _s) in code_best.items()}
    prev_doc = json.loads((BASE / "data/sw_l2_names.json").read_text(encoding="utf-8"))
    manual = dict(prev_doc.get("manual_fills") or {})
    # 只保留仍然有效的 manual_fills（已被官方对齐覆盖的不再需要）
    manual = {k: v for k, v in manual.items() if k not in table}
    table.update(manual)

    # 未映射码分类
    unresolved = {}
    for code, syms in sorted(by_code.items()):
        if code in table or code in ("NA", ""):
            continue
        n_live = len(syms & live)
        cls = "historical" if (n_live / len(syms)) < LIVE_MIN else "unresolved"
        cand = None
        best = 0.0
        for sw, mem in members.items():
            if not mem:
                continue
            u = len(mem | syms)
            r = (len(mem & syms) / u) if u else 0.0
            if r > best:
                cand, best = official[sw], r
        unresolved[code] = {
            "class": cls,
            "members_total": len(syms),
            "members_live": n_live,
            "best_candidate": cand,
            "best_ratio": round(best, 3),
            "sample": sorted(syms)[:6],
        }

    doc = {
        "src": "申万宏源官方 institute-sw 二级行业成分股 × 本地 sw_industry_history.csv 一对一最优重叠对齐",
        "fetched_at": "2026-09-12",
        "official_count": len(official),
        "aligned": len(table),
        "l2_code_universe": len(by_code),
        "manual_fills": manual,
        "names": dict(sorted(table.items())),
        "unresolved": unresolved,
        "note": "unresolved.class=historical 表示该码成员股已基本退市（历史残留），非当前有效行业；"
                "class=unresolved 表示仍需人工核定",
    }
    (BASE / "data/sw_l2_names.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (BASE / "data/sw_l2_alignment_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    cov = {
        "generated_at": "2026-09-12",
        "official_second_count": len(official),
        "l2_code_universe": len(by_code),
        "mapped": len([c for c in by_code if c in table]),
        "coverage_pct": round(100.0 * len([c for c in by_code if c in table]) / max(len(by_code), 1), 1),
        "historical": sorted(c for c, v in unresolved.items() if v["class"] == "historical"),
        "unresolved": {c: v for c, v in unresolved.items() if v["class"] == "unresolved"},
        "note": "historical 无需补名（成员股已退市）；unresolved 需人工或外部源核定",
    }
    (BASE / "data/sw_l2_coverage_report.json").write_text(
        json.dumps(cov, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"aligned={len(table)} official={len(official)} "
          f"universe={len(by_code)} 覆盖={cov['coverage_pct']}%", flush=True)
    print(f"historical={len(cov['historical'])} unresolved={len(cov['unresolved'])}", flush=True)
    if cov["unresolved"]:
        for c, v in cov["unresolved"].items():
            print(f"  ⚠️ unresolved {c}:  candidate={v['best_candidate']} "
                  f"ratio={v['best_ratio']} live={v['members_live']}/{v['members_total']} "
                  f"sample={v['sample']}", flush=True)
    # 关键校验：750100 必须已命名
    print(f"750100 -> {doc['names'].get('750100')!r}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
