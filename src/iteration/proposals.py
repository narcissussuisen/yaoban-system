"""提案挖掘：把影子回归结果 + 卡片缺口转成结构化提案。

两类来源：
  1. 参数轴挖掘（parameter 类）——逐轴单变量对比基准配置，过 gate 则 applied。
  2. 卡片缺口（rule / code / data 类）——未量化规则、被阻塞的分时规则、数据缺口，
     恒为 pending_confirm，供人工裁决（对应「规则/代码类等你确认」）。
"""
from __future__ import annotations

import pathlib

from . import gate, rules
from .model import (CHANGE_CODE, CHANGE_DATA, CHANGE_RULE, VERDICT_PENDING_CONFIRM, Proposal)

AXIS_LABEL = {
    "vol_ratio_max": "量能比上限",
    "vol_mult_min": "涨停柱放量下限倍数",
    "vol_base_ma": "放量基准均量周期",
    "pullback_days_max": "回档日数上限",
    "pullback_days_min": "回档日数下限",
    "entry_gain_max": "买点当日涨幅上限",
    "not_break_zt_low": "不破涨停柱低点（必守）",
    "vol_ratio_basis": "缩量比较基准",
    "ma_ref": "反包回踩参照均线",
    "ma_tol": "企稳容差",
    "vol_expand": "反包放量倍数下限",
    "exit_gain_max": "短线走弱涨幅上限",
    "vol_confirm": "破位放量确认倍数",
    "ma_n": "趋势线均线周期",
    "pullback_pct_min": "回调幅度下限",
    "pullback_pct_max": "回调幅度上限",
}


def _fmt(v) -> str:
    return "不限制(None)" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))


def _key(axis: dict) -> tuple:
    return tuple(sorted((k, repr(v)) for k, v in axis.items()))


def mine_parameter_axes(rule_name: str, block: dict, config: dict, *,
                        days: int = 0, segments: dict | None = None,
                        seq: int = 1) -> list[Proposal]:
    """逐轴单变量挖掘参数提案（其他轴固定为当前配置值）。

    segments: {(rule, axis_key, param): [seg_metrics...]} 供分段稳定性门槛使用。
    days: 回测窗口交易日数（由调用方按行情日期数给出，用于门禁的窗口门槛）。
    """
    spec = rules.RULES[rule_name]
    base_params = rules.rule_params(rule_name, config)
    cfg_paths = spec.get("config_paths", {})
    axis_params = list(spec["grid_params"])

    variants = {_key(v.get("_axis", {})): v for v in block["variants"]}
    markets = {_key(m.get("_axis", {})): m for m in block.get("market", [])}
    base_key = _key({p: base_params.get(p) for p in axis_params if p in base_params})
    base_case = variants.get(base_key)
    base_mkt = markets.get(base_key)

    proposals: list[Proposal] = []
    if base_mkt is None or base_case is None:
        return proposals

    for p in axis_params:
        if p not in cfg_paths:
            continue                      # 无 config 归属 → 无法作为参数类生效
        cur = base_params.get(p)
        seen = set()
        for key, mkt in markets.items():
            # key 为 _axis 的 repr 化键值对，还原后即为该变体的轴取值
            cand_axis = {k: eval(v) for k, v in key}   # noqa: S307 - 键来自本模块 variants() 自产
            cand = cand_axis.get(p, "__missing__")
            if cand == "__missing__" or _key(cand_axis) == base_key:
                continue
            if repr(cand) in seen:
                continue
            seen.add(repr(cand))
            if cand == cur:
                continue
            case = variants.get(_key(cand_axis))
            segs_base = segs_cand = None
            if segments:
                segs_base = segments.get((rule_name, base_key, p))
                segs_cand = segments.get((rule_name, _key(cand_axis), p))
            g = gate.check(
                base_mkt, mkt, param_path=cfg_paths[p], axis_param=p,
                cur_value=cur, cand_value=cand, days=days,
                seg_base=segs_base, seg_cand=segs_cand,
            )
            kind = "放宽" if g.get("loosening") else ("收紧" if g.get("loosening") is False else "调整")
            title = f"{spec['label']}：{AXIS_LABEL.get(p, p)} {_fmt(cur)} → {_fmt(cand)}（{kind}）"
            ev = {
                "rule": rule_name, "axis": p,
                "base": {k: base_mkt.get(k) for k in
                         ("n", "mean_ret_pct", "win_rate_pct", "p10_ret_pct", "hwm_ge3_pct")},
                "candidate": {k: mkt.get(k) for k in
                              ("n", "mean_ret_pct", "win_rate_pct", "p10_ret_pct", "hwm_ge3_pct")},
                "case_recall": {"base": base_case.get("recall_pct"),
                                "candidate": (case or {}).get("recall_pct")},
                "case_false_kill": {"base": base_case.get("false_kill_n"),
                                    "candidate": (case or {}).get("false_kill_n")},
                "deltas": g.get("deltas"),
                "date_range": mkt.get("date_range"),
            }
            proposals.append(Proposal(
                proposal_id=f"P{seq:03d}",
                change_class="parameter",
                title=title,
                rationale=(f"影子回归：基准 n={base_mkt.get('n')} 均值 {base_mkt.get('mean_ret_pct')}% / "
                           f"胜率 {base_mkt.get('win_rate_pct')}% → 候选 n={mkt.get('n')} "
                           f"均值 {mkt.get('mean_ret_pct')}% / 胜率 {mkt.get('win_rate_pct')}%；"
                           f"案例召回 {base_case.get('recall_pct')}% → {(case or {}).get('recall_pct')}%"),
                card_ids=spec["card_ids"],
                target={cfg_paths[p]: {"from": cur, "to": cand}},
                evidence=ev,
                thresholds={"checks": g["thresholds"], "reason": g["reason"],
                            "verdict": g["verdict"]},
                samples=int(mkt.get("n", 0)),
                risk=("放宽阈值会增加信号数，若后续行情结构与回测窗口不同，可能出现负向漂移；"
                      "回滚方式=恢复 config 原值（台账记录 from 值）。"),
                status=g["verdict"],
                decided_at=__import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
                decision_reason=g["reason"],
            ))
            seq += 1
    return proposals


