# -*- coding: utf-8 -*-
"""decision_digest —— 一次决策的不可变结构化摘要 + 内容哈希（R1.6 契约）。

它是什么
--------
把「看到什么 → 触发哪些规则 → 裁量了什么 → 风控过没过 → 打算做什么 → 实际做了什么」
这一整条链，压成一个**不可变、可取哈希、可重放比对**的结构化对象。覆盖：

    inputs_snapshot → rules_fired → discretions → risk_gate → order_intent → fill | not_executed

它解决什么问题（供 R5.0 分层晋级）
---------------------------------
R5.0 要求把「实现层」与「有效性层」分开判：

| 层 | 问题 | 判据 | 需要净值吗 |
|---|---|---|---|
| **实现层** | 代码有没有按 SOP 规格执行？ | 6 条**机械**规则（见 `LAYER_RULES`） | **不需要** |
| **有效性层** | 赚钱了吗？ | 只能由 R5.1 净值判定器（vs 双基准臂，60 日） | 需要 |

⭐ **核心洞察**：实现层与行为层的分界可以**机械判定** ——
**`replay_hash` 不变 = 纯实现层改动（refactor 不改行为）→ 可直接晋级；
`replay_hash` 变化 = 行为改动 → 必须进 60 日有效性队列。**
这把「这次改动到底是不是只动了实现」从主观判断变成了一次哈希比对。

与既有契约的关系（不重复造轮子）
------------------------------
- 时序契约 `signal_ts ≤ decision_ts ≤ recorded_at` + 120s 新鲜度：沿用 R0.3（`portfolio/timing_contract.py`）
- `decision_id` 命名（`dec-tick-{day}-{sym}` / `dec-auto-{ts}-{hash}`）与登记：沿用 R0.4（`portfolio/ledger.py`）
- `kind` / `executed` / `authority` 三键：沿用 `docs/SELL_EXECUTION_CONTRACT.md §4`（防审计重建被误当成交）
- `sop_version_id` / `params_hash`：取自 R1.5 的 frozen 组（`persona/versions/<v>.toml`）
- `rules_fired` / `discretions[].point_id`：取自 R1.1（`sop_v0.toml`）/ R1.2（`discretion_v0.toml`）
- `narrative_refs`：取自 R1.4（`memory_v0.toml` 的 entry id / 自述字段）

零生产写入
----------
本模块**尚未被任何生产脚本 import**；它只定义契约并提供可测的参考实现。
接线属 R3.1（六段决策环引擎）与 R4.1（盘中决策服务）。
"""

from __future__ import annotations

import hashlib
import json
import re

SCHEMA_VERSION = "decision-digest/v1"

