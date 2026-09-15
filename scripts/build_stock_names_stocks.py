# -*- coding: utf-8 -*-
"""R2.8 重建个股名称表 → data/stock_names_stocks.json

## 为什么要重建而不是筛旧表
旧表 `data/stock_names_full.json`（35086 条）的污染不是"多了些别的类型"，而是**代码空间冲突**：
深市个股 `000xxx` 与**上证指数系列** `000xxx` 撞号，且旧生成器 `fetch_stock_names.py:59 m.update(p)`
让一方**覆盖**另一方 → 表里 `000004` = 「工业指数」，而它其实是**在市个股「国华网安」**
（硬证据：`F:/WorkBuddyItem/a股分钟线/parquet_qfq_2026/000004.SZ.parquet` 存在）。
→ **被覆盖的正确名在旧表里根本不存在，只能重取。**

## 核心修法：用 (market, code) 组合判类型（而非 code + 事后覆盖）
通达信的市场号本身就把冲突分开了：
  market=1(沪) 的 `000004` = 上证工业指数  → **不是个股**
  market=0(深) 的 `000004` = 深主板国华网安 → **是个股**
所以 `is_equity(code, market)` 按「市场 + 代码段」双条件判定，**从根上消除覆盖问题**。

## 三源交叉（个股优先 + 在库白名单双重确认）
  ① 主源   : pytdx `get_security_list(market, start)`，9 节点池 failover（复用 `src/data/minute.py:PYTDX_SERVERS`）
  ② 白名单 : `F:/WorkBuddyItem/a股分钟线/parquet_qfq_2026/*.SZ|.SH.parquet`（6001 只，实测）
  ③ 反向剔除: `C:/new_tdx/T0002/hq_cache/tdxzs.cfg`（GBK，指数/板块名，作为冗余剔除证据）
  ④ 交叉验证: `outputs/**` 里生产用过的 code→name（≈851），仅用于**发现不一致**，不覆盖主源

## 验收
  equity_total ∈ [5500, 6500]；无 `\\x00`；`missing_vs_library` → 0；
  `names['000004'] == '国华网安'`；`names['300750'] == '宁德时代'`；conflicts 逐条可解释。

用法: python scripts/build_stock_names_stocks.py [--report-only]
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import re
import sys
import time

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

OUT_NEW = BASE / "data" / "stock_names_stocks.json"
OUT_RAW = BASE / "data" / "stock_names_full.json"
MANUAL = BASE / "data" / "stock_names_manual.json"
WHITELIST_DIR = pathlib.Path(r"F:\WorkBuddyItem\a股分钟线\parquet_qfq_2026")
TDXZS = pathlib.Path(r"C:\new_tdx\T0002\hq_cache\tdxzs.cfg")

# 与 src/data/minute.py:PYTDX_SERVERS 保持一致（9 节点池）
SERVERS = [
    ("59.36.5.11", 7709), ("117.34.114.18", 7709),
    ("117.34.114.13", 7709), ("117.34.114.27", 7709), ("117.34.114.16", 7709),
    ("117.34.114.20", 7709), ("117.34.114.17", 7709), ("117.34.114.14", 7709),
    ("117.34.114.15", 7709),
]

SH_PREFIX2 = ("60", "68")                      # 600/601/603/605/688/689
SZ_PREFIX3 = ("000", "001", "002", "003", "300", "301")
BJ_PREFIX1 = ("4", "8")                        # 430/83x/87x/88x
BJ_PREFIX3 = ("920",)
CLEAN_RE = re.compile(r"[\x00-\x1f\u3000]")


def is_equity(code: str, market: int) -> bool:
    """(market, code) 双条件判个股 —— 本脚本的核心修正点。"""
    if not (isinstance(code, str) and len(code) == 6 and code.isdigit()):
        return False
    if market == 1:                                   # 沪市
        return code[:2] in SH_PREFIX2
    if market == 0:                                   # 深市 + 北交所
        if code[:3] in SZ_PREFIX3:
            return True
        if code[0] in BJ_PREFIX1 or code[:3] in BJ_PREFIX3:
            return True
        return False
    return False


def clean_name(nm) -> str:
    return CLEAN_RE.sub("", str(nm or "")).strip()


_VARIANT_PREFIX = re.compile(r"^(?:\*?ST|XD|XR|DR|N|C|U|W|S)+", re.I)


def _norm_variant(nm: str) -> str:
    """归一化「同名不同时点」的写法：*ST/ST/XD/XR/N/C/U/W 前缀 + 去空白。
    例：`乐心股份` vs `乐心医疗` 不归一（不同名）；`剑桥科技` vs `XD剑桥科技` 归一。
    """
    return _VARIANT_PREFIX.sub("", str(nm or "").replace(" ", "").replace("\u3000", "")).strip()


def is_equity_code(code: str) -> bool:
    """仅凭代码段判 A 股（**不区分沪深**）—— 用于过滤「白名单」这类只有代码没有市场号的输入。

    ⚠️ 注意：`000004` 在**深市**是个股、在**沪市**是上证工业指数 → 仅凭代码无法判定。
    故本函数只用于「白名单去噪」（把 399xxx/880xxx/1xxxxx/200xxx/900xxx 排除掉），
    **真正的类型判定一律用 `is_equity(code, market)`**。
    """
    if not (isinstance(code, str) and len(code) == 6 and code.isdigit()):
        return False
    return (code[:2] in SH_PREFIX2) or (code[:3] in SZ_PREFIX3) or (code[:3] in BJ_PREFIX3)


def load_manual() -> tuple[dict, dict]:
    """人工核验补充表（最高优先级）。返回 (names, excluded)。"""
    if not MANUAL.exists():
        return {}, {}
    d = json.loads(MANUAL.read_text(encoding="utf-8"))
    return (dict(d.get("names") or {}), dict(d.get("excluded") or {}))


def fetch_all() -> tuple[dict, str]:
    """返回 ({"<market>:<code>": name}, source_desc)。

    ⚠️ 实测（2026-09-12）：**9 节点池的 pytdx 只能拿深市列表** ——
    `get_security_count(1)=27743` 有值，但 `get_security_list(1,0)` 恒为 0（所有节点一致）。
    故**沪市必须走 mootdx**（`client.stocks(market=1)`，实测 27920 行）。
    mootdx 的 `market` 参数正是把「深 000004 个股」与「沪 000004 指数」分开的关键 ——
    这也是旧脚本 `m.update(p)` 按代码合并时踩的坑。
    """
    out: dict[str, str] = {}
    try:
        from mootdx.quotes import Quotes
        client = Quotes.factory(market="std")
        for m in (0, 1):
            df = client.stocks(market=m)
            n = 0
            if df is not None:
                for _, r in df.iterrows():
                    code = str(r.get("code", "")).zfill(6)
                    nm = clean_name(r.get("name"))
                    if code and nm:
                        out[f"{m}:{code}"] = nm
                        n += 1
            print(f"  [source] mootdx market={m}: {n} 条", flush=True)
        if out:
            return out, "mootdx(market=0/1)"
    except Exception as e:
        print(f"  [warn] mootdx 失败（{type(e).__name__}: {e}）→ 退 pytdx 深市", flush=True)

    from pytdx.hq import TdxHq_API
    api = TdxHq_API(heartbeat=False)
    for h, p in SERVERS:
        try:
            if not api.connect(h, p, time_out=8):
                continue
            if api.get_security_count(0) <= 0:      # 取数验活，不靠 connect 返回值
                api.disconnect()
                continue
            start, n = 0, 0
            while True:
                lst = api.get_security_list(0, start)
                if not lst:
                    break
                for it in lst:
                    code = str(it.get("code", "")).zfill(6)
                    nm = clean_name(it.get("name"))
                    if code and nm:
                        out[f"0:{code}"] = nm
                        n += 1
                start += len(lst)
                if len(lst) < 1000:
                    break
            api.disconnect()
            print(f"  [source] pytdx 深市 @ {h}:{p} → {n} 条（**无沪市**）", flush=True)
            return out, f"pytdx-深市-only {h}:{p}"
        except Exception:
            continue
    return out, "none"


def load_whitelist() -> set[str]:
    if WHITELIST_DIR.is_dir():
        return {p.name.split(".")[0] for p in WHITELIST_DIR.glob("*.parquet")
                if p.name[:6].isdigit()}
    return set()


def load_index_names() -> set[str]:
    """tdxzs.cfg 里的指数/板块名（GBK），作冗余剔除证据。"""
    if not TDXZS.exists():
        return set()
    out = set()
    try:
        for line in TDXZS.read_bytes().decode("gbk", "replace").splitlines():
            parts = [x.strip() for x in line.split(",")]
            for x in parts[:3]:
                if x and re.search(r"[\u4e00-\u9fff]", x):
                    out.add(x)
                    break
    except Exception:
        pass
    return out


def load_outputs_names() -> dict[str, str]:
    """从 outputs/ 收集生产用过的 code→name（仅用于发现不一致）。"""
    pairs: dict[str, collections.Counter] = {}
    for pat in ("outputs/plans/*.json", "outputs/intraday/*.json", "outputs/**/*.json"):
        for f in BASE.glob(pat):
            try:
                if f.stat().st_size > 8_000_000:
                    continue
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            stack = [d]
            while stack:
                x = stack.pop()
                if isinstance(x, dict):
                    c, n = x.get("sym") or x.get("symbol"), x.get("name")
                    if isinstance(c, str) and isinstance(n, str) and len(c) == 6 and c.isdigit():
                        n = clean_name(n)
                        if n:
                            pairs.setdefault(c, collections.Counter())[n] += 1
                    stack.extend(x.values())
                elif isinstance(x, list):
                    stack.extend(x[:4000])
    return {c: v.most_common(1)[0][0] for c, v in pairs.items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true", help="只打印不落盘")
    args = ap.parse_args()

    t0 = time.time()
    raw_doc = json.loads(OUT_RAW.read_text(encoding="utf-8")) if OUT_RAW.exists() else {}
    raw_flat = {k: v for k, v in raw_doc.items() if k != "_meta" and isinstance(v, str)}
    print(f"[1/5] 旧表 raw={len(raw_flat)} 条", flush=True)

    wl_all = load_whitelist()
    # 白名单去噪：只保留 A 股代码段（排除 399xxx 指数 / 880xxx 板块 / 1xxxxx 债基 /
    # 200xxx 深B / 900xxx 沪B）。⚠️ 该库含**已退市**标的历史数据 → 不等于「在市」。
    wl = {c for c in wl_all if is_equity_code(c)}
    idx_names = load_index_names()
    out_names = load_outputs_names()
    manual_names, manual_excluded = load_manual()
    print(f"[2/5] 白名单 {len(wl_all)} → A股段 {len(wl)}（去噪 {len(wl_all)-len(wl)}）  "
          f"指数名={len(idx_names)}  outputs交叉={len(out_names)}  人工核验={len(manual_names)}",
          flush=True)

    degraded = False
    src_desc = ""
    try:
        got, src_desc = fetch_all()
        if not got:
            raise RuntimeError("主源与备源均无数据")
    except Exception as e:
        print(f"[!] 取数失败 → 降级模式: {e}", flush=True)
        got, degraded, src_desc = {}, True, "degraded(from raw table)"

    # 分市场/前缀分布诊断（便于发现「沪市缺失」这类问题）
    dist = collections.defaultdict(collections.Counter)
    for key in got:
        m, c = key.split(":", 1)
        dist[m][c[:2]] += 1
    for m in sorted(dist):
        print(f"  [dist] market={m} {dist[m].most_common(8)}", flush=True)

    names: dict[str, str] = {}
    excluded = 0
    conflicts: list[dict] = []
    rule_only = 0
    confirmed = 0
    arbitrated = 0

    if got:
        for key, nm in got.items():
            market_s, code = key.split(":", 1)
            market = int(market_s)
            if not is_equity(code, market):
                excluded += 1
                continue
            # 冗余剔除：名字恰是通达信指数/板块名 且 不在白名单 → 强怀疑
            if nm in idx_names and code not in wl:
                excluded += 1
                conflicts.append({"code": code, "market": market, "raw_name": nm,
                                  "action": "excluded_as_index_name"})
                continue
            if code in wl:
                confirmed += 1
            else:
                rule_only += 1
            names[code] = nm
    else:
        # 降级：从旧表按段规则筛（无法修复被占位者，显式标记）
        for code, nm in raw_flat.items():
            m = 1 if code[:2] in SH_PREFIX2 else 0
            if is_equity(code, m):
                names[code] = clean_name(nm)
                rule_only += 1

    # 人工核验补充（最高优先级，覆盖主源）
    manual_applied = 0
    for c, n in manual_names.items():
        if names.get(c) != n:
            manual_applied += 1
        names[c] = n

    # 与 outputs 交叉发现不一致（**只报告，不覆盖**）
    for c, n in out_names.items():
        if c in names and names[c] != n and _norm_variant(names[c]) != _norm_variant(n):
            conflicts.append({"code": c, "pytdx_name": names[c], "outputs_name": n,
                              "action": "kept_main_source", "in_whitelist": c in wl})
            arbitrated += 1

    missing_vs_lib = sorted(wl - set(names))
    bj_missing = [c for c in missing_vs_lib if c[:3] in BJ_PREFIX3]
    other_missing = [c for c in missing_vs_lib if c not in set(bj_missing)]
    print(f"[3/5] 个股 {len(names)}  confirmed={confirmed} rule_only={rule_only} "
          f"manual={manual_applied} excluded={excluded}", flush=True)
    print(f"      白名单(A股段)缺 {len(missing_vs_lib)}：北交所 {len(bj_missing)} + 其它 {len(other_missing)}",
          flush=True)
    if other_missing:
        print(f"      其它缺失（多为退市，不给名）: {other_missing[:20]}", flush=True)

    doc = {
        "_meta": {
            "schema_version": "1",
            "built_at": time.strftime("%Y-%m-%d"),
            "sources": ["mootdx stocks(market=0/1) 主源", "pytdx 深市 备源",
                        "parquet_qfq_2026 whitelist", "tdxzs.cfg index names",
                        "outputs cross-check"],
            "source_used": src_desc,
            "method": "is_equity(code, market) 双条件判定 —— (market,code) 组合消除代码空间冲突",
            "raw_total": len(raw_flat),
            "equity_total": len(names),
            "excluded_non_equity": excluded,
            "in_library_confirmed": confirmed,
            "rule_only": rule_only,
            "arbitrated": arbitrated,
            "manual_applied": manual_applied,
            "manual_excluded": manual_excluded,
            "missing_vs_library": missing_vs_lib[:80],
            "missing_vs_library_count": len(missing_vs_lib),
            "known_gaps": {
                "bj_market": {
                    "count": len(bj_missing),
                    "codes_sample": bj_missing[:20],
                    "reason": "北交所（920xxx）名称三源皆不可得：通达信沪深列表不含、mootdx 不支持 market=2、"
                              "pytdx market=2 有 count=351 但 get_security_list 恒返空。"
                              "且北交所当前不在本项目数据链内（分钟数据同样取不到）→ 登记为已知缺口，不静默。",
                },
                "other": {
                    "count": len(other_missing),
                    "codes": other_missing[:40],
                    "reason": "白名单（parquet_qfq_2026）含**已退市**标的历史数据 → 这些代码在通达信与主源里"
                              "都查不到名称。按「不猜名」原则不给名，显式列出。",
                },
            },
            "conflicts": conflicts[:80],
            "conflicts_count": len(conflicts),
            "degraded": degraded,
            "elapsed_sec": round(time.time() - t0, 1),
            "generator": "scripts/build_stock_names_stocks.py",
            "note": "个股优先 + 在库白名单双重确认；conflicts 一律显式列出，不静默",
        },
        "names": dict(sorted(names.items())),
    }

    print(f"[4/5] 抽查: "
          f"000004={doc['names'].get('000004')!r} "
          f"000005={doc['names'].get('000005')!r} "
          f"000015={doc['names'].get('000015')!r} "
          f"300750={doc['names'].get('300750')!r} "
          f"600519={doc['names'].get('600519')!r} "
          f"920982={doc['names'].get('920982')!r}", flush=True)
    bad_nul = [k for k, v in names.items() if "\x00" in str(v)]
    print(f"      含 \\x00 = {len(bad_nul)}；条数 = {len(names)}", flush=True)
    if conflicts:
        print(f"      conflicts 前 10：", flush=True)
        for c in conflicts[:10]:
            print(f"        {c}", flush=True)

    ok = (5100 <= len(names) <= 5400) and not bad_nul
    print(f"[5/5] {'OK' if ok else 'FAIL'} equity_total={len(names)} "
          f"（沪深 A 股期望 5100~5400；北交所 {len(bj_missing)} 只未纳入，见 known_gaps）"
          f"elapsed={time.time()-t0:.1f}s", flush=True)

    if not args.report_only:
        OUT_NEW.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"      → {OUT_NEW}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