def _mkt_axis_value(mkt: dict, param: str):
    return (mkt.get("_axis") or {}).get(param)


def mine_gaps(cards: list, case: dict, *, seq: int = 100, shadow: dict | None = None,
              intraday: dict | None = None,
              counterfactual: dict | None = None) -> list[Proposal]:
    """从卡片缺口与回归阻塞点挖出 rule / code / data 类提案（恒待人工确认）。"""
    shadow = shadow or {}
    out: list[Proposal] = []
    n = seq

    unquant = [c for c in cards if c.testability == "qualitative" or not c.quantified]
    for c in unquant:
        out.append(Proposal(
            proposal_id=f"P{n:03d}", change_class=CHANGE_RULE,
            title=f"未量化规则待定义：{c.card_id} {c.title}",
            rationale=(f"卡片结论「{c.statement}」未给出可执行阈值，无法进入影子回归；"
                       f"需人工定义量化判据后才能编码。"),
            card_ids=[c.card_id], target={}, evidence={"testability": c.testability},
            thresholds={"checks": [{"name": "变更类别", "passed": True,
                                    "detail": "rule 类不适用自动生效（硬边界）"}]},
            samples=c.sample_n or 0,
            risk="以未量化规则上线会造成不可复现的语义判断，违反蓝图 §9 硬边界。",
            status=VERDICT_PENDING_CONFIRM, decided_at="",
            decision_reason="等待人工定义量化判据",
        ))
        n += 1

    intraday_cards = [c for c in cards if c.testability == "intraday"]
    if intraday_cards and intraday and intraday.get("baseline"):
        b = intraday["baseline"]
        fp = intraday.get("frozen_params", {})
        rows = b.get("rows", [])
        buys = [r for r in rows if r.get("action") == "BUY"]
        vetoes = [r for r in rows if r.get("action") == "VETO"]
        # 门禁：命中选手行为 ≠ 有收益筛选价值 → 必须过全市场反事实检验
        g = gate.intraday_rule_verdict(counterfactual, card_title="跌破均价线不玩")
        cf = counterfactual or {}
        cf_v, cf_p = cf.get("veto", {}), cf.get("pass", {})
        out.append(Proposal(
            proposal_id=f"P{n:03d}", change_class=CHANGE_RULE,
            title=("⭐ 分时规则「跌破均价线不玩」：选手行为吻合 "
                   f"{b.get('accuracy_pct')}%（{b['days']} 日 {b['scored']} 条），"
                   + ("反事实检验通过" if g["verdict"] == "applied" else "但反事实检验未通过")),
            rationale=(
                "本地分钟快照（tools/snapshot_minute.py，1m）已覆盖案例窗口，按显式口径确定性复现："
                f"「分时均价线=当日累计成交额/累计成交量；决策窗口 {fp.get('window_start')}–"
                f"{fp.get('window_end')}；连续 {fp.get('confirm_minutes')} 分钟收盘低于均价线"
                f"×(1−{fp.get('tol')}) → 弃」。"
                f"在 {b['days']} 个交易日 {b['scored']} 条**选手明示**决策上："
                f"明示否决命中 {b['veto_correct']}、漏否决 {b['veto_missed']}、"
                f"误否决 {b['false_veto']}、买入存活 {b['buy_correct']}"
                + (f"（另有 {b['unknown_excluded']} 条未说明原因，不计分）。" if b.get('unknown_excluded') else "。") +
                "——即该规则能高精度**复刻选手行为**。"
                "但全市场反事实检验（tools/run_intraday_counterfactual.py）给出相反结论："
                f"被否决事件 {cf_v.get('n')} 个、前向均值 {cf_v.get('mean_ret_pct')}%，"
                f"通过事件 {cf_p.get('n')} 个、前向均值 {cf_p.get('mean_ret_pct')}%"
                f"（差 {(cf_p.get('mean_ret_pct', 0) - cf_v.get('mean_ret_pct', 0)):+.3f}pp，"
                f"Welch p≈{(cf.get('welch') or {}).get('p_two_sided_approx')}）。"
                "**该纪律不能筛选收益，方向甚至相反**：这解释了选手为何规避（回踩后走强的样本反而更多），"
                "但机械复制它不产生 alpha。"),
            card_ids=[c.card_id for c in intraday_cards],
            target={"module": "src/iteration/intraday.py",
                    "config_keys": ["entry.vwap_veto.window_start", "entry.vwap_veto.window_end",
                                    "entry.vwap_veto.confirm_minutes", "entry.vwap_veto.tol"],
                    "integration": "src/core/scan 候选过滤（发布后立即判定）"},
            evidence={"frozen_params": fp,
                      "days": b["days"], "scored": b["scored"],
                      "unknown_excluded": b.get("unknown_excluded"),
                      "veto_correct": b["veto_correct"], "veto_missed": b["veto_missed"],
                      "false_veto": b["false_veto"], "buy_correct": b["buy_correct"],
                      "accuracy_pct": b.get("accuracy_pct"),
                      "counterfactual": g.get("counterfactual"),
                      "rows": [{"date": r.get("date"), "code": r.get("code"), "name": r.get("name"),
                                "action": r.get("action"), "veto": r.get("veto"),
                                "break_ts": r.get("break_ts"),
                                "below_minutes": r.get("below_minutes")} for r in rows]},
            thresholds={"checks": g["thresholds"], "verdict": g["verdict"]},
            samples=b["scored"],
            risk=("样本为 7 个交易日 18 条明示决策且同属一段市场状态；"
                  "反事实检验为 15 个交易日、同一市场状态。"
                  "若要上线须先在更多市场状态（含下跌/震荡市）复现。"),
            status=g["verdict"], decided_at=g["decided_at"], decision_reason=g["reason"],
        ))
        n += 1
    elif intraday_cards:
        out.append(Proposal(
            proposal_id=f"P{n:03d}", change_class=CHANGE_CODE,
            title=f"分时规则自动化受阻（{len(intraday_cards)} 张卡片）：需分钟数据管线",
            rationale=("以下卡片口径为分时级，日线无法确定性复现：" +
                       "、".join(f"{c.card_id} {c.title}" for c in intraday_cards) +
                       "。本地分钟快照可由 tools/snapshot_minute.py 按需落盘"
                       "（pytdx 1m 窗口约 3.7 个月）。"),
            card_ids=[c.card_id for c in intraday_cards],
            target={"module": "src/iteration/intraday.py", "need": "minute_snapshot"},
            evidence={"note": "分钟快照缺失，分时层未评估"},
            thresholds={"checks": [{"name": "变更类别", "passed": True,
                                    "detail": "code 类不适用自动生效（硬边界）"}]},
            samples=sum(c.sample_n or 0 for c in intraday_cards),
            risk="未接入前，「跌破均价线不玩」这一条最强纪律无法进入自动化校验。",
            status=VERDICT_PENDING_CONFIRM, decided_at="",
            decision_reason="等待人工决定是否建分钟数据快照管线",
        ))
        n += 1

    # 未落入 config 的参数轴：不能作为参数类自动生效（无归属），显式提为 rule 类
    for name, block in (shadow or {}).get("rules", {}).items():
        spec = rules.RULES[name]
        unmapped = [p for p in spec["grid_params"] if p not in spec.get("config_paths", {})]
        if not unmapped:
            continue
        out.append(Proposal(
            proposal_id=f"P{n:03d}", change_class=CHANGE_RULE,
            title=f"{spec['label']}：参数轴 {', '.join(AXIS_LABEL.get(p, p) for p in unmapped)} 无 config 归属",
            rationale=("以下参数对信号有实质影响，但 `config/parameters.toml` 中没有对应键，"
                       "故无法进入「参数类过门槛自动生效」路径：" +
                       "、".join(f"{p}（{AXIS_LABEL.get(p, p)}）" for p in unmapped) +
                       "。需人工决定是否新增 config 键并纳入门禁管辖。"),
            card_ids=spec["card_ids"], target={"rule": name, "params": unmapped},
            evidence={"grid": {p: spec["grid"].get(p) for p in unmapped}},
            thresholds={"checks": [{"name": "变更类别", "passed": True,
                                    "detail": "rule 类不适用自动生效（硬边界）"}]},
            samples=sum(int(v.get("n", 0)) for v in block.get("market", [])[:1]),
            risk="未纳入 config 的参数只能靠代码常量维持，变更不可审计、不可回滚。",
            status=VERDICT_PENDING_CONFIRM, decided_at="",
            decision_reason="等待人工决定是否新增 config 键",
        ))
        n += 1

    gaps = case.get("data_gaps", [])
    if gaps:
        out.append(Proposal(
            proposal_id=f"P{n:03d}", change_class=CHANGE_DATA,
            title=f"案例表数据缺口 {len(gaps)} 项待补",
            rationale="；".join(gaps),
            card_ids=[], target={"file": "data/iteration/case_table_yaoban.json"},
            evidence={"gaps": gaps},
            thresholds={"checks": [{"name": "变更类别", "passed": True,
                                    "detail": "data 类不适用自动生效（硬边界）"}]},
            samples=0,
            risk="缺口会让案例召回率分母偏小，得出过于乐观的结论。",
            status=VERDICT_PENDING_CONFIRM, decided_at="",
            decision_reason="等待人工补料或确认可接受",
        ))
        n += 1

    # 案例召回不足的规则族 → 提示归属或规则结构问题
    for name, block in shadow.get("rules", {}).items():
        if block["kind"] != "entry":
            continue
        best = None
        for v in block["variants"]:
            if v.get("recall_pct") is None or not v.get("cases"):
                continue
            if best is None or v["recall_pct"] > best["recall_pct"]:
                best = v
        if best and best["cases"] and best["recall_pct"] < 100.0:
            out.append(Proposal(
                proposal_id=f"P{n:03d}", change_class=CHANGE_RULE,
                title=f"规则召回不足：{block['label']} 最好仅 {best['recall_pct']}%"
                      f"（{best['hits']}/{best['cases']}）",
                rationale=("全部参数组合都无法覆盖该战法族的实际入场样本：" +
                           "；".join(f"{m.get('name')}({m.get('code')}) {m.get('why')}"
                                    for m in best.get("misses", [])[:6]) +
                           "。可能原因：归属错误、规则结构缺失（形态未编码）、或样本本身非该战法。"),
                card_ids=block["card_ids"],
                target={"rule": name},
                evidence={"best_params": best.get("params"), "best_recall_pct": best["recall_pct"],
                          "misses": best.get("misses", [])},
                thresholds={"checks": [{"name": "变更类别", "passed": True,
                                        "detail": "rule 类不适用自动生效（硬边界）"}]},
                samples=best["cases"],
                risk="强行提高召回会引入形态不符的样本，导致影子盘质量下降。",
                status=VERDICT_PENDING_CONFIRM, decided_at="",
                decision_reason="等待人工裁决规则结构",
            ))
            n += 1
    return out


def sort_proposals(items: list[Proposal]) -> list[Proposal]:
    order = {"applied": 0, "insufficient_evidence": 1, "rejected": 2, "pending_confirm": 3}
    return sorted(items, key=lambda p: (order.get(p.status, 9), -p.samples, p.proposal_id))