# ══════════════════════════════════════════════════════════════════════
# 字段契约（单一事实源：生成器 tools/build_decision_digest_schema.py 从这里导出 TOML/MD）
# layer ∈ identity | implementation | effectiveness
#   identity      —— 标识与溯源，不参与行为判定
#   implementation—— 实现层判定用（可机械判定，不需净值）
#   effectiveness —— 有效性层用（只能由 R5.1 净值判定器解释；digest 只携带指针）
# ══════════════════════════════════════════════════════════════════════
FIELDS = [
    # ── identity
    dict(path="digest_id", type="str", layer="identity", required=True,
         fmt="dd-{day}-{seq}-{hash8}",
         note="本 digest 的稳定标识；seq 为当日序号，hash8 = replay_hash 前 8 位"),
    dict(path="digest_revision", type="str", layer="identity", required=True,
         fmt=SCHEMA_VERSION, note="schema 版本；跨版本比对必须显式声明"),
    dict(path="decision_id", type="str", layer="identity", required=True,
         note="**必须与 ledger 的 decision_id 一致**（R0.4）；ledger 侧空值即拒单"),
    dict(path="day", type="date", layer="identity", required=True,
         note="交易日（YYYY-MM-DD）；同一 digest 的 day 必须与 timing.decision_ts 的日期一致"),
    dict(path="sym", type="str", layer="identity", required=True,
         note="标的代码（6 位数字）；与 ledger fill 的 sym 必须完全一致"),
    dict(path="side", type="enum", layer="identity", required=True,
         values=["buy", "sell", "hold", "skip"],
         note="hold/skip 也必须有 digest —— 否则「没做什么」不可审计"),

    # ── implementation: 时序（R0.3）
    dict(path="timing.signal_ts", type="datetime", layer="implementation", required=True,
         note="信号产生时刻；格式 'YYYY-MM-DD HH:MM:SS'。时序契约链的第 1 环"),
    dict(path="timing.decision_ts", type="datetime", layer="implementation", required=True,
         note="作出决策的时刻。必须 ≥ signal_ts —— 否则是回溯成交（R0.3 禁止）"),
    dict(path="timing.recorded_at", type="datetime", layer="implementation", required=True,
         note="落账时刻。必须 ≥ decision_ts —— 否则时序倒挂"),
    dict(path="timing.freshness_sec", type="number", layer="implementation", required=True,
         note="decision_ts − signal_ts；>120s 一律拒单（tick 卖出豁免，见 SELL_EXECUTION_CONTRACT §2）"),
    dict(path="timing.tick_executor", type="bool", layer="implementation", required=True,
         note="是否由 tick 执行器产生的决策（决定是否适用 120s 新鲜度门）"),

    # ── implementation: 输入快照
    dict(path="inputs.candidate_snapshot_id", type="str", layer="implementation", required=True,
         note="候选池快照 id（R0.4 蓝图要求）；无候选池的卖出决策可写空串"),
    dict(path="inputs.market_snapshot_hash", type="str", layer="implementation", required=True,
         note="决策所依据的行情数据 canonical hash（涉及标的的 bar 序列）—— 重放的基准"),
    dict(path="inputs.sop_version_id", type="str", layer="implementation", required=True,
         note="人格版本 id，如 v0；取自 persona/versions/registry.json"),
    dict(path="inputs.params_hash", type="str", layer="implementation", required=True,
         note="config/parameters.toml 的 sha256（R1.5 frozen 组）"),

    # ── implementation: 决策内容
    dict(path="decision.rules_fired", type="list[str]", layer="implementation", required=True,
         note="⭐ 必须是 R1.1 SOP 表里的规则 id（如 GEN-HOLD-01）；无规则触发时为空数组"),
    dict(path="decision.discretions", type="list[table]", layer="implementation", required=True,
         fields=["point_id", "output", "rationale", "model", "prompt_sha256", "cli_version"],
         note="⭐ 每项 point_id 必须是 R1.2 的 D1–D10；output 必须落在该点的枚举内。"
              "model/prompt_sha256/cli_version 是可重放性的前提（计划 §4.2）"),
    dict(path="decision.risk_gate", type="table", layer="implementation", required=True,
         fields=["passed", "veto_reason", "checks"],
         note="风控 veto 不可被裁量绕过；checks 列出实际执行的门禁项"),
    dict(path="decision.narrative_refs", type="list[str]", layer="implementation", required=False,
         note="本轮引用的 R1.4 记忆 id（MEM-*）或自述字段路径"),

    # ── implementation: 动作
    dict(path="action.order_intent", type="table", layer="implementation", required=True,
         fields=["side", "qty", "px_limit", "reason", "plan_ref"],
         note="打算做什么。`reason` 应是 rules_fired 或裁量点的可读映射，不是自由文本下单理由"),
    dict(path="action.executed", type="bool", layer="implementation", required=True,
         note="**是否真成交**。审计重建必须为 false（SELL_EXECUTION_CONTRACT §4）"),
    dict(path="action.kind", type="enum", layer="implementation", required=True,
         values=["fill", "audit_counterfactual", "shadow"],
         note="与 SELL_EXECUTION_CONTRACT §4 对齐；audit_counterfactual 是被 return 5 守卫"
              "永久禁执行的重建流水线产物"),
    dict(path="action.authority", type="str", layer="implementation", required=True,
         note="执行权威来源；真成交固定为 `account.fills`（防双重计算的机器可读标记）"),
    dict(path="action.fill_ref", type="str", layer="implementation", required=False,
         note="executed=true 时指向 account.fills 的定位键（sym+ts+qty+px）"),

    # ── effectiveness：只携带指针，不参与本层判定
    dict(path="outcome.pnl_attributable", type="number", layer="effectiveness", required=False,
         note="本 digest 关联的已实现盈亏。**仅供 R5.1 归因，不用于实现层判定**"),
    dict(path="outcome.effectiveness_basis", type="str", layer="effectiveness", required=False,
         note="该盈亏属于哪段净值（R5.2 版本分段）；禁止跨段拼接"),

    # ── integrity
    dict(path="replay_hash", type="str", layer="identity", required=True,
         note="⭐ 对**决策内容**（IMPLEMENTATION_HASH_FIELDS）的 canonical sha256。"
              "重放一致性的唯一判据：hash 相同即同决策"),
]

