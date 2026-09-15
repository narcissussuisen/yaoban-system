# -*- coding: utf-8 -*-
"""R2.3 · 资金流（大单净额，**自建口径**来自 TDX 逐笔成交）。

## 为什么自建而不是用东财
实测（2026-09-12，**沙箱外**）：东财资金流三个端点（`fflow/daykline` / 板块 `clist&fid=f62` / `ulist`）
**全部 `RemoteDisconnected`** —— 与项目既有记录一致（`vendor_astock_skill.md` L2204「push2 对大陆住宅 IP 间歇封锁 #18」）。
而 **TDX 逐笔可用**（`59.36.5.11:7709`，`get_history_transaction_data` 返回 `time/price/vol/num/buyorsell`）。
→ 自建口径的好处：**完全可审计**（东财口径是黑盒），符合本项目「口径必须显式」的要求。

## 口径（用户 2026-09-12 拍板：按成交额分层）
单笔成交额 = `price × vol × 100`（vol 单位=手）：
- 超大单 ≥ 100 万
- 大单 20~100 万
- 中单 4~20 万
- 小单 < 4 万
**主力净额 = （超大单买 − 超大单卖）+（大单买 − 大单卖）**；同时输出中/小单净额与总主动买卖净额。
方向由 `buyorsell` 给。

⚠️ **标 `calibrated=false`**：这组阈值来自**行业惯例**，**不是**从选手证据标定的。
   人格 SOP 只给了「**钱往哪流**」的定性，没给金额阈值 → 不假装已标定（同 M1/M2/M3、`deep_drop_count`）。

## 覆盖（用户拍板：候选池 ≤50 只/日）
候选池并集复用 `scripts/minute_incremental_snapshot.py::candidate_union`（同一口径，单一事实源）。

## 用法
    python scripts/fund_flow_from_ticks.py --date 2026-09-11 --dry-run
    python scripts/fund_flow_from_ticks.py --date 2026-09-11
    python scripts/fund_flow_from_ticks.py --codes 600519,000001 --date 2026-09-11
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import pathlib
import sys
import time

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(BASE))

from data.minute import market_of, PYTDX_SERVERS  # noqa: E402

OUT_DIR = BASE / "data" / "fund_flow"
SUPER = 1_000_000.0
BIG = 200_000.0
MID = 40_000.0
PAGE = 2000


def _connect():
    from pytdx.hq import TdxHq_API
    api = TdxHq_API(heartbeat=False)
    for h, p in PYTDX_SERVERS:
        try:
            if api.connect(h, p, time_out=8) and api.get_security_count(0) > 0:
                return api, f"{h}:{p}"
            api.disconnect()
        except Exception:
            continue
    return None, None


def fetch_ticks(api, code: str, day: str):
    """当日全部逐笔（分页）。返回 (rows, pages)。"""
    mk = market_of(code)
    d = int(day.replace("-", ""))
    rows, start, pages = [], 0, 0
    while pages < 30:
        try:
            r = api.get_history_transaction_data(mk, code, start, PAGE, d)
        except Exception:
            break
        pages += 1
        if not r:
            break
        rows.extend(r)
        if len(r) < PAGE:
            break
        start += len(r)
    return rows, pages


def classify(rows):
    """按单笔成交额分层聚合。返回净额字典。"""
    net = collections.Counter()
    n = len(rows)
    for r in rows:
        try:
            amt = float(r["price"]) * float(r["vol"]) * 100.0
        except Exception:
            continue
        bs = r.get("buyorsell")
        sign = 1.0 if bs in (0, "0") else (-1.0 if bs in (1, "1") else 0.0)
        if sign == 0.0:
            continue
        if amt >= SUPER:
            k = "super"
        elif amt >= BIG:
            k = "big"
        elif amt >= MID:
            k = "mid"
        else:
            k = "small"
        net[k] += sign * amt
        net["all"] += sign * amt
    net["main"] = net["super"] + net["big"]
    return net, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--codes", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    day = args.date or (_dt.date.today() - _dt.timedelta(days=1)).isoformat()
    t0 = time.time()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        src = "--codes"
    else:
        from minute_incremental_snapshot import candidate_union
        codes, diag = candidate_union(day)
        src = f"候选池并集({diag['files']} files / {diag['union']} 只)"
    print(f"[资金流] {day}  来源={src}  标的 {len(codes)} 只", flush=True)
    if not codes:
        print("[跳过] 当日无候选池产物（非交易日或产物缺失）")
        return 0
    if args.dry_run:
        print(f"--dry-run：将取 {len(codes)} 只的当日逐笔（未落盘）")
        return 0

    api, host = _connect()
    if api is None:
        print("FAIL: TDX 9 节点全不可用")
        return 1
    print(f"  [source] pytdx @ {host}", flush=True)

    recs, fails = [], []
    for i, code in enumerate(codes, 1):
        try:
            rows, pages = fetch_ticks(api, code, day)
            if not rows:
                fails.append(code)
                continue
            net, n = classify(rows)
            recs.append(dict(code=code, ticks=n, pages=pages,
                             net_main=round(net["main"]), net_super=round(net["super"]),
                             net_big=round(net["big"]), net_mid=round(net["mid"]),
                             net_small=round(net["small"]), net_all=round(net["all"])))
        except Exception as e:
            fails.append(code)
            if len(fails) <= 5:
                print(f"  [warn] {code}: {type(e).__name__}: {e}", flush=True)
        if i % 10 == 0:
            print(f"  ...{i}/{len(codes)}", flush=True)
    api.disconnect()

    doc = {
        "_meta": {
            "schema_version": "1",
            "date": day,
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": f"pytdx get_history_transaction_data @ {host}（TDX 逐笔成交）",
            "why_self_built": "东财资金流端点在本机全部 RemoteDisconnected（沙箱外实测）"
                              "→ 改用 TDX 逐笔自建，口径显式可审计",
            "thresholds_yuan": {"super": SUPER, "big": BIG, "mid": MID},
            "definition": "单笔成交额 = price×vol×100；主力净额 = (超大单买−超大单卖)+(大单买−大单卖)；"
                          "方向取 buyorsell",
            "calibrated": False,
            "calibrated_note": "⚠️ 这组金额阈值来自**行业惯例**，**不是**从选手证据标定的 —— "
                              "人格 SOP 只给「钱往哪流」的定性、未给数值。故标 calibrated=false，不假装已标定。",
            "coverage": src, "n_codes": len(codes), "n_ok": len(recs), "n_fail": len(fails),
            "failed": fails[:40],
            "generator": "scripts/fund_flow_from_ticks.py",
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "records": sorted(recs, key=lambda r: -r["net_main"]),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"fund_flow_{day}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    back = json.loads(out.read_text(encoding="utf-8"))
    assert back["_meta"]["n_ok"] == len(back["records"])

    print(f"\n{'代码':<8}{'逐笔':>7}{'主力净额(万)':>13}{'超大单':>10}{'大单':>10}")
    for r in doc["records"][:15]:
        print(f"{r['code']:<8}{r['ticks']:>7}{r['net_main']/1e4:>13.1f}"
              f"{r['net_super']/1e4:>10.1f}{r['net_big']/1e4:>10.1f}")
    print(f"\nok={len(recs)} fail={len(fails)}  主力净额>0 的 {sum(1 for r in recs if r['net_main']>0)} 只")
    print(f"selfcheck OK → {out} ({out.stat().st_size}B) elapsed={time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
