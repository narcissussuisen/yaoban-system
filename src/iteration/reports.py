"""人读摘要：把一次批次的结论压成一页，供人工在次日开工前 2 分钟读完。"""
from __future__ import annotations

from .model import Proposal

CAP_HINT = 3000      # 与 daily_iteration.py 的 --market-cap 默认值一致（超出则抽样）


def _badge(status: str) -> str:
    return {
        "applied": "✅ 过门槛生效",
        "rejected": "❌ 否决",
        "insufficient_evidence": "⚠️ 证据不足",
        "pending_confirm": "🖐 待你确认",
    }.get(status, status)


def _short_target(target: dict) -> str:
    if not target:
        return "—"
    parts = []
    for k, v in target.items():
        if isinstance(v, dict) and "from" in v:
            f, t = _disp(v["from"]), _disp(v["to"])
            parts.append(f"`{k.split('.')[-1]}` {f} → {t}")
        else:
            parts.append(f"`{k}`")
    return "；".join(parts)


def _disp(v) -> str:
    """参数值显示：0.0 上限类参数代表「不启用」，None 代表「不限制」。"""
    if v is None:
        return "不限制"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)) and float(v) == 0.0:
        return "关闭"
    return f"{v:g}" if isinstance(v, float) else str(v)


def render_digest(*, date: str, run_id: str, scan: dict, cs: dict, prof: dict,
                  pdiff: dict, sh: dict, props: list[Proposal], span: dict,
                  notes: list[str], case: dict, tdays: int, split: str,
                  latest_bar: str | None) -> str:
    L: list[str] = []
    L.append(f"# 每日自迭代批 · {date}")
    L.append("")
    L.append(f"> run_id `{run_id}` ｜ 状态：资料解析 → 知识卡片 → 画像 → 提案 → 影子回归 全链完成")
    L.append(f"> 行情最新 bar：`{latest_bar}` ｜ 回测窗口：{tdays} 交易日（分段点 `{split}`）")
    L.append("")

    # ---- 一、结论先行 ----
    applied = [p for p in props if p.status == "applied"]
    pending = [p for p in props if p.status == "pending_confirm"]
    insuff = [p for p in props if p.status == "insufficient_evidence"]
    rejected = [p for p in props if p.status == "rejected"]
    L += ["## 一、结论先行", ""]
    L.append(f"- **参数类自动生效**：{len(applied)} 条"
             + ("（见台账 `outputs/iteration/ledger.jsonl`，补丁预览 `parameter_patch.json`）"
                if applied else "（无）"))
    L.append(f"- **待你确认**：{len(pending)} 条（规则/代码/数据类，永不自动生效）")
    L.append(f"- **证据不足**：{len(insuff)} 条（样本量未达门槛，不作为结论）")
    L.append(f"- **否决**：{len(rejected)} 条")
    if not applied:
        if insuff and len(insuff) == len(props) - len(pending) and len(props) > len(pending):
            L.append("- ⚠️ 本批**无法判定**任何参数改动：全部候选因样本量/窗口未达门槛被判"
                     "「证据不足」。这不等于「现状最优」——是**样本不足以下结论**，"
                     "需先扩样本（更长历史或更多标的）。")
        else:
            L.append("- ⚠️ 本批**没有**任何参数达到自动生效门槛：现有阈值在回测窗口内未被证伪，"
                     "维持现状即最优选择之一。")
    L.append("")

    # ---- 二、资料与卡片 ----
    L += ["## 二、资料解析与知识卡片", ""]
    L.append(f"- 资料扫描：{scan['file_count']} 个文件"
             + (f"，新增 **{len(scan['new'])}**" if scan.get("new") else "，无新增")
             + (f"，变更 {len(scan['changed'])}" if scan.get("changed") else ""))
    if scan.get("baseline"):
        L.append("- 注：本次为首次基线扫描，全部文件计为新增。")
    L.append(f"- 知识卡片：**{cs['total']}** 张（本批新增 {len(pdiff.get('added', []))}，"
             f"变更 {len(pdiff.get('changed', []))}，移除 {len(pdiff.get('removed', []))}）")
    L.append(f"- 可测试性分布：{cs['by_testability']}")
    L.append(f"- 未量化（阻塞自动化）：{len(cs['unquantified'])} 张 → "
             f"{', '.join(cs['unquantified']) or '—'}")
    L.append("")
    L.append("| 类别 | 张数 |")
    L.append("|---|---|")
    for k, v in cs["by_category"].items():
        L.append(f"| {k} | {v} |")
    L.append("")

    # ---- 三、画像 ----
    L += ["## 三、选手画像（机读版）", ""]
    L.append(f"- 截至 {prof['as_of']}：{prof['card_total']} 张卡片，"
             f"{len(prof['categories'])} 个类别")
    if pdiff.get("baseline"):
        L.append("- 首次生成机读画像（无上一版可比）。")
    else:
        L.append(f"- 相对上一版：新增 {pdiff.get('added') or '—'}；"
                 f"变更 {pdiff.get('changed') or '—'}；移除 {pdiff.get('removed') or '—'}")
    L.append(f"- 叙述型画像仍以 `选手学习资料/选手战法画像-累计.md` 为准（人工维护）。")
    L.append("")

    # ---- 四、影子回归 ----
    L += ["## 四、影子回归", ""]
    L.append("> 案例归因回归校验「规则能否复现选手实际动作」；市场影子盘检验"
             "「规则独立运行是否成立」。两者口径不同，禁止合并解读。")
    L.append("")
    L.append("| 规则 | 类型 | 案例召回 | 误杀 | 市场事件数 | 均值收益 | 胜率 | 10分位 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for name, block in sh["rules"].items():
        best = None
        for v in block["variants"]:
            if best is None:
                best = v
                continue
            if (v.get("recall_pct") or -1) > (best.get("recall_pct") or -1):
                best = v
        base_m = block["market"][0] if block.get("market") else {}
        n_base = int(base_m.get("n", 0))
        cap_note = "（截断）" if n_base >= CAP_HINT else ""
        L.append(f"| {block['label']} | {block['kind']} | "
                 f"{(best or {}).get('recall_pct')}%（{(best or {}).get('hits')}/{(best or {}).get('cases')}） | "
                 f"{(best or {}).get('false_kill_n')} | {n_base}{cap_note} | "
                 f"{base_m.get('mean_ret_pct')}% | {base_m.get('win_rate_pct')}% | "
                 f"{base_m.get('p10_ret_pct')}% |")
    L.append("")
    L.append("（表中「案例召回」取该规则族参数网格中的最好值；市场列为当前配置值。"
             f"事件数标注「截断」= 命中上限 {CAP_HINT} 后的抽样，指标为样本估计而非全量。）")
    # 样本饥饿告警：基准事件数不足门槛时，该规则的参数结论不可用
    starved = [(name, blk) for name, blk in sh["rules"].items()
               if blk["kind"] == "entry" and blk.get("market")
               and int(blk["market"][0].get("n", 0)) < 30]
    if starved:
        L.append("")
        L.append("⚠️ **样本饥饿**：以下规则在当前配置下事件数 < 30，其参数结论不成立，"
                 "需先修规则口径或扩样本：")
        for name, blk in starved:
            L.append(f"- {blk['label']}：基准事件 n={blk['market'][0].get('n')}"
                     f"（{blk['market'][0].get('date_range')}）")
    L.append("")

    # 案例召回明细
    for name, block in sh["rules"].items():
        if block["kind"] != "entry":
            continue
        best = max(block["variants"], key=lambda v: (v.get("recall_pct") or -1))
        if best.get("misses"):
            L.append(f"**{block['label']} 未命中样本（最好组合 {best['params']}）**")
            for m in best["misses"]:
                L.append(f"- {m.get('name') or m.get('code')}：{m.get('why')}"
                         + (f" — {m.get('attribution')}" if m.get("attribution") else ""))
            L.append("")

    # 影子盘：规则独立运行是否成立（与门禁的单轴推断是两个问题）
    if sh.get("portfolio"):
        L += ["", "### 影子盘（当前配置，独立运行）", "",
              "> 次日开盘等权买入、双边成本 0.1%、最多 4 仓，出场固定为"
              "「收盘破 MA10 / −5% 止损 / 5 日时间止损」。这是**基线口径的诚实上界估计**，"
              "不含分时入场过滤（该纪律尚未接入）。", "",
              "| 规则 | 区间 | 总收益 | 年化 | 最大回撤 | 成交 | 胜率 | 单笔均值 | 平均持有 |",
              "|---|---|---|---|---|---|---|---|---|"]
        for name, blk in sh["portfolio"].items():
            if not blk or blk.get("error"):
                L.append(f"| {name} | — | — | — | — | — | — | — | {blk.get('error') if blk else '无数据'} |")
                continue
            L.append(f"| {name} | {blk.get('start')}~{blk.get('end')} "
                     f"| {blk.get('total_return_pct')}% | {blk.get('annualized_pct')}% "
                     f"| {blk.get('max_drawdown_pct')}% | {blk.get('trades')} "
                     f"| {blk.get('win_rate_pct')}% | {blk.get('mean_trade_ret_pct')}% "
                     f"| {blk.get('avg_hold_days')}d |")
        L.append("")
        L.append("⚠️ 影子盘**不是**策略绩效归因：它只跑「入场规则 + 硬出场」的组合，"
                 "不含票型分档出场、不含资金调度、不含盘中过滤。**不得**当作策略上线依据。")

    # ---- 四之二、分时规则层 ----
    idr = sh.get("intraday")
    if idr and idr.get("baseline"):
        b = idr["baseline"]
        rows = [r for r in b.get("rows", []) if r.get("action") != "UNKNOWN"]
        unk = [r for r in b.get("rows", []) if r.get("action") == "UNKNOWN"]
        fp = idr.get("frozen_params", {})
        ws = fp.get("window_start") or "09:30"
        L += ["", "### 分时规则层（分钟数据已接入）", "",
              f"> 冻结口径（{b['days']} 个交易日、{b['scored']} 条明示决策的网格搜索选出，"
              f"后续不再调参）：决策窗口 **{ws}–{fp.get('window_end')}**、"
              f"连续 {fp.get('confirm_minutes')} 分钟收盘低于均价线×(1−{fp.get('tol')}) → 弃。", "",
              f"**准确率 {b.get('accuracy_pct')}%**（{b['veto_correct'] + b['buy_correct']}/{b['scored']}）："
              f"明示否决命中 {b['veto_correct']}、漏否决 {b['veto_missed']}、"
              f"误否决 {b['false_veto']}、买入存活 {b['buy_correct']}"
              + (f"；另有 {b['unknown_excluded']} 条未说明原因的未执行，不计分。" if unk else "。"), "",
              "| 日期 | 标的 | 选手动作 | 规则否决 | 跌破时点 | 低于均价线 |",
              "|---|---|---|---|---|---|"]
        for r in rows:
            act = {"BUY": "✅ 买入", "VETO": "❌ 弃"}.get(r.get("action"), r.get("action"))
            L.append(f"| {r.get('date')} | {r.get('name')}({r.get('code')}) | {act} "
                     f"| {'是' if r.get('veto') else '否'} | {r.get('break_ts') or '—'} "
                     f"| {r.get('below_minutes')}/{r.get('window_minutes')} |")
        L.append("")
        L.append("⚠️ 样本为 7 个交易日的 18 条决策，且同属一段市场状态；"
                 "**全市场反事实检验**（`tools/run_intraday_counterfactual.py`）"
                 "回答「该纪律是否有真实筛选价值」，与案例吻合度是两个问题。")
        if unk:
            L.append("")
            L.append("未计分样本（未说明未执行原因，避免误判）：" +
                     "、".join(f"{r.get('name')}({r.get('date')})" for r in unk))

    # ---- 五、提案 ----
    L += ["## 五、提案", ""]
    if applied:
        L += ["### ✅ 参数类（已过门槛，待落盘确认）", ""]
        for p in applied:
            L.append(f"- **{p.proposal_id}** {p.title}")
            L.append(f"  - 依据：{p.rationale}")
            L.append(f"  - 门槛：{p.thresholds.get('reason')}")
            d = p.evidence.get("deltas") or {}
            L.append(f"  - 变化：均值 {d.get('mean_ret_pp')}pp / 胜率 {d.get('win_rate_pp')}pp / "
                     f"容量 ×{d.get('capacity_x')} / 总收益 ×{d.get('total_ret_x')}")
    if pending:
        L += ["", "### 🖐 待你确认（规则/代码/数据类）", ""]
        for p in pending:
            L.append(f"- **{p.proposal_id}** [{p.change_class}] {p.title}")
            L.append(f"  - {p.rationale}")
    if insuff:
        L += ["", "### ⚠️ 证据不足（不作结论）", ""]
        for p in insuff:
            L.append(f"- **{p.proposal_id}** {p.title}")
            L.append(f"  - {p.thresholds.get('reason')}")
    if rejected:
        L += ["", "### ❌ 已否决（未过门槛）", "",
              "| 提案 | 改动 | 基准 n/均值/胜率 | 候选 n/均值/胜率 | 容量 | 总收益 | 未过项 |",
              "|---|---|---|---|---|---|---|"]
        for p in rejected:
            ev = p.evidence or {}
            b, c = ev.get("base", {}), ev.get("candidate", {})
            d = ev.get("deltas") or {}
            tgt = p.target or {}
            ck = p.thresholds.get("checks", [])
            failed = [x["name"] for x in ck
                      if not x["passed"] and x["name"] != "改善路径"]
            if not failed:
                failed = [x["name"] for x in ck if not x["passed"]]
            L.append(
                f"| {p.proposal_id} | {_short_target(tgt)} "
                f"| {b.get('n')} / {b.get('mean_ret_pct')}% / {b.get('win_rate_pct')}% "
                f"| {c.get('n')} / {c.get('mean_ret_pct')}% / {c.get('win_rate_pct')}% "
                f"| ×{d.get('capacity_x')} | ×{d.get('total_ret_x')} "
                f"| {'、'.join(failed) or '—'} |")
    L.append("")

    # ---- 六、数据与边界 ----
    L += ["## 六、数据就绪与诚实边界", ""]
    L.append(f"- 行情覆盖：{span.get('codes_ok')} 只 / 中位 {span.get('rows_median')} 根 bar "
             f"（{span.get('start_min')} ~ {span.get('end_max')}）")
    L.append(f"- 案例表：实际交易 {len(case.get('entries', []))} 笔、清仓 "
             f"{len(case.get('exits', []))} 笔、公开候选 {len(case.get('candidates', []))} 条、"
             f"命中未成交 {len(case.get('screened_no_fill', []))} 条")
    for g in case.get("data_gaps", []):
        L.append(f"- 数据缺口：{g}")
    for n in notes:
        L.append(f"- {n}")
    L.append("- 本批**不写**账本、不改门禁、不改生产代码；参数改动仅出补丁预览。")
    L.append("")
    return "\n".join(L)
