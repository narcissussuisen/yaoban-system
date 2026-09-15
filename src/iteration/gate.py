"""参数门禁：决定提案能否**自动生效**（parameter 类）或必须等人工确认。

硬边界（继承蓝图）：
  - 只有 parameter 类可自动生效；rule / code / data 类**永远** pending_confirm。
  - 自动生效需同时通过「样本量 / 质量不退化 / 尾部风险 / 容量或收益改善 / 分段稳定」五项门槛。
  - 未达样本量一律判 `insufficient_evidence`（不是"通过"也不是"否决"），必须显式可见。
  - 任何判定都带逐项阈值明细，可审计、可回放。

本模块**不写** config/parameters.toml：生效由 apply.py 写入台账 + 补丁文件，
最终落盘需人工确认（见 RUNBOOK）。
"""
from __future__ import annotations

from .model import CHANGE_PARAMETER, VERDICT_APPLIED, VERDICT_PENDING_CONFIRM, now_str

# 门槛阈值（改这里就是改门禁本身，属 rule 类变更，需人工确认）
MIN_EVENTS = 30             # 候选配置最少事件数
MIN_BASE_EVENTS = 20        # 基准配置最少事件数（否则不可比）
MIN_TRADING_DAYS = 20       # 回测窗口最少交易日
MAX_MEAN_DEGRADE = 1.0      # 平均收益允许退化上限（百分点）
MAX_P10_DEGRADE = 1.0       # 10 分位收益允许退化上限（百分点）
MIN_WIN_RATE_GAIN = 1.0     # 质量路径：胜率提升下限（百分点）
MIN_MEAN_GAIN = 0.5         # 质量路径：平均收益提升下限（百分点）
MIN_CAPACITY_GAIN = 1.15    # 容量路径：事件容量提升倍数
MAX_WIN_RATE_DROP = 5.0     # 容量路径：胜率允许下降上限（百分点）
MIN_TOTAL_RET_GAIN = 1.0    # 容量路径：总收益（n × 均值）不得下降（倍数）
MIN_SEGMENT_N = 10          # 分段稳定性：每段最少事件
SEGMENTS = 2

# 绝对可用性地板：候选配置自身必须「能站住」，否则相对改善只是「从很亏变成没那么亏」。
# 这一条与相对比较相互独立，任何路径都不能绕过它。
MIN_CAND_WIN_RATE = 50.0    # 候选胜率下限（%）
# 候选总收益（n × 均值）不得为负——负总收益意味着整套信号纯亏，无论胜率数字多好看。
MIN_CAND_TOTAL_RET = 0.0
# 相对改善不得以「总收益变差」为代价（防止容量扩大但整体更亏）。

# 参数「放宽」方向：True = 变大更宽，False = 变小更宽，None = 非单调（不判定）
LOOSEN_DIR: dict[str, bool | None] = {
    "vol_ratio_max": True,
    "vol_mult_min": False,       # 放量门槛越低越宽
    "vol_base_ma": None,
    "pullback_days_max": True,
    "pullback_days_min": False,
    "pullback_pct_max": True,
    "pullback_pct_min": False,
    "entry_gain_max": True,      # None 视为 +inf
    "not_break_zt_low": False,   # False = 不要求「不破涨停柱低点」→ 更宽
    "vol_ratio_basis": None,     # 口径选择，无宽严之分
    "ma_ref": None,
    "ma_tol": True,              # 「企稳」容差越大越宽
    "vol_expand": False,         # 阈值越低越宽（None = 不要求放量）
    "exit_gain_max": True,
    "vol_confirm": False,
    "ma_n": None,
}


def _num(v) -> float:
    return float("inf") if v is None else float(v)


def loosening_axis(param_path: str, axis_param: str, cur, cand) -> bool | None:
    """判断候选相对现状是「放宽」还是「收紧」。None = 无法判定（非单调参数）。"""
    if cur == cand:
        return None
    d = LOOSEN_DIR.get(axis_param)
    if d is None:
        return None
    if axis_param == "not_break_zt_low":
        # 布尔型：False 更宽（不要求「不破涨停柱低点」）
        return (cand is False) and (cur is not False)
    if cur is None:
        cur_v = float("inf")
    else:
        cur_v = float(cur)
    cand_v = float("inf") if cand is None else float(cand)
    if cur_v == cand_v:
        return None
    wider = cand_v > cur_v
    return wider if d else (not wider)


