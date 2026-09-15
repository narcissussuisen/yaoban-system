# -*- coding: utf-8 -*-
"""R3.3 · 双轨 shadow：**诊断轨**（命中/漏选/误选）+ **决策差异报告**（EvoAlpha 会怎么做 vs 选手实际做了什么）。

## 权威定义
`docs/EVOALPHA_V2_RESTRUCTURE_PLAN.md §R3.3`：「双轨 shadow：**诊断轨**（命中率/漏选/误选）+
**决策差异报告**（EvoAlpha 会怎么做 vs 选手实际做了什么）」。
§4.3 另定：「诊断轨：选手命中率 / 漏选误选 / 纪律合规率 → **只报告，不作晋级**」。

## ⚠️ 本模块的根本纪律
**诊断轨只报告，不作晋级。** 晋级闸门是**前向净值**（R5），不是「像不像选手」
—— 「行为复刻 ≠ 收益来源」已证伪（命中 94.4% 但反事实显著反向 p=0.0013）。
**本模块的任何数字都不得用作晋级依据。**

## 为什么只对照 ④ 段（而不是六段全比）
`src/iteration/intraday.py::DECISION_CASES` 里选手的 BUY/VETO **理由全部是分时质量**：
  - VETO：「分时回落跌破分时均价线，短线走势不强，不考虑玩」
  - BUY ：「①最优档：有量+一步步向上」「②中等档：无量欠缺力度」
  → 与 **④ 段的 `D6`（分时质量三档）** 一一对应，是**唯一可直接对照**的一段。
  ② 主线 / ③ 形态 / ⑥ 仓位在选手侧没有同日、同口径的离散标签可比（且 ② 的历史回算有已知限制）。

## ⚠️ 选手标签口径（来自 `DECISION_CASES` 的既有纪律，本模块严格遵守）
`action` 三值：`BUY`（实际买入，规则不应否决）· `VETO`（**明示**因分时走弱而放弃）·
`UNKNOWN`（未执行但**未说明原因**）。
> **`UNKNOWN` 不计入评分** —— 否则会把「资金已满 / 大盘闸门 / 只是没来得及」误判成「分时否决」。

另设一档 **`NOT_IN_POOL`**：选手做了该标的，但它**不在 EvoAlpha 当日候选池**里。
这**不算漏选**（可能是候选池口径差异，而非 D6 判错）→ **单列，不进一致率分母。**

## 用法
    python scripts/run_shadow_report.py --dates 2026-09-03,2026-09-04,2026-09-07,2026-09-09,2026-09-10
    python scripts/run_shadow_report.py --dates 2026-09-10 --no-llm   # 只看机械事实轨
"""
from __future__ import annotations

