"""每日自迭代批编排（EvoAlphaDailyIteration 内核，22:00 计划任务调用）。

流水线：资料扫描 → 知识卡片 → 画像 → 提案（门槛判定）→ 影子回归 → 台账与卡片摘要

边界（硬）：
  - 全程只读资料与行情；不改账本、不写门禁判定、不改生产代码。
  - parameter 类提案过门槛后写入台账与补丁文件（patch），**不直接改写 config**；
    落盘生效需人工确认（RUNBOOK §自迭代批）。
  - rule / code / data 类提案恒 pending_confirm。
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKSPACE = ROOT.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from iteration import cards as cards_mod          # noqa: E402
from iteration import market, profile as profile_mod, proposals as prop_mod  # noqa: E402
from iteration import rules, shadow, sources      # noqa: E402
from iteration.model import BatchResult, now_str, today_str   # noqa: E402

CARDS_DIR = ROOT / "data" / "iteration" / "knowledge_cards"
CASE_FILE = ROOT / "data" / "iteration" / "case_table_yaoban.json"
OUT_ROOT = ROOT / "outputs" / "iteration"
PROP_DIR = ROOT / "outputs" / "iteration_proposals"      # 与既有消费方兼容
LEDGER = ROOT / "outputs" / "iteration" / "ledger.jsonl"
MATERIALS = WORKSPACE / "选手学习资料"


def _write_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _write_text(path: pathlib.Path, s: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(s, encoding="utf-8", newline="\n")


def build_segments(rule_names: list[str], config: dict, codes: list[str],
                   market_start: str, cap: int) -> tuple[dict, str, int]:
    """为每个（规则, 轴组合, 参数）计算两段指标。返回 (segments, split_date, days)。"""
    probe_codes = codes[: min(len(codes), 800)]
    all_days = sorted(set(_iter_dates(probe_codes, market_start)))
    days = len(all_days)
    split = all_days[len(all_days) // 2] if all_days else market_start

    segments: dict = {}
    for name in rule_names:
        spec = rules.RULES[name]
        if spec["kind"] != "entry":
            continue
        for params in rules.single_axis_variants(name, config):
            key = tuple(sorted((k, repr(v)) for k, v in params.get("_axis", {}).items()))
            segs = []
            for lo, hi in ((market_start, split), (split, None)):
                segs.append(shadow.market_shadow_signals(
                    name, params, codes, start=lo, end=hi, cap=cap))
            for p in spec["grid_params"]:
                segments[(name, key, p)] = segs
    return segments, split, days


def _iter_dates(codes: list[str], start: str):
    seen = set()
    for c in codes:
        df = market.load_daily(c)
        if df is None or not len(df):
            continue
        for d in df["date"]:
            if str(d) >= start:
                seen.add(str(d))
    for d in sorted(seen):
        yield d


def run(date: str | None = None, *, case_window: int = 4, market_codes: int = 1200,
        market_cap: int = 3000, market_start: str = "2026-04-01",
        rule_names: list[str] | None = None, with_portfolio: bool = True) -> BatchResult:
    date = date or today_str()
    run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / date
    out_dir.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []

    # ---- 0. 输入自检 ----
    table_path = ROOT / "config" / "parameters.toml"
    import tomllib
    config = tomllib.loads(table_path.read_text(encoding="utf-8"))

    # ---- 1. 资料扫描 ----
    scan = sources.run(OUT_ROOT, MATERIALS, out_dir)
    notes.append(f"资料扫描：{scan['file_count']} 个文件，新增 {len(scan['new'])}，"
                 f"变更 {len(scan['changed'])}"
                 + ("（首次基线，全部计为新增）" if scan.get("baseline") else ""))

    # ---- 2. 知识卡片 ----
    lib, warn = cards_mod.load_library(CARDS_DIR)
    notes += warn
    cs = cards_mod.summarize(lib)
    _write_text(out_dir / "knowledge_cards.md", cards_mod.render_library(lib))
    _write_json(out_dir / "knowledge_cards.json",
                {"summary": cs, "cards": [c.to_dict() for c in lib]})

    # ---- 3. 画像 ----
    prof = profile_mod.build_profile(lib, date)
    prev_prof = sorted(
        (d for d in OUT_ROOT.iterdir() if d.is_dir() and d.name < date), reverse=True)
    prev_path = (prev_prof[0] / "profile.json") if prev_prof else None
    pdiff = profile_mod.diff_against(prev_path, prof) if prev_path else {"baseline": True}
    prof["diff"] = pdiff
    _write_text(out_dir / "profile.md", profile_mod.render_markdown(prof))
    _write_json(out_dir / "profile.json", prof)
    notes.append(f"画像：{prof['card_total']} 张卡片；变更 {pdiff}")

    # ---- 4. 数据自检 + 影子回归 ----
    names = rule_names or [k for k, v in rules.RULES.items() if v["kind"] == "entry"]
    codes = market.available_codes(limit=market_codes)
    span = market.history_span(codes)
    latest_bar = span.get("end_max")
    if latest_bar and latest_bar < date:
        notes.append(f"⚠️ 行情最新 bar = {latest_bar}（早于批次日 {date}）："
                     f"前向窗口受限，末段样本会被自动截断")
    _write_json(out_dir / "data_readiness.json",
                {"span": span, "codes_available": len(market.available_codes()),
                 "codes_used": len(codes), "market_start": market_start,
                 "latest_bar": latest_bar})

    case = shadow.load_case_table(CASE_FILE)
    sh = shadow.run(names, config, case, codes, case_window=case_window,
                    market_cap=market_cap, market_start=market_start)

    # 分段稳定性（两段）
    segments, split, tdays = build_segments(names, config, codes, market_start, market_cap)
    _write_json(out_dir / "shadow_segments.json",
                {"split": split, "trading_days": tdays,
                 "keys": [f"{k[0]}|{k[1]}|{k[2]}" for k in list(segments)[:200]]})

    # 分时规则层（需本地分钟快照；无快照时显式记缺口，不静默跳过）
    intraday_result = None
    try:
        from iteration import intraday as intraday_mod
        cov = intraday_mod.coverage()
        if cov["missing_n"] == 0:
            base = intraday_mod.evaluate_frozen()
            intraday_result = {"coverage": cov, "baseline": base,
                               "frozen_params": intraday_mod.FROZEN_VWAP_PARAMS}
            _write_json(out_dir / "intraday_rules.json", intraday_result)
        else:
            notes.append(f"⚠️ 分时规则未评估：分钟快照缺失 {cov['missing_n']} 只"
                         f"（跑 tools/snapshot_minute.py --from-case-table 补齐）")
    except Exception as e:  # noqa: BLE001 - 分时层失败不得拖垮主链
        notes.append(f"⚠️ 分时规则评估异常：{type(e).__name__}: {e}")

    # 分时规则的全市场反事实检验（判定「命中选手行为」是否等于「有收益筛选价值」）
    counterfactual = None
    cf_prev = OUT_ROOT / "intraday_counterfactual.json"
    if cf_prev.exists():
        try:
            counterfactual = json.loads(cf_prev.read_text(encoding="utf-8"))
            if counterfactual.get("since") != "2026-08-20":
                notes.append("⚠️ 反事实检验结果的窗口与当前批次不一致，请重跑 "
                             "tools/run_intraday_counterfactual.py")
        except Exception as e:  # noqa: BLE001
            notes.append(f"⚠️ 反事实检验结果不可读：{e}")
    else:
        notes.append("⚠️ 未找到反事实检验结果（tools/run_intraday_counterfactual.py）"
                     "→ 分时规则按「无收益筛选价值」处理，不予晋级")

    # 影子盘（当前配置）
    port: dict = {}
    if with_portfolio:
        for name in names:
            base = rules.rule_params(name, config)
            if not base:
                continue
            port[name] = shadow.market_shadow_portfolio(name, base, codes, start=market_start)
        _write_json(out_dir / "shadow_portfolio.json", port)
    sh["portfolio"] = port
    sh["intraday"] = intraday_result
    _write_json(out_dir / "shadow_regression.json", sh)

    # ---- 5. 提案 + 门禁 ----
    props = []
    seq = 1
    for name, block in sh["rules"].items():
        got = prop_mod.mine_parameter_axes(
            name, block, config, days=tdays,
            segments={k: v for k, v in segments.items() if k[0] == name}, seq=seq)
        props += got
        seq += len(got)
    props += prop_mod.mine_gaps(lib, case, seq=100, shadow=sh, intraday=intraday_result,
                                counterfactual=counterfactual)
    props = prop_mod.sort_proposals(props)

    applied = [p for p in props if p.status == "applied"]
    pending = [p for p in props if p.status == "pending_confirm"]
    insufficient = [p for p in props if p.status == "insufficient_evidence"]
    rejected = [p for p in props if p.status == "rejected"]

    payload = {
        "date": date, "run_id": run_id, "generated_at": now_str(),
        "source": "EvoAlphaDailyIteration（资料解析→知识卡片→画像→提案→影子回归）",
        "config_file": str(table_path.relative_to(ROOT)),
        "status": "pending_confirm" if pending else ("applied" if applied else "no_change"),
        "policy": {
            "parameter": "过门槛自动生效（写台账 + patch，不改写 config）",
            "rule/code/data": "恒 pending_confirm，需人工确认",
        },
        "counts": {"applied": len(applied), "pending_confirm": len(pending),
                   "insufficient_evidence": len(insufficient), "rejected": len(rejected)},
        "proposals": [p.to_dict() for p in props],
        "cards_summary": cs,
        "data_readiness": {"latest_bar": latest_bar, "trading_days": tdays,
                           "market_start": market_start, "split": split},
        "notes": notes,
    }
    PROP_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(PROP_DIR / f"{date}.json", payload)
    _write_json(out_dir / "proposals.json", payload)

    # 生效台账（append-only）
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8", newline="\n") as f:
        for p in applied:
            f.write(json.dumps({
                "ts": now_str(), "date": date, "run_id": run_id,
                "proposal_id": p.proposal_id, "change_class": p.change_class,
                "target": p.target, "evidence": p.evidence, "thresholds": p.thresholds,
            }, ensure_ascii=False) + "\n")

    # 参数补丁预览（人工确认后再落盘）
    if applied:
        patch = {
            "generated_at": now_str(), "date": date,
            "warning": "本文件是补丁预览，不会自动写入 config/parameters.toml；"
                       "确认后由人工/会话执行落盘并 bump RULES_VERSION。",
            "changes": [{"proposal_id": p.proposal_id, "target": p.target,
                         "evidence_deltas": p.evidence.get("deltas")} for p in applied],
        }
        _write_json(out_dir / "parameter_patch.json", patch)

    # ---- 6. 人读摘要 ----
    from iteration import reports
    _write_text(out_dir / "digest.md", reports.render_digest(
        date=date, run_id=run_id, scan=scan, cs=cs, prof=prof, pdiff=pdiff,
        sh=sh, props=props, span=span, notes=notes, case=case, tdays=tdays,
        split=split, latest_bar=latest_bar))

    res = BatchResult(
        run_id=run_id, date=date, generated_at=now_str(),
        mats_new=len(scan["new"]), mats_total=scan["file_count"],
        cards_total=len(lib), cards_new=len(pdiff.get("added", [])),
        proposals_total=len(props), proposals_applied=len(applied),
        proposals_pending=len(pending), shadow_runs=sum(
            len(b["variants"]) for b in sh["rules"].values()),
        shadow_ok=sum(1 for b in sh["rules"].values() for v in b["variants"]
                      if v.get("cases")),
        shadow_insufficient=len(insufficient), notes=notes)
    _write_json(out_dir / "batch_result.json", res.to_dict())
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="EvoAlpha 每日自迭代批")
    ap.add_argument("--date", default=None, help="批次日期（默认今天）")
    ap.add_argument("--market-codes", type=int, default=1200)
    ap.add_argument("--market-cap", type=int, default=3000)
    ap.add_argument("--market-start", default="2026-04-01")
    ap.add_argument("--case-window", type=int, default=4)
    ap.add_argument("--no-portfolio", action="store_true")
    ap.add_argument("--rules", default=None, help="逗号分隔的规则名（默认全部入场规则）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    names = [x.strip() for x in a.rules.split(",")] if a.rules else None
    res = run(date=a.date, case_window=a.case_window, market_codes=a.market_codes,
              market_cap=a.market_cap, market_start=a.market_start,
              rule_names=names, with_portfolio=not a.no_portfolio)
    if not a.quiet:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