# replay_hash 覆盖哪些字段（顺序无关，canonical 后排序）
#
# 三分法：**决策内容**（进哈希）／**执行环境**（不进）／**结果**（不进）
#   决策内容 = day, sym, side, inputs.*, decision.*, action.order_intent
#   执行环境 = timing.*（何时触发）、action.executed / kind / authority / fill_ref（走哪条渠道）
#   结果     = outcome.*
#
# 刻意排除的理由：
#   timing.*        —— 同一决策在不同时刻/网络下触发，内容不应变化；排除后重放才可能一致
#   action.executed / fill_ref —— 执行环境决定（盘后禁执行 vs 盘中成交），非决策内容
#   action.kind / action.authority —— ⚠️ **这是本契约的一个关键裁定**：
#      它们描述「这条 digest 代表真实成交 / 审计重建 / 影子」，即**执行渠道**，不是决策本身。
#      若纳入哈希，则 R3 影子盘 → R4 实盘的**同一个决策**会被算成两个不同 fingerprint，
#      R5.0 会把这次「渠道切换」误判为**行为改动**并对每一天都触发 60 日评审 → 判据失效。
#      故排除；「是否为真成交」由 action.executed/kind 字段**如实记录**（并受 R-IMPL-5 校验），
#      只是不参与内容指纹。
#      身份标识用 `digest_id`（含当日 seq），内容比对用 `replay_hash` —— 两者职责分离。
#   outcome.*       —— 结果，绝不能进内容哈希（否则「赚钱了」会改变「决策是什么」）
#   digest_id / replay_hash / digest_revision —— 派生值
IMPLEMENTATION_HASH_FIELDS = [
    "day", "sym", "side",
    "inputs.candidate_snapshot_id", "inputs.market_snapshot_hash",
    "inputs.sop_version_id", "inputs.params_hash",
    "decision.rules_fired", "decision.discretions", "decision.risk_gate",
    "decision.narrative_refs",
    "action.order_intent",
]

# 明确列出「不进哈希」的执行环境字段，供校验与文档引用（防止将来被误加进去）
EXECUTION_CONTEXT_FIELDS = [
    "timing.signal_ts", "timing.decision_ts", "timing.recorded_at",
    "timing.freshness_sec", "timing.tick_executor",
    "action.executed", "action.kind", "action.authority", "action.fill_ref",
]

# ══════════════════════════════════════════════════════════════════════
# 分层判定规则（R5.0 的机械分判据）
# ══════════════════════════════════════════════════════════════════════
LAYER_RULES = [
    dict(id="R-IMPL-1", layer="implementation", rule="replay_hash 不变",
         meaning="该改动属**实现层**（refactor / 性能优化 / 日志调整等不改变决策内容）",
         verdict="可直接晋级（无需净值）"),
    dict(id="R-IMPL-2", layer="implementation", rule="provenance_complete",
         meaning="必填字段齐备 + decision_id 已在 ledger 登记（R0.4）",
         verdict="失败即实现层不达标，禁止晋级"),
    dict(id="R-IMPL-3", layer="implementation", rule="timing_ok",
         meaning="signal_ts ≤ decision_ts ≤ recorded_at；tick 执行器豁免 120s 新鲜度门，其余 >120s 拒单",
         verdict="失败即实现层不达标"),
    dict(id="R-IMPL-4", layer="implementation", rule="sop_conformant",
         meaning="rules_fired ⊆ SOP 规则 id；discretions[].point_id ⊆ D1–D10 且 output ∈ 枚举",
         verdict="失败即实现层不达标（出现表外理由 = 绕过 SOP）"),
    dict(id="R-IMPL-5", layer="implementation", rule="authority_not_double_counted",
         meaning="kind=audit_counterfactual ⇒ executed=false；executed=true ⇒ authority='account.fills'",
         verdict="失败即实现层不达标（会导致审计重建被误当成交、双重计算收益）"),
    dict(id="R-EFF-1", layer="effectiveness", rule="replay_hash 变化",
         meaning="属**行为改动**（决策内容变了）",
         verdict="**必须**进 60 日有效性队列，不得仅凭实现层判据晋级"),
    dict(id="R-EFF-2", layer="effectiveness", rule="有效性只能由 R5.1 判定",
         meaning="净值 / 回撤 / 收益÷回撤 vs 双基准臂 / 纪律合规率",
         verdict="digest 只提供 pnl_attributable 与版本分段依据，**不自行判定有效性**"),
]