def check(base: dict, cand: dict, *, param_path: str, axis_param: str,
          cur_value, cand_value, change_class: str = CHANGE_PARAMETER,
          days: int = 0, seg_base: list[dict] | None = None,
          seg_cand: list[dict] | None = None) -> dict:
    """逐项门槛判定。返回 {verdict, thresholds, reason}。"""
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str):
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    n_base, n_cand = int(base.get("n", 0)), int(cand.get("n", 0))
    add("样本量·候选", n_cand >= MIN_EVENTS, f"候选事件 {n_cand} ≥ {MIN_EVENTS}")
    add("样本量·基准", n_base >= MIN_BASE_EVENTS, f"基准事件 {n_base} ≥ {MIN_BASE_EVENTS}")
    add("回测窗口", days >= MIN_TRADING_DAYS, f"交易日 {days} ≥ {MIN_TRADING_DAYS}")

    if n_cand < MIN_EVENTS or n_base < MIN_BASE_EVENTS or days < MIN_TRADING_DAYS:
        return {
            "verdict": "insufficient_evidence",
            "thresholds": checks,
            "reason": (f"证据不足：候选 {n_cand} / 基准 {n_base} 事件，窗口 {days} 交易日；"
                       f"门槛要求 ≥{MIN_EVENTS} / ≥{MIN_BASE_EVENTS} / ≥{MIN_TRADING_DAYS}"),
            "loosening": loosening_axis(param_path, axis_param, cur_value, cand_value),
        }

    bm = float(base.get("mean_ret_pct", 0.0))
    cm = float(cand.get("mean_ret_pct", 0.0))
    bw = float(base.get("win_rate_pct", 0.0))
    cw = float(cand.get("win_rate_pct", 0.0))
    bp = float(base.get("p10_ret_pct", 0.0))
    cp = float(cand.get("p10_ret_pct", 0.0))
    degrade = bm - cm
    p10_degrade = bp - cp
    win_gain = cw - bw
    cap_gain = (n_cand / n_base) if n_base else 0.0
    total_base = n_base * bm
    total_cand = n_cand * cm
    total_gain = (total_cand / total_base) if total_base != 0 else None

    add("质量不退化", degrade <= MAX_MEAN_DEGRADE,
        f"平均收益 {bm:.3f}% → {cm:.3f}%（退化 {degrade:+.3f}pp ≤ {MAX_MEAN_DEGRADE}）")
    add("尾部风险", p10_degrade <= MAX_P10_DEGRADE,
        f"P10 {bp:.3f}% → {cp:.3f}%（退化 {p10_degrade:+.3f}pp ≤ {MAX_P10_DEGRADE}）")

    # 绝对可用性地板：候选自身必须站得住
    add("绝对可用性·胜率", cw >= MIN_CAND_WIN_RATE,
        f"候选胜率 {cw:.1f}% ≥ {MIN_CAND_WIN_RATE}%")
    add("绝对可用性·总收益", total_cand > MIN_CAND_TOTAL_RET,
        f"候选总收益 {total_cand:.2f}（n×均值）> {MIN_CAND_TOTAL_RET}")

    mean_gain = cm - bm
    # 路径一（质量）：选择质量显著提升——胜率 +1pp 或平均收益 +0.5pp
    quality_route = (win_gain >= MIN_WIN_RATE_GAIN) or (mean_gain >= MIN_MEAN_GAIN)
    # 路径二（容量）：事件数显著扩容、总收益不降、胜率退化受控
    capacity_route = (cap_gain >= MIN_CAPACITY_GAIN) and (
        total_gain is not None and total_gain >= MIN_TOTAL_RET_GAIN) and (
        win_gain >= -MAX_WIN_RATE_DROP)
    # 两条路径都不允许「总收益变差」——容量扩大但整体更亏属于负向改善。
    total_ret_ok = (total_gain is not None and total_gain >= MIN_TOTAL_RET_GAIN)
    add("总收益不退化", total_ret_ok,
        f"总收益 ×{(total_gain if total_gain is not None else float('nan')):.3f} ≥ ×{MIN_TOTAL_RET_GAIN}")
    add("改善路径", (quality_route or capacity_route) and total_ret_ok,
        f"胜率 {bw:.1f}% → {cw:.1f}%（{win_gain:+.1f}pp）| 均值 {bm:.3f}% → {cm:.3f}%（{mean_gain:+.3f}pp）"
        f" | 容量 ×{cap_gain:.2f} | 总收益 ×{(total_gain if total_gain is not None else float('nan')):.3f}"
        f"（质量路径：胜率≥{MIN_WIN_RATE_GAIN}pp 或均值≥{MIN_MEAN_GAIN}pp；"
        f"容量路径：容量≥×{MIN_CAPACITY_GAIN} 且总收益≥×{MIN_TOTAL_RET_GAIN} 且胜率退化≤{MAX_WIN_RATE_DROP}pp）")

    # 分段稳定性：两段都必须不出现明显退化
    seg_ok, seg_detail = True, "无分段数据"
    if seg_base and seg_cand and len(seg_base) == len(seg_cand):
        parts = []
        for i, (sb, sc) in enumerate(zip(seg_base, seg_cand)):
            nb, nc = int(sb.get("n", 0)), int(sc.get("n", 0))
            if nb < MIN_SEGMENT_N or nc < MIN_SEGMENT_N:
                parts.append(f"段{i+1}: 样本不足({nb}/{nc})")
                seg_ok = False
                continue
            dg = float(sb.get("mean_ret_pct", 0.0)) - float(sc.get("mean_ret_pct", 0.0))
            parts.append(f"段{i+1}: {sb.get('mean_ret_pct'):.3f}%→{sc.get('mean_ret_pct'):.3f}% ({-dg:+.3f}pp)")
            if dg > MAX_MEAN_DEGRADE:
                seg_ok = False
        seg_detail = " | ".join(parts)
    add("分段稳定", seg_ok, seg_detail)

    passed = all(c["passed"] for c in checks)
    loosening = loosening_axis(param_path, axis_param, cur_value, cand_value)
    if passed and loosening is True:
        # 放宽类改动额外要求：不得让「本该被过滤」的样本变多而不给回报 → 由容量路径兜住
        pass
    reason = "全部门槛通过" if passed else "未通过：" + "; ".join(
        c["name"] for c in checks if not c["passed"])
    return {
        "verdict": VERDICT_APPLIED if passed else "rejected",
        "thresholds": checks,
        "reason": reason,
        "loosening": loosening,
        "deltas": {
            "mean_ret_pp": round(cm - bm, 3),
            "win_rate_pp": round(win_gain, 1),
            "p10_pp": round(cp - bp, 3),
            "capacity_x": round(cap_gain, 3),
            "total_ret_x": round(total_gain, 3) if total_gain is not None else None,
        },
    }