import json
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(BASE / "src"), str(BASE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT = BASE / "outputs" / "shadow"

# 判定分类（driver = 谁做的决定；outcome_key = 汇总用的桶）
VERDICT_SPEC = {
    "agree_keep":    dict(scored=True,  ok=True,  desc="选手 BUY & EvoAlpha 保留 → **一致**"),
    "agree_veto":    dict(scored=True,  ok=True,  desc="选手 VETO & EvoAlpha 排除 → **一致**"),
    "miss":          dict(scored=True,  ok=False, desc="**漏选**：选手 BUY 但 EvoAlpha 排除"),
    "false_keep":    dict(scored=True,  ok=False, desc="**误留**：选手 VETO 但 EvoAlpha 保留"),
    "not_in_pool":   dict(scored=False, ok=None,  desc="（`universe=pool` 口径下）选手标的**不在当日候选池** → 单列"),
    "no_minute":     dict(scored=False, ok=None,  desc="**无当日 1m 数据** → 无法评估，**不算漏选**（数据缺口≠判错）"),
    "player_unknown": dict(scored=False, ok=None, desc="选手 UNKNOWN（未说明原因）→ **不计入**"),
}


def load_player(day: str) -> list[dict]:
    """选手侧：`DECISION_CASES` 的离散决策标签（含原文理由）。"""
    from iteration.intraday import DECISION_CASES
    out = []
    for row in DECISION_CASES.get(day, []):
        code, name, action, why = (list(row) + ["", "", "", ""])[:4]
        out.append(dict(code=str(code), name=str(name), action=str(action).upper(), why=str(why)))
    return out


def load_evolved(day: str, use_llm: bool = True, codes: list[str] | None = None) -> dict:
    """EvoAlpha 侧：直接调 ④ 段（`seg_entry`）—— 只跑这一段，因为只有它与选手标签同口径。"""
    from decision_chain.engine import seg_entry
    return seg_entry(day, use_llm=use_llm, codes=codes)


def compare(day: str, use_llm: bool = True, universe: str = "player",
            evolved: dict | None = None) -> dict:
    """逐标的对照：EvoAlpha 会怎么做 vs 选手实际做了什么。

    :param universe: `"player"`（默认）＝**直接对选手实际决策的标的**跑 ④ 段。
        这是 R3.3 想要的问法 —— 「**对选手这些标的，EvoAlpha 会怎么做**」。
        候选池（`"pool"`）只是我们自己的观察范围，用它限样本会让绝大多数选手标的
        落进 `not_in_pool`（首跑实测：16 例中 15 例池外，可比样本只剩 1 例，**对照失去意义**）。
    """
    players = load_player(day)
    if universe == "player":
        codes = [p["code"] for p in players]
        ev = evolved if evolved is not None else load_evolved(day, use_llm=use_llm, codes=codes)
        absent_verdict = "no_minute"
    else:
        ev = evolved if evolved is not None else load_evolved(day, use_llm=use_llm)
        absent_verdict = "not_in_pool"
    if ev.get("status") != "ok":
        return dict(date=day, status="no_evolved_data", reason=ev.get("reason"),
                    universe=universe, n_player=len(players), rows=[], summary={})
    by_code = {it["code"]: it for it in (ev.get("items") or [])}

    rows, buckets = [], {k: 0 for k in VERDICT_SPEC}
    for p in players:
        it = by_code.get(p["code"])
        ev_keep = (not it.get("excluded")) if it else None
        ev_d6 = (it or {}).get("d6") or {}
        ev_mech_break = (it or {}).get("break_below_vwap")
        # ⚠️ 判定优先级（2026-09-12 由测试暴露后修正）：
        #   `player_unknown`（选手侧未说明原因）**优先于** `no_minute`（EvoAlpha 侧数据缺口）——
        #   因为 UNKNOWN 是关于**选手披露质量**的属性，与我们有没有数据无关；
        #   两者都 `scored=False`（不影响一致率），但**分桶报告必须准确**，
        #   否则「选手未说明原因」的例数会被数据缺口吞掉，掩盖披露质量问题。
        if p["action"] == "UNKNOWN":
            verdict = "player_unknown"
        elif it is None:
            verdict = absent_verdict
        elif p["action"] == "BUY":
            verdict = "agree_keep" if ev_keep else "miss"
        elif p["action"] == "VETO":
            verdict = "agree_veto" if not ev_keep else "false_keep"
        else:
            verdict = absent_verdict
        buckets[verdict] += 1
        rows.append(dict(code=p["code"], name=p["name"], player_action=p["action"],
                         player_why=p["why"],
                         evo_evaluated=it is not None,
                         evo_verdict=("keep" if ev_keep else "exclude") if it is not None else None,
                         evo_d6_choice=ev_d6.get("choice"), evo_d6_score=ev_d6.get("score"),
                         evo_d6_status=ev_d6.get("status"), evo_d6_reason=ev_d6.get("reason"),
                         evo_mech_break_below_vwap=ev_mech_break,
                         verdict=verdict, verdict_desc=VERDICT_SPEC[verdict]["desc"],
                         scored=VERDICT_SPEC[verdict]["scored"]))

    scored = [r for r in rows if r["scored"]]
    n_ok = sum(1 for r in scored if VERDICT_SPEC[r["verdict"]]["ok"])
    agree = (n_ok / len(scored)) if scored else None
    # 机械式 px<vwap 的独立一致率（与 D6 裁量对照，回答「裁量是否比机械式更贴合选手」）
    mech_scored = [r for r in scored if r["evo_mech_break_below_vwap"] is not None]
    mech_ok = sum(1 for r in mech_scored
                  if (r["player_action"] == "VETO") == bool(r["evo_mech_break_below_vwap"]))
    return dict(
        date=day, status="ok", llm_enabled=use_llm, universe=universe,
        eval_scope=ev.get("scope"),
        n_player=len(players), rows=rows,
        summary=dict(
            buckets=buckets,
            n_scored=len(scored), n_agree=n_ok,
            agree_rate=round(agree, 4) if agree is not None else None,
            n_miss=buckets["miss"], n_false_keep=buckets["false_keep"],
            n_not_in_pool=buckets["not_in_pool"], n_no_minute=buckets["no_minute"],
            n_player_unknown=buckets["player_unknown"],
            mech_n_scored=len(mech_scored), mech_n_agree=mech_ok,
            mech_agree_rate=round(mech_ok / len(mech_scored), 4) if mech_scored else None,
            mech_note="⚠️ 机械式＝`GEN-ENTRY-04` 的 `first_break_below_vwap`（冻结口径），"
                      "**仅作对照量**，它不是本链的否决者（否决权归 D6）。",
        ),
        discipline="**诊断轨只报告，不作晋级。** 晋级闸门＝前向净值（R5）。"
                   "`UNKNOWN` 不计入评分；`not_in_pool` 不算漏选（单列）。",
    )


def aggregate(reports: list[dict]) -> dict:
    """多日汇总（只报告）。"""
    tot = {k: 0 for k in VERDICT_SPEC}
    scored = ok = 0
    mech_s = mech_ok = 0
    for r in reports:
        if r.get("status") != "ok":
            continue
        s = r["summary"]
        for k, v in s["buckets"].items():
            tot[k] += v
        scored += s["n_scored"]
        ok += s["n_agree"]
        mech_s += s["mech_n_scored"]
        mech_ok += s["mech_n_agree"]
    return dict(
        days=[r["date"] for r in reports if r.get("status") == "ok"],
        days_skipped=[r["date"] for r in reports if r.get("status") != "ok"],
        buckets=tot, n_scored=scored, n_agree=ok,
        agree_rate=round(ok / scored, 4) if scored else None,
        mech_n_scored=mech_s, mech_n_agree=mech_ok,
        mech_agree_rate=round(mech_ok / mech_s, 4) if mech_s else None,
        discipline="**只报告，不作晋级**（§4.3）。`UNKNOWN` 不计分；`not_in_pool` 不算漏选。",
    )


def write_report(reports: list[dict], agg: dict, tag: str) -> tuple[pathlib.Path, pathlib.Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    per = OUT / f"shadow_report_{tag}.json"
    per.write_text(json.dumps(dict(reports=reports, aggregate=agg), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    return per, OUT
