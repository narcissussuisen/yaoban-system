# -*- coding: utf-8 -*-
"""R2.4 · 候选池 1m 分钟快照「每日增量落盘」。

## 为什么需要
决策环第④段（找低吸）与第⑤段（稳持仓）需要**候选池的持续分钟数据**。现状：
  - `data/minute/1m/` = 970 只（**滚动窗口**，由 `tools/snapshot_minute.py` 按需攒出，无调度）
  - `yaoban.db::minute_kline` = 86 只、**停在 2026-08-27**
→ 分钟数据没有「每日累积」，导致分时级规则无法回看/复核。

## 候选池口径（2026-09-12 实测确定）
**`outputs/intraday/confirm_YYYYMMDD_HHMM.json` 的 `candidates_snapshot.pool[].sym`，
按 `candidates_ref`（sha256 前 12 位）去重后取当日并集。**

实测每日并集：09-03 **45** / 09-07 **62** / 09-08 **53** / 09-09 **53** / 09-10 **42** / 09-11 **23** 只
—— 正是计划书 §R2.4「≤50 只/日」的量级。

> 为什么不用 `outputs/plans/<date>_plan.json` 的 `picks`？那只 **≤4 只**，覆盖太窄。
> 为什么不用 `market_scan_*.json`？那是**全市场 300 行扫描**，不是候选池。

## 落盘约定（用户 2026-09-12 拍板）
- 位置：**C 盘** `data/minute/1m/<code>.parquet`（沿用现有形态）
- 形态：**按 code 覆盖写**（非每日独立文件）→ 一年去重后约 80–240MB
- 元数据：`<code>.meta.json` 记录来源与拉取时间（沿用 `tools/snapshot_minute.py` 既有约定）

## 用法
    python scripts/minute_incremental_snapshot.py --date 2026-09-11 --dry-run   # 只看名单与只数
    python scripts/minute_incremental_snapshot.py --date 2026-09-11             # 实拉并落盘
    python scripts/minute_incremental_snapshot.py --date today                  # 当日（供盘后链调用）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
import time

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

INTRADAY = BASE / "outputs" / "intraday"
# 路径统一由 src/data/minute_paths.py 解析（EVOALPHA_MINUTE_SNAPSHOT 可覆盖根目录，
# 便于用户后续把快照整体迁到 F 盘而**无需改代码**）
from data.minute_paths import snapshot_root as _snapshot_root  # noqa: E402
SNAPSHOT = _snapshot_root()
FREQ = "1m"


def candidate_union(day: str) -> tuple[list[str], dict]:
    """当日候选池并集（按 candidates_ref 去重）。返回 (syms, 诊断信息)。"""
    ymd = day.replace("-", "")
    refs: dict[str, set] = {}
    files = sorted(INTRADAY.glob(f"confirm_{ymd}_*.json"))
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        snap = d.get("candidates_snapshot") or {}
        ref = str(d.get("candidates_ref") or snap.get("date", ""))[:12]
        # ⚠️ 2026-09-14 修复（schema 兼容，**否则新 schema 下本函数会静默返回空并集**）：
        #   当日把 `scan_and_confirm` 的 confirm 快照字段由 `pool`（= 涨幅前 8）拆成了
        #     `movers`（异动池，发现层）/ `confirm_queue`（**准入层** = 战法池 ∩ 今日活跃 ∩ 板块）。
        #   本函数原先只读 `pool` ⇒ 新快照下恒为空 ⇒ DRAGON 段的「候选池」静默消失
        #   （只剩「主线板块成分」），且不报错、不留痕。
        #   优先级：`confirm_queue`（新·准入层） > `movers`（新·发现层） > `pool`（旧）。
        pool = (snap.get("confirm_queue") or d.get("confirm_queue")
                or snap.get("movers") or d.get("movers")
                or snap.get("pool") or d.get("pool") or [])
        refs.setdefault(ref, set()).update(
            str(p["sym"]) for p in pool if p.get("sym"))
    syms = sorted({s for v in refs.values() for s in v})
    diag = {"day": day, "files": len(files), "distinct_refs": len(refs),
            "union": len(syms),
            "per_ref": {k: len(v) for k, v in list(refs.items())[:0]} or None}
    return syms, diag


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", default="")
    ap.add_argument("--pause", type=float, default=0.15)
    args = ap.parse_args()

    day = args.date
    if not day or day == "today":
        day = _dt.date.today().isoformat()
    syms, diag = candidate_union(day)
    print(f"[候选池] {day}: {diag['files']} 个 confirm 文件 / "
          f"{diag['distinct_refs']} 个 distinct ref / **并集 {diag['union']} 只**", flush=True)
    if not syms:
        print("[跳过] 当日无候选池产物（非交易日或产物缺失）")
        return 0
    print("  名单:", ",".join(syms), flush=True)

    # 需拉取的窗口：候选池是按「信号日」定、次日执行 → 至少要覆盖 day 与前一交易日
    since = args.since or (_dt.date.fromisoformat(day) - _dt.timedelta(days=10)).isoformat()
    print(f"[窗口] since={since}（覆盖 day 及其前置回档窗口）", flush=True)

    if args.dry_run:
        print(f"--dry-run：将拉取 {len(syms)} 只 × {FREQ}（未落盘）")
        return 0

    from data.minute import fetch_minute_kline       # pytdx 主源（9 节点 failover）
    outdir = SNAPSHOT / FREQ
    outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ok = fail = 0
    rows_total = 0
    failed: list[str] = []
    for i, sym in enumerate(syms, 1):
        try:
            df = fetch_minute_kline(sym, freq=FREQ, since=since)
            if df is None or df.empty:
                fail += 1
                failed.append(sym)
                continue
            # 合并既有快照（按 ts 去重、新数据覆盖旧）→ 保持「按 code 覆盖写」语义
            fp = outdir / f"{sym}.parquet"
            import pandas as pd
            if fp.exists():
                try:
                    old = pd.read_parquet(fp)
                    df = (pd.concat([old, df], ignore_index=True)
                            .drop_duplicates(subset=["ts"], keep="last")
                            .sort_values("ts").reset_index(drop=True))
                except Exception:
                    pass
            df.to_parquet(fp, index=False)
            meta = {"code": sym, "freq": FREQ, "source": "pytdx fetch_minute_kline",
                    "fetched_at": _dt.datetime.now().isoformat(timespec="seconds"),
                    "rows": int(len(df)), "first_ts": str(df["ts"].iloc[0]),
                    "last_ts": str(df["ts"].iloc[-1]),
                    "candidate_day": day}
            (outdir / f"{sym}.meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
            ok += 1
            rows_total += len(df)
        except Exception as e:
            fail += 1
            failed.append(sym)
            if fail <= 5:
                print(f"  [warn] {sym}: {type(e).__name__}: {e}", flush=True)
        if i % 10 == 0:
            print(f"  ...{i}/{len(syms)}", flush=True)
        time.sleep(args.pause)          # 限流保护（腾讯备胎有 5000+ 次限流记录）
    print(f"[完成] ok={ok} fail={fail} rows={rows_total} elapsed={time.time()-t0:.0f}s", flush=True)
    if failed:
        print("  失败:", ",".join(failed), flush=True)
    sz = sum(f.stat().st_size for f in outdir.glob("*.parquet"))
    print(f"[体积] data/minute/{FREQ} 现 {len(list(outdir.glob('*.parquet')))} 只 / {sz/1e6:.1f}MB", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
