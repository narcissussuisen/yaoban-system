# -*- coding: utf-8 -*-
"""R1.6 交付物：从 `src/core/decision_digest.py` 的契约常量导出 schema 与人读文档。

单一事实源：`FIELDS` / `IMPLEMENTATION_HASH_FIELDS` / `EXECUTION_CONTEXT_FIELDS` / `LAYER_RULES`
全部定义在 `src/core/decision_digest.py`。本工具只做导出 + 校验 + 生成样例，**不重复定义契约**。

产物：
  yaoban-system/persona/decision_digest_schema_v1.toml  字段契约（机器可读）
  yaoban-system/persona/_decision_digest_sample_v1.json 一个真实形状的样例 digest
  docs/DECISION_DIGEST_CONTRACT.md                       契约文档（人读）

校验（缺一即 ERR）：
  ① 每个字段的 layer 合法、required 字段有 note
  ② IMPLEMENTATION_HASH_FIELDS / EXECUTION_CONTEXT_FIELDS 的每个键都必须在 FIELDS 里存在
  ③ 两个集合不相交，且并集 + outcome.* + 派生值 = 全体字段（**三层必须完整无遗漏**）
  ④ LAYER_RULES 的 id 唯一，且同时覆盖 implementation 与 effectiveness 两层
  ⑤ 样例 digest 能通过 `validate()`
"""

import json
import pathlib
import sys
import tomllib
import traceback

ROOT = pathlib.Path(r"C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\EvoAlpha")
YS = ROOT / "yaoban-system"
sys.path.insert(0, str(YS / "src"))

from core.decision_digest import (  # noqa: E402
    EXECUTION_CONTEXT_FIELDS, FIELDS, IMPLEMENTATION_HASH_FIELDS, LAYER_RULES,
    SCHEMA_VERSION, build_digest, validate,
)

OUT_SCHEMA = YS / "persona/decision_digest_schema_v1.toml"
OUT_SAMPLE = YS / "persona/_decision_digest_sample_v1.json"
OUT_MD = ROOT / "docs/DECISION_DIGEST_CONTRACT.md"
OUT_REPORT = YS / "persona/_build_decision_digest.report.txt"

LAYERS = ("identity", "implementation", "effectiveness")
DERIVED = ("digest_id", "digest_revision", "replay_hash")