# ══════════════════════════════════════════════════════════════════════ 实现
def canonical(obj) -> str:
    """稳定序列化：键排序 + 紧凑分隔符 + 无末尾空白。保证同一语义对象必得同一字符串。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _get(d, path):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def replay_hash(digest: dict) -> str:
    """对决策内容（IMPLEMENTATION_HASH_FIELDS）取 canonical sha256。"""
    payload = {p: _get(digest, p) for p in IMPLEMENTATION_HASH_FIELDS}
    return hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()


def build_digest(*, decision_id: str, day: str, sym: str, side: str,
                 signal_ts: str, decision_ts: str, recorded_at: str,
                 candidate_snapshot_id: str, market_snapshot_hash: str,
                 sop_version_id: str, params_hash: str,
                 rules_fired: list, discretions: list, risk_gate: dict,
                 order_intent: dict, executed: bool, kind: str, authority: str,
                 narrative_refs=None, fill_ref: str = "", seq: int = 1,
                 tick_executor: bool = True, pnl_attributable=None,
                 effectiveness_basis: str = "") -> dict:
    """组装 digest 并计算 digest_id / replay_hash。

    `freshness_sec` 由 signal/decision 计算，不由调用方传 —— 避免自报。
    """
    d = {
        "digest_revision": SCHEMA_VERSION,
        "decision_id": decision_id,
        "day": day,
        "sym": sym,
        "side": side,
        "timing": {
            "signal_ts": signal_ts,
            "decision_ts": decision_ts,
            "recorded_at": recorded_at,
            "freshness_sec": _delta_sec(signal_ts, decision_ts),
            "tick_executor": bool(tick_executor),
        },
        "inputs": {
            "candidate_snapshot_id": candidate_snapshot_id,
            "market_snapshot_hash": market_snapshot_hash,
            "sop_version_id": sop_version_id,
            "params_hash": params_hash,
        },
        "decision": {
            "rules_fired": list(rules_fired),
            "discretions": list(discretions),
            "risk_gate": dict(risk_gate),
            "narrative_refs": list(narrative_refs or []),
        },
        "action": {
            "order_intent": dict(order_intent),
            "executed": bool(executed),
            "kind": kind,
            "authority": authority,
            "fill_ref": fill_ref,
        },
        "outcome": {
            "pnl_attributable": pnl_attributable,
            "effectiveness_basis": effectiveness_basis,
        },
    }
    d["replay_hash"] = replay_hash(d)
    d["digest_id"] = f"dd-{day}-{seq}-{d['replay_hash'][:8]}"
    return d


_TS = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2}):(\d{2})$")


def _delta_sec(a: str, b: str) -> float:
    """两个 'YYYY-MM-DD HH:MM:SS' 之差（秒）。格式不合规返回 -1（由 validate 报错）。"""
    ma, mb = _TS.match(str(a)), _TS.match(str(b))
    if not (ma and mb):
        return -1.0
    from datetime import datetime
    fa = datetime.fromisoformat(f"{ma.group(1)}T{ma.group(2)}:{ma.group(3)}:{ma.group(4)}")
    fb = datetime.fromisoformat(f"{mb.group(1)}T{mb.group(2)}:{mb.group(3)}:{mb.group(4)}")
    return (fb - fa).total_seconds()


# ── 校验（引用完整性需要外部注入合法 id 集合）
def validate(digest: dict, *, sop_rule_ids=None, discretion_ids=None,
             discretion_enums=None, require_registered_decision=False,
             registered_decision_ids=None) -> list:
    """返回错误列表（空 = 通过）。实现层判定 R-IMPL-2/3/4/5 的机械检查都在这里。"""
    errs = []

    # R-IMPL-2 provenance_complete
    for f in FIELDS:
        if not f.get("required"):
            continue
        v = _get(digest, f["path"])
        if v is None or (isinstance(v, str) and v == "" and f["path"] != "inputs.candidate_snapshot_id"):
            errs.append(f"[R-IMPL-2] 缺必填字段 {f['path']}")
    if digest.get("digest_revision") != SCHEMA_VERSION:
        errs.append(f"[R-IMPL-2] digest_revision 应为 {SCHEMA_VERSION}，实际 {digest.get('digest_revision')}")
    for f in FIELDS:
        if f["type"] == "enum":
            v = _get(digest, f["path"])
            if v is not None and v not in f["values"]:
                errs.append(f"[R-IMPL-2] {f['path']}={v!r} 越出枚举 {f['values']}")
    if require_registered_decision:
        reg = set(registered_decision_ids or [])
        if digest.get("decision_id") not in reg:
            errs.append(f"[R-IMPL-2] decision_id={digest.get('decision_id')!r} 未在 ledger 登记")

    # R-IMPL-3 timing_ok
    t = digest.get("timing", {})
    fsec = t.get("freshness_sec", -1)
    if fsec is None or fsec < 0:
        errs.append("[R-IMPL-3] 时间戳格式不合规（无法计算 freshness_sec）")
    else:
        if t.get("signal_ts") and t.get("decision_ts") and fsec < 0:
            errs.append("[R-IMPL-3] decision_ts 早于 signal_ts")
        if t.get("decision_ts") and t.get("recorded_at"):
            d2r = _delta_sec(t["decision_ts"], t["recorded_at"])
            if d2r is not None and d2r < 0:
                errs.append("[R-IMPL-3] recorded_at 早于 decision_ts（违反 signal≤decision≤recorded）")
        if not t.get("tick_executor") and fsec > 120:
            errs.append(f"[R-IMPL-3] 非 tick 执行器且 freshness_sec={fsec} > 120s，应拒单")

    # R-IMPL-4 sop_conformant
    if sop_rule_ids is not None:
        unknown = [r for r in digest.get("decision", {}).get("rules_fired", []) if r not in set(sop_rule_ids)]
        if unknown:
            errs.append(f"[R-IMPL-4] rules_fired 含 SOP 表外规则 id：{unknown}")
    if discretion_ids is not None:
        for item in digest.get("decision", {}).get("discretions", []):
            pid = item.get("point_id")
            if pid not in set(discretion_ids):
                errs.append(f"[R-IMPL-4] 裁量点 {pid!r} 不在 D1–D10 内")
            elif discretion_enums and pid in discretion_enums:
                if item.get("output") not in discretion_enums[pid]:
                    errs.append(f"[R-IMPL-4] {pid} 输出 {item.get('output')!r} 越出枚举 {discretion_enums[pid]}")

    # R-IMPL-5 防双重计算
    a = digest.get("action", {})
    if a.get("kind") == "audit_counterfactual" and a.get("executed"):
        errs.append("[R-IMPL-5] kind=audit_counterfactual 但 executed=true（审计重建不得计为成交）")
    if a.get("executed") and a.get("authority") != "account.fills":
        errs.append(f"[R-IMPL-5] executed=true 但 authority={a.get('authority')!r}（应为 account.fills）")

    # hash 自洽
    if digest.get("replay_hash") and digest["replay_hash"] != replay_hash(digest):
        errs.append("[integrity] replay_hash 与内容不符（内容被改过或哈希算法不一致）")
    return errs


def layer_verdict(prev: dict | None, cur: dict) -> dict:
    """R5.0 分层判定：给定前一版与当前版的 digest，判定改动属实现层还是行为层。"""
    if prev is None:
        return dict(verdict="behavior", reason="无基线可比 → 按行为改动处理（进 60 日队列）",
                    replay_hash_changed=True, rules=[r["id"] for r in LAYER_RULES])
    ph, ch = prev.get("replay_hash"), cur.get("replay_hash")
    changed = ph != ch
    return dict(
        verdict="behavior" if changed else "implementation",
        replay_hash_changed=changed,
        prev_replay_hash=ph,
        cur_replay_hash=ch,
        reason=("replay_hash 变化 → 行为改动，必须进 60 日有效性队列（R-EFF-1）" if changed
                else "replay_hash 不变 → 纯实现层改动，可直接晋级（R-IMPL-1）"),
        rules=[r["id"] for r in LAYER_RULES],
    )


def diff_fields(prev: dict, cur: dict) -> list:
    """replay_hash 变了，具体差在哪些字段？（行为改动的归因）"""
    out = []
    for p in IMPLEMENTATION_HASH_FIELDS:
        a, b = _get(prev, p), _get(cur, p)
        if canonical(a) != canonical(b):
            out.append(dict(field=p, prev=a, cur=b))
    return out
