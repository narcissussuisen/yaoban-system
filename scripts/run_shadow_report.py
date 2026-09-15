# -*- coding: utf-8 -*-
"""R3.3 · 双轨 shadow 报告 CLI（实现全在 `src/decision_chain/shadow.py`）。

对照「EvoAlpha 会怎么做」vs「选手实际做了什么」，产出**诊断轨**数字。
⚠️ **只报告，不作晋级**（§4.3）；晋级闸门＝前向净值（R5）。

用法:
    python scripts/run_shadow_report.py
    python scripts/run_shadow_report.py --dates 2026-09-03,2026-09-10
    python scripts/run_shadow_report.py --no-llm          # 只跑机械事实轨（快）
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys
import time

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

# 默认＝「选手标签 ∩ 候选池产物」都存在的交易日（由 DECISION_CASES 与 outputs/intraday/confirm_* 求交）
DEFAULT_DATES = ["2026-09-03", "2026-09-04", "2026-09-07", "2026-09-09", "2026-09-10"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", default=",".join(DEFAULT_DATES))
    ap.add_argument("--no-llm", action="store_true", help="只跑机械事实轨（不调 LLM，快）")
    ap.add_argument("--universe", default="player", choices=("player", "pool"),
                    help="player=对选手实际决策的标的跑 ④ 段（默认，R3.3 想要的问法）；"
                         "pool=只在自己候选池范围内对照（会大量落 not_in_pool）")
    args = ap.parse_args()
    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    use_llm = not args.no_llm

    from decision_chain import shadow

    t0 = time.time()
    reports = []
    for d in dates:
        r = shadow.compare(d, use_llm=use_llm, universe=args.universe)
        reports.append(r)
        s = r.get("summary") or {}
        if r.get("status") == "ok":
            print(f"[{d}] 选手 {r['n_player']} 例 → 计入 {s['n_scored']} "
                  f"一致 {s['n_agree']}（{s['agree_rate']}）"
                  f"｜漏选 {s['n_miss']} 误留 {s['n_false_keep']}"
                  f"｜无分钟 {s['n_no_minute']} 未说明 {s['n_player_unknown']}"
                  f"｜机械式 {s['mech_agree_rate']}", flush=True)
        else:
            print(f"[{d}] 跳过：{r.get('reason')}", flush=True)

    agg = shadow.aggregate(reports)
    tag = f"{dates[0]}_{dates[-1]}" + ("" if use_llm else "_noLLM")
    per, outdir = shadow.write_report(reports, agg, tag)

    print("\n=== 诊断轨汇总（**只报告，不作晋级**）===")
    print(f"  可比对交易日: {agg['days']}")
    if agg["days_skipped"]:
        print(f"  跳过（无 EvoAlpha 侧数据）: {agg['days_skipped']}")
    print(f"  计入样本 {agg['n_scored']}（选手 UNKNOWN / 无分钟数据 / 池外标的不计）")
    print(f"  **一致率 = {agg['n_agree']}/{agg['n_scored']} = {agg['agree_rate']}**")
    print(f"  漏选 {agg['buckets']['miss']} · 误留 {agg['buckets']['false_keep']}")
    print(f"  无分钟数据 {agg['buckets']['no_minute']}（数据缺口≠判错）· "
          f"池外 {agg['buckets']['not_in_pool']} · 选手未说明 {agg['buckets']['player_unknown']}（不计分）")
    print(f"  对照量·机械式 px<vwap 一致率 = {agg['mech_n_agree']}/{agg['mech_n_scored']} = {agg['mech_agree_rate']}")
    print(f"  ⚠️ {agg['discipline']}")
    print(f"\n→ {per}")

    # 逐标的明细（不一致 + 无分钟数据，便于复盘）
    bad = [(r["date"], row) for r in reports if r.get("status") == "ok"
           for row in r["rows"] if row["verdict"] in ("miss", "false_keep", "no_minute")]
    if bad:
        print("\n=== 不一致 / 数据缺口明细（复盘用）===")
        print(f"{'日期':<12}{'代码':<8}{'名称':<10}{'选手':<8}{'EvoAlpha':<9}{'D6':<11}{'判定'}")
        for d, row in bad:
            print(f"{d:<12}{row['code']:<8}{str(row['name'])[:8]:<10}{row['player_action']:<8}"
                  f"{str(row['evo_verdict']):<9}{str(row['evo_d6_choice']):<11}{row['verdict']}")
    print(f"\nelapsed={time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