def tv(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(tv(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f'"{k}" = {tv(x)}' for k, x in v.items()) + "}"
    if v is None:
        return '""'
    s = (str(v).replace("\\", "\\\\").replace('"', '\\"')
         .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return f'"{s}"'


def main():
    log = []
    lw = log.append
    errs, warns = [], []

    paths = [f["path"] for f in FIELDS]
    if len(paths) != len(set(paths)):
        dup = [p for p in paths if paths.count(p) > 1]
        errs.append(f"字段 path 重复：{sorted(set(dup))}")

    # ① layer / note
    for f in FIELDS:
        if f["layer"] not in LAYERS:
            errs.append(f"{f['path']}: 非法 layer {f['layer']}")
        if f.get("required") and not f.get("note"):
            errs.append(f"{f['path']}: required 字段缺 note（契约必须自解释）")
        if f["type"] == "enum" and not f.get("values"):
            errs.append(f"{f['path']}: enum 字段缺 values")

    # ② 集合成员必须存在于 FIELDS
    known = set(paths)
    for name, coll in (("IMPLEMENTATION_HASH_FIELDS", IMPLEMENTATION_HASH_FIELDS),
                       ("EXECUTION_CONTEXT_FIELDS", EXECUTION_CONTEXT_FIELDS)):
        for k in coll:
            if k not in known:
                errs.append(f"{name} 含不存在的字段 {k}")

    # ③ 三层完整覆盖
    h = set(IMPLEMENTATION_HASH_FIELDS)
    e = set(EXECUTION_CONTEXT_FIELDS)
    if h & e:
        errs.append(f"进哈希与执行环境集合相交：{sorted(h & e)}")
    eff = {f["path"] for f in FIELDS if f["layer"] == "effectiveness"}
    ident = {f["path"] for f in FIELDS if f["layer"] == "identity"}
    classified = h | e | eff | ident
    unclassified = known - classified
    if unclassified:
        errs.append(f"未归类字段（三层必须完整）：{sorted(unclassified)}")
    for d in DERIVED:
        if d in h or d in e:
            errs.append(f"派生值 {d} 不得进入任一分类集合")

    # ④ LAYER_RULES
    rids = [r["id"] for r in LAYER_RULES]
    if len(rids) != len(set(rids)):
        errs.append("LAYER_RULES id 重复")
    rl = {r["layer"] for r in LAYER_RULES}
    for must in ("implementation", "effectiveness"):
        if must not in rl:
            errs.append(f"LAYER_RULES 未覆盖 {must} 层")

    # ⑤ 样例
    sample = build_digest(
        decision_id="dec-tick-20260911-300468",
        day="2026-09-11", sym="300468", side="sell",
        signal_ts="2026-09-11 09:41:08", decision_ts="2026-09-11 09:41:08",
        recorded_at="2026-09-11 09:41:09",
        candidate_snapshot_id="", market_snapshot_hash="a" * 64,
        sop_version_id="v0", params_hash="b" * 64,
        rules_fired=["GEN-HOLD-22", "P1-HOLD-01", "GEN-HOLD-03"],
        discretions=[dict(point_id="D7", output="logic_invalidated",
                          rationale="有效跌破 20 日线（收盘破 + 次日未收回）→ 买入逻辑消失",
                          model="deepseek-v4-flash", prompt_sha256="c" * 64,
                          cli_version="n/a")],
        risk_gate=dict(passed=True, veto_reason="",
                       checks=["single_stock_pct", "portfolio_floor", "stop_px"]),
        order_intent=dict(side="sell", qty=1800, px_limit=23.30,
                          reason="stop_loss(px<=stop_px) → GEN-HOLD-22",
                          plan_ref="plan-20260911"),
        executed=True, kind="fill", authority="account.fills",
        fill_ref="300468|2026-09-11T09:41:09|1800|23.31",
        narrative_refs=["MEM-005", "MEM-002"], seq=2,
        pnl_attributable=-1930.52, effectiveness_basis="v0-seg-1（start_date=2026-09-14 之前无，留空）",
    )
    verrs = validate(sample)
    errs.extend(f"样例 validate: {x}" for x in verrs)

    # ── 输出 schema TOML
    with OUT_SCHEMA.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# EvoAlpha · decision_digest 字段契约 v1（R1.6）\n")
        fh.write("# 生成器 tools/build_decision_digest_schema.py；单一事实源 src/core/decision_digest.py\n")
        fh.write("# 用途：供 R5.0 把「实现层」与「有效性层」机械分开（见 docs/DECISION_DIGEST_CONTRACT.md）\n\n")
        fh.write("[meta]\n")
        fh.write(f'version = "{SCHEMA_VERSION}"\n')
        fh.write('built_at = "2026-09-12"\n')
        fh.write(f'field_count = {len(FIELDS)}\n')
        fh.write(f'hash_field_count = {len(IMPLEMENTATION_HASH_FIELDS)}\n')
        fh.write(f'exec_context_field_count = {len(EXECUTION_CONTEXT_FIELDS)}\n')
        fh.write(f'layer_rule_count = {len(LAYER_RULES)}\n')
        fh.write('purpose = "一次决策的不可变结构化摘要 + 内容哈希；'
                 '覆盖 inputs_snapshot → rules_fired → discretions → risk_gate → order_intent → fill|not_executed"\n')
        fh.write('core_insight = "replay_hash 不变 = 纯实现层改动（可直接晋级）；'
                 'replay_hash 变化 = 行为改动（必须进 60 日有效性队列）"\n\n')
        fh.write("[layers]\n")
        fh.write('identity = "标识与溯源，不参与行为判定"\n')
        fh.write('implementation = "实现层：可机械判定，不需要净值"\n')
        fh.write('effectiveness = "有效性层：只能由 R5.1 净值判定器解释；digest 只携带指针"\n\n')
        fh.write("[hash_policy]\n")
        fh.write("implementation_hash_fields = " + tv(IMPLEMENTATION_HASH_FIELDS) + "\n")
        fh.write("execution_context_fields = " + tv(EXECUTION_CONTEXT_FIELDS) + "\n")
        fh.write('excluded_note = "结果(outcome.*)、派生值(digest_id/replay_hash/digest_revision) 一律不进哈希"\n\n')
        for f in FIELDS:
            fh.write("[[field]]\n")
            for k in ("path", "type", "layer", "required", "fmt", "values", "fields", "note"):
                if k in f:
                    fh.write(f"{k} = {tv(f[k])}\n")
            fh.write("\n")
        for r in LAYER_RULES:
            fh.write("[[layer_rule]]\n")
            for k in ("id", "layer", "rule", "meaning", "verdict"):
                fh.write(f"{k} = {tv(r[k])}\n")
            fh.write("\n")

    OUT_SAMPLE.write_text(json.dumps(sample, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 报告
    lw("=" * 84)
    lw("R1.6 decision_digest 契约 构建报告")
    lw("=" * 84)
    lw(f"schema 版本         : {SCHEMA_VERSION}")
    lw(f"字段总数            : {len(FIELDS)}")
    for L in LAYERS:
        lw(f"  layer={L:<16}: {sum(1 for f in FIELDS if f['layer']==L)}")
    lw(f"进内容哈希的字段    : {len(IMPLEMENTATION_HASH_FIELDS)}")
    lw(f"执行环境（不进哈希）: {len(EXECUTION_CONTEXT_FIELDS)}")
    lw(f"分层判定规则        : {len(LAYER_RULES)}"
       f"（implementation {sum(1 for r in LAYER_RULES if r['layer']=='implementation')} / "
       f"effectiveness {sum(1 for r in LAYER_RULES if r['layer']=='effectiveness')}）")
    lw(f"样例 digest_id      : {sample['digest_id']}")
    lw(f"样例 replay_hash    : {sample['replay_hash']}")
    lw(f"ERR {len(errs)} / WARN {len(warns)}")
    for x in errs:
        lw(f"  ERR  {x}")
    for x in warns:
        lw(f"  WARN {x}")
    lw("")
    lw("字段清单（path | layer | required）：")
    for f in FIELDS:
        lw(f"  {f['path']:<34} {f['layer']:<16} {'必填' if f.get('required') else '可选'}")
    OUT_REPORT.write_text("\n".join(log), encoding="utf-8")

    write_md(sample)

    # ── 自校验
    tomllib.loads(OUT_SCHEMA.read_text(encoding="utf-8"))
    json.loads(OUT_SAMPLE.read_text(encoding="utf-8"))
    if OUT_MD.stat().st_size < 4000:
        raise SystemExit("FAIL: DECISION_DIGEST_CONTRACT.md 过小")
    print(f"selfcheck: schema TOML 回读 OK / sample JSON OK / md={OUT_MD.stat().st_size}B")
    print(f"OK fields={len(FIELDS)} hash={len(IMPLEMENTATION_HASH_FIELDS)} "
          f"exec_ctx={len(EXECUTION_CONTEXT_FIELDS)} rules={len(LAYER_RULES)} err={len(errs)}")
    for x in errs:
        print("  ERR ", x)
    return 1 if errs else 0


def write_md(sample):
    L = []
    a = L.append
    a("# decision_digest 契约 v1")
    a("")
    a("> **自动生成**，请勿手改。生成器 `yaoban-system/tools/build_decision_digest_schema.py`；")
    a("> 契约的**单一事实源**是 `yaoban-system/src/core/decision_digest.py`（常量 `FIELDS` / "
      "`IMPLEMENTATION_HASH_FIELDS` / `EXECUTION_CONTEXT_FIELDS` / `LAYER_RULES`）；")
    a("> 机器可读：`persona/decision_digest_schema_v1.toml`；样例：`persona/_decision_digest_sample_v1.json`。")
    a("")
    a("## 一、它解决什么问题")
    a("")
    a("R5.0 要求把「**实现层**」与「**有效性层**」分开判：")
    a("")
    a("| 层 | 问题 | 判据 | 需要净值吗 |")
    a("|---|---|---|---|")
    a("| **实现层** | 代码有没有按 SOP 规格执行？ | 6 条**机械**规则（见 §三） | **不需要** |")
    a("| **有效性层** | 赚钱了吗？ | 只能由 R5.1 净值判定器（vs 双基准臂，60 日） | 需要 |")
    a("")
    a("⭐ **核心洞察**：两者的分界可以**机械判定** ——")
    a("")
    a("> **`replay_hash` 不变 = 纯实现层改动（refactor 不改行为）→ 可直接晋级；**")
    a("> **`replay_hash` 变化 = 行为改动 → 必须进 60 日有效性队列。**")
    a("")
    a("这把「这次改动到底是不是只动了实现」从主观判断变成了一次哈希比对。")
    a("")
    a("## 二、digest 覆盖的链条")
    a("")
    a("```")
    a("inputs_snapshot → rules_fired → discretions → risk_gate → order_intent → fill | not_executed")
    a("```")
    a("")
    a("### 字段契约（三层分类）")
    a("")
    a("| 字段 | 类型 | 层 | 必填 | 说明 |")
    a("|---|---|---|---|---|")
    for f in FIELDS:
        t = f["type"] + (f"({','.join(f['values'])})" if f["type"] == "enum" else "")
        note = str(f.get("note", "—")).replace("|", "\\|")
        a(f"| `{f['path']}` | {t} | **{f['layer']}** | {'✅' if f.get('required') else '—'} | {note} |")
    a("")
    a("## 三、分层判定规则（R5.0 的机械分判据）")
    a("")
    a("| 规则 | 层 | 判据 | 含义 | 结论 |")
    a("|---|---|---|---|---|")
    for r in LAYER_RULES:
        a(f"| **{r['id']}** | {r['layer']} | `{r['rule']}` | {r['meaning']} | {r['verdict']} |")
    a("")
    a("## 四、⭐ 哈希的敏感性边界（本契约最关键的裁定）")
    a("")
    a("`replay_hash` 对决策内容取 canonical sha256。**三分法**：")
    a("")
    a("| 类别 | 字段 | 进哈希？ | 理由 |")
    a("|---|---|---|---|")
    a("| **决策内容** | `day/sym/side` · `inputs.*` · `decision.*` · `action.order_intent` | ✅ | "
      "「决定了什么」 |")
    a("| **执行环境** | `timing.*` · `action.executed/kind/authority/fill_ref` | ❌ | "
      "「何时、走哪条渠道执行」—— 不是决策本身 |")
    a("| **结果** | `outcome.*` | ❌ | 「赚了多少」—— 绝不能反过来定义「决策是什么」 |")
    a("")
    a("**为什么 `kind`/`authority` 也要排除**（容易被误改，测试已钉住）：")
    a("")
    a("它们描述「这条 digest 代表真实成交 / 审计重建 / 影子」，即**执行渠道**。")
    a("若纳入哈希，则 **R3 影子盘 → R4 实盘的同一个决策**会得到两个不同 fingerprint，")
    a("R5.0 会把这次「渠道切换」误判为**行为改动**、对每一天都触发 60 日评审 → **判据失效**。")
    a("")
    a("> 身份用 `digest_id`（含当日 seq），内容比对用 `replay_hash` —— 两者职责分离。")
    a("")
    a("**进哈希的字段**（{} 个）：".format(len(IMPLEMENTATION_HASH_FIELDS)))
    a("")
    for f in IMPLEMENTATION_HASH_FIELDS:
        a(f"- `{f}`")
    a("")
    a("**明确不进哈希的执行环境字段**（{} 个）：".format(len(EXECUTION_CONTEXT_FIELDS)))
    a("")
    for f in EXECUTION_CONTEXT_FIELDS:
        a(f"- `{f}`")
    a("")
    a("## 五、与既有契约的关系（不重复造轮子）")
    a("")
    a("| 本契约的字段 | 沿用自 |")
    a("|---|---|")
    a("| `timing.signal_ts/decision_ts/recorded_at` + 120s 新鲜度 | R0.3 `portfolio/timing_contract.py` |")
    a("| `decision_id` 命名（`dec-tick-{day}-{sym}` / `dec-auto-{ts}-{hash}`）与登记 | R0.4 `portfolio/ledger.py` |")
    a("| `action.kind/executed/authority` | `docs/SELL_EXECUTION_CONTRACT.md §4`（防审计重建被误当成交、防双重计算） |")
    a("| `inputs.sop_version_id` / `inputs.params_hash` | R1.5 `persona/versions/<v>.toml`（frozen 组） |")
    a("| `decision.rules_fired` | R1.1 `persona/sop_v0.toml`（150 条规则 id） |")
    a("| `decision.discretions[].point_id/output` | R1.2 `persona/discretion_v0.toml`（D1–D10 + 输出枚举） |")
    a("| `decision.narrative_refs` | R1.4 `persona/memory/memory_v0.toml`（MEM-* id） |")
    a("| `decision.risk_gate` | 风控 veto（不可被裁量绕过） |")
    a("")
    a("## 六、样例 digest")
    a("")
    a("```json")
    a(json.dumps(sample, ensure_ascii=False, indent=1))
    a("```")
    a("")
    a("## 七、零生产写入说明")
    a("")
    a("`src/core/decision_digest.py` **尚未被任何生产脚本 import** —— 本步只定义契约并提供可测的参考实现。")
    a("接线属 **R3.1**（六段决策环引擎）与 **R4.1**（盘中决策服务）。")
    a("")
    a("测试：`tests/test_decision_digest.py`（28 项，覆盖哈希敏感性边界的正反两面）。")
    a("")
    OUT_MD.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
        OUT_REPORT.write_text("!!! EXCEPTION !!!\n" + traceback.format_exc(), encoding="utf-8")
        raise