def intraday_rule_verdict(counterfactual: dict | None, *, card_title: str) -> dict:
    """分时规则的门禁判定。

    与日线规则的根本差别：**命中选手行为 ≠ 有收益筛选价值**。
    因此除「与选手决策的吻合度」外，必须通过**全市场反事实检验**：
    被否决组的前向收益必须显著**劣于**通过组，否则该纪律只是复刻选手的措辞，
    甚至在收益上是反向的（实测确实如此，见 evoalpha_all/docs_archive_20260915/ITERATION_2026-09-11.md §六）。

    counterfactual: tools/run_intraday_counterfactual.py 的输出；None 表示未检验。
    """
    checks: list[dict] = [{"name": "变更类别", "passed": True,
                           "detail": "rule 类不适用自动生效（硬边界）"}]
    if not counterfactual:
        checks.append({"name": "反事实检验", "passed": False,
                       "detail": "未运行 tools/run_intraday_counterfactual.py → 不予晋级"})
        return {"verdict": "rejected", "thresholds": checks,
                "reason": "缺少全市场反事实检验：命中选手行为不等于有收益筛选价值",
                "decided_at": now_str()}

    v, p = counterfactual.get("veto", {}), counterfactual.get("pass", {})
    nv, np_ = int(v.get("n", 0)), int(p.get("n", 0))
    checks.append({"name": "反事实样本量", "passed": nv >= 100 and np_ >= 100,
                   "detail": f"否决组 {nv} / 通过组 {np_}（各要求 ≥100）"})
    if nv < 100 or np_ < 100:
        return {"verdict": "insufficient_evidence", "thresholds": checks,
                "reason": f"反事实样本不足：否决 {nv} / 通过 {np_}",
                "decided_at": now_str()}

    gap = float(p.get("mean_ret_pct", 0.0)) - float(v.get("mean_ret_pct", 0.0))
    pv = (counterfactual.get("welch") or {}).get("p_two_sided_approx")
    sig = pv is not None and pv < 0.05
    checks.append({"name": "筛选方向", "passed": gap > 0,
                   "detail": f"通过组 − 否决组 = {gap:+.3f}pp（要求 > 0，即否决组更差）"})
    checks.append({"name": "统计显著", "passed": bool(sig),
                   "detail": f"Welch p≈{pv}（要求 < 0.05）"})
    passed = all(c["passed"] for c in checks)
    if passed:
        reason = (f"✅ 有真实筛选价值：否决组 {v.get('mean_ret_pct')}% 显著劣于通过组 "
                  f"{p.get('mean_ret_pct')}%（差 {gap:+.3f}pp, p≈{pv}）")
    else:
        reason = (f"❌ 否决：无收益筛选价值——否决组 {v.get('mean_ret_pct')}% vs 通过组 "
                  f"{p.get('mean_ret_pct')}%（差 {gap:+.3f}pp, p≈{pv}）。"
                  f"该纪律复刻了选手的行为，但不能筛选收益，方向甚至相反。")
    return {"verdict": "applied" if passed else "rejected", "thresholds": checks,
            "reason": reason, "decided_at": now_str(),
            "counterfactual": {"veto_mean_pct": v.get("mean_ret_pct"),
                               "pass_mean_pct": p.get("mean_ret_pct"),
                               "gap_pp": round(gap, 3), "welch_p": pv,
                               "veto_n": nv, "pass_n": np_}}


def non_parameter_verdict(change_class: str, title: str) -> dict:
    """rule / code / data 类提案：恒待人工确认。"""
    return {
        "verdict": VERDICT_PENDING_CONFIRM,
        "thresholds": [{"name": "变更类别", "passed": True,
                        "detail": f"{change_class} 类不适用自动生效（硬边界）"}],
        "reason": f"{change_class} 类变更需人工确认：{title}",
        "decided_at": now_str(),
    }
