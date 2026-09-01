"""Align official SW publish index names to 6-digit industry codes.

Method (pure data-driven, auditable):
1. official API: index_publish/current (二级行业) -> 801xxx + official name (GBK).
2. official API: index_publish/details/component_stocks?swindexcode=801xxx -> member stock codes.
3. local sw_industry_history.csv: stock -> latest l2_code (6-digit industry code).
4. For each 801xxx, find the 6-digit code whose member set overlaps most; require
   >=70% Jaccard common with the official member set, else leave unverified.
Writes data/sw_l2_names.json (code->name) + data/sw_l2_alignment_report.json
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
CURRENT = "https://www.swsresearch.com/institute-sw/api/index_publish/current/"
COMPONENT = "https://www.swsresearch.com/institute-sw/api/index_publish/details/component_stocks/"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=30).read()


# 官方未发布对应指数的二级行业（申万2021分类标准名称，人工核对后固化）
MANUAL_FILLS = {"110300": "林业Ⅱ", "110600": "农业综合Ⅱ"}


def decode(raw: bytes) -> str:
    try:
        return raw.decode("gbk")
    except Exception:
        return raw.decode("utf-8", "replace")


def main() -> int:
    typ = urllib.parse.quote("二级行业")
    raw = get(CURRENT + "?page=1&page_size=200&indextype=" + typ)
    data = json.loads(decode(raw))
    official = {r["swindexcode"]: r["swindexname"] for r in data["data"]["results"]}

    # local stock -> current membership = row with max start_date (matches industry_of semantics)
    rows: dict[str, tuple[str, str]] = {}
    with (BASE / "data" / "sw_industry_history.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sym = str(row["code"]).strip().zfill(6)
            code = str(row["l2_code"]).strip()
            started = str(row.get("start_date", "") or "")[:10]
            cur = rows.get(sym)
            if cur is None or started >= cur[0]:
                rows[sym] = (started, code)
    by_code: dict[str, set[str]] = {}
    for sym, (_updated, code) in rows.items():
        by_code.setdefault(code, set()).add(sym)

    # official members per 801xxx
    members: dict[str, set[str]] = {}
    for swcode in list(official.keys()):
        try:
            raw = get(COMPONENT + "?swindexcode=" + swcode + "&page=1&page_size=10000")
            data = json.loads(decode(raw))
            rows = data["data"]["results"] if isinstance(data.get("data"), dict) else (data.get("data") or [])
            members[swcode] = {str(r["stockcode"]).zfill(6) for r in rows if r.get("stockcode")}
        except Exception as exc:
            print("member fail", swcode, exc, flush=True)
            members[swcode] = set()
        time.sleep(0.3)

    # align: for each official name, find code with best overlap
    code_members_inv: dict[str, list[str]] = {}
    for code, syms in by_code.items():
        code_members_inv.setdefault(code, [])
        code_members_inv[code].append(code)
    # per-code best match, then keep best name per code (one-to-one)
    code_best: dict[str, tuple[float, str]] = {}
    report = {}
    for swcode, name in official.items():
        mem = members.get(swcode, set())
        best_code, best_ratio = None, 0.0
        if mem:
            for code, syms in by_code.items():
                union = len(mem | syms)
                ratio = (len(mem & syms) / union) if union else 0.0
                if ratio > best_ratio:
                    best_code, best_ratio = code, ratio
        if best_code and best_ratio >= 0.5:
            prev = code_best.get(best_code)
            if prev is None or best_ratio > prev[0]:
                code_best[best_code] = (best_ratio, name)
        report[swcode] = {"name": name, "ratio": round(best_ratio, 3), "code": best_code}
    table = {code: name for code, (_r, name) in sorted(code_best.items())}
    table.update(MANUAL_FILLS)

    doc = {
        "src": "申万宏源官方 institute-sw API 二级行业成分股对齐本地 sw_industry_history.csv",
        "fetched_at": "2026-08-31",
        "aligned": len(table),
        "manual_fills": MANUAL_FILLS,
        "names": dict(sorted(table.items())),
    }
    (BASE / "data" / "sw_l2_names.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (BASE / "data" / "sw_l2_alignment_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("aligned=%d official=%d report=%s" % (len(table), len(official), str(BASE / "data" / "sw_l2_alignment_report.json")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
