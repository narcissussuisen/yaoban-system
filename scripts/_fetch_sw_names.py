"""SW official level-2 name alignment. See module docstring in repo."""
from __future__ import annotations
import json
import pathlib
import urllib.parse
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent
URL_BASE = "https://www.swsresearch.com/institute-sw/api/index_publish/current/"

def fetch_official() -> dict:
    typ = urllib.parse.quote("二级行业")
    url = URL_BASE + "?page=1&page_size=200&indextype=" + typ
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=30).read()
    try:
        text = raw.decode("gbk")
    except Exception:
        text = raw.decode("utf-8", "replace")
    data = json.loads(text)
    return {r["swindexcode"]: r["swindexname"] for r in data["data"]["results"]}

def main() -> int:
    pairs = fetch_official()
    cache = BASE / "data" / "sw_publish_names.json"
    cache.write_text(json.dumps({
        "fetched_at": "2026-08-31",
        "source": "swsresearch.com institute-sw index_publish current",
        "indextype": "二级行业",
        "names": pairs,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    path = BASE / "data" / "sw_l2_names.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    names = doc["names"]
    official_names = set(pairs.values())
    aligned = {k: v for k, v in names.items() if v in official_names}
    unaligned = {k: v for k, v in names.items() if v not in official_names}
    reverse = {}
    for code, name in pairs.items():
        reverse.setdefault(name, code)
    fixed = {k: v for k, v in names.items()}
    doc["names"] = fixed
    doc["official_aligned_count"] = len(aligned)
    doc["officially_unverified_codes"] = sorted(unaligned.keys())
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print("official=%d table=%d aligned=%d unaligned=%d" % (len(pairs), len(fixed), len(aligned), len(unaligned)))
    print("unaligned:", json.dumps(unaligned, ensure_ascii=False)[:1200])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
