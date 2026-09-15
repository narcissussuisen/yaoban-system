"""R4.1 盘中 D6 否决权（最小切片）—— 让 LLM 裁量在**买入发生之前**生效。

## 为什么需要它

R3.2 的 LLM 裁量层只挂在**盘后 15:35 决策链**上，对当日盘中买入**毫无作用**（时序矛盾）。
本模块把 `D6`（分时质量三档）的否决权搬到 `scan_and_confirm.py` 的买入路径上。

## 六项已冻结裁定（2026-09-13，用户逐项确认）

1. **只接 D6 一条** —— 它是唯一有实测执行率证据的判据（5 个交易日与选手一致率 9/14）；
   D2/D3 板块阈值全是占位值（`calibrated=false`）、D4/D5 无机械判据 → 接入等于用未标定判据否决买入。
2. **当日首次判一次并锁定至收盘** —— 每只标的当日只真调一次。
3. **计划内 + 计划外全覆盖** —— scan 两条路都会真买，计划外正是「候选池 ≠ 完整交易集」的来源。
4. **产完整 `decision_digest`**（R1.6 契约）—— R4 验收要求「全链 provenance 完整」。
5. **降级 = 放行** —— LLM 失败/超时/schema 不过 → 不否决。语义等价于「退回无否决权的现状」，
   相对本次变更是**零新增风险**（不是「资金保守」，这点容易读反）。
6. 配套：`max_single_weight` 0.45 → 0.30（对齐 SOP「单票 ≤30%」）。

## 当日锁定的实现（本模块最易被改坏的地方）

`llm.consult` 的缓存键 = `sha256(point | template_version | model | snapshot_hash)`。
盘后链的 `snapshot_hash = _sha(dict(code, day, feats))` **含盘中特征** ⇒ 键随分钟变化 ⇒ 5 分钟 TTL
形同虚设，且同一标的可能被改判（先 `C_reject` 后 `A_optimum`）⇒ **否决权自我失效**。

本模块改用**日级稳定哈希**（只含 `scope|day|code`，不含任何盘中特征），并把 TTL 拉到**当日收盘**。
两者合起来才构成「当日锁定」：
  - 键稳定  ⇒ 同一标的当日命中同一缓存条目
  - TTL 覆盖 ⇒ 该条目当日不过期
只是**锁定的是结论**，不是输入 —— 决策时的 `obs` 仍是当时的实时分时特征（会被 `_persist` 落盘留证）。

⚠️ 盘后链的键**未改动**，故 R3.2 的 `replay_hash` 基线不受影响。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent.parent
for _p in (str(BASE), str(BASE / "src"), str(BASE / "scripts"), str(BASE / "portfolio")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.decision_digest import build_digest, validate  # noqa: E402
from decision_chain import figures as F  # noqa: E402
from decision_chain import llm  # noqa: E402

# 键命名空间：与盘后链的 per-code+feats 键**结构性不同**，两者永不互相命中。
INTRADAY_SCOPE = "intraday-d6-v1"
# 当日锁定的终点（收盘后 30 分钟，覆盖 scan 的最晚触发 15:01）。
LOCK_END_HM = (15, 30)
OUT_DIR = BASE / "outputs" / "decision_chain" / "intraday"

# LLM 侧失败语义 → 一律放行（裁定 5）
PASS_STATUSES = ("degraded", "unreproducible", "error", "llm_disabled", "skipped")

# ⚠️ 已登记的契约缺口（不在本切片处置）：
#   `decision_digest.FIELDS["action.kind"]` 的枚举只有 `["fill","audit_counterfactual","shadow"]`，
#   **没有为「真实盘中决策但被否决（未成交）」预留值**。本模块用 `kind="shadow"` + `authority="llm_veto"`
#   的组合过校验，靠 **authority** 与盘后影子链区分（authority 不进 replay_hash，见哈希三分法）。
#   建议 R5.0 阶段扩展该枚举（如 `live_veto`），届时本处一并改。
KIND_UNEXECUTED = "shadow"
AUTHORITY_VETO = "llm_veto"


def intraday_snapshot_hash(code: str, day: str) -> str:
    """**日级稳定**快照哈希 —— 故意不含盘中特征，这是「当日锁定」的前提，不可改成含 feats 的版本。"""
    return hashlib.sha256(f"{INTRADAY_SCOPE}|{day}|{code}".encode("utf-8")).hexdigest()


def lock_ttl(now: _dt.datetime) -> float:
    """缓存寿命 = 到当日 15:30 的剩余秒数（下限 60s）。"""
    end = now.replace(hour=LOCK_END_HM[0], minute=LOCK_END_HM[1], second=0, microsecond=0)
    return max(60.0, (end - now).total_seconds())


def _out_path(day: str) -> pathlib.Path:
    return OUT_DIR / f"intraday_{day}.jsonl"


def load_prev_df(code: str, day: str):
    """昨日 1m 序列（供 D6 第 ③ 项 input「今日低点 vs 昨日低点」）。

    数据来自 R2.4 盘后落盘的 `data/minute/1m/<code>.parquet`（**昨日已落盘**，盘中可读）。
    加载失败返回 None —— D6 该 input 记为 `None`，**不是**判定失败（与 ④ 段同口径）。
    """
    try:
        from decision_chain import engine as E
        pd_ = E._prev_trading_day(day)
        return F.load_day_minute(code, pd_) if pd_ else None
    except Exception:  # noqa: BLE001
        return None


def _rows(day: str) -> list:
    """当日 jsonl 全部记录（坏行跳过）。"""
    fp = _out_path(day)
    out = []
    if fp.exists():
        try:
            for line in fp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            pass
    return out


def _next_seq(day: str) -> int:
    """当日流水号 —— **只数真调记录**（`kind != "reuse"`），保证 digest_id 不撞号且不跳号。"""
    return 1 + sum(1 for r in _rows(day) if r.get("kind") != "reuse")


def _first_digest_id(day: str, code: str):
    """当日该标的第一次**真调**产生的 digest_id（供缓存命中的复用记录引用）。"""
    for r in _rows(day):
        if r.get("code") == code and r.get("digest_id"):
            return r["digest_id"]
    return None


def _sop_and_params():
    """复用 engine 的 SOP 版本与 params 哈希口径（单一事实源，不重复实现）。"""
    sop_v, params_hash, rules = "", "", []
    try:
        from decision_chain import engine as E
        sop_v = E._sop_version()
        p = BASE / "config" / "parameters.toml"
        params_hash = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else ""
        rules = list(E.HARD_RULES.get("ENTRY") or [])
    except Exception:  # noqa: BLE001
        pass
    return sop_v, params_hash, rules


def _d6_sop() -> dict:
    """D6 的 SOP 定义从真相源 `persona/discretion_v0.toml` 读（不硬编码原语）。"""
    try:
        from decision_chain import engine as E
        return E._discretion_def("D6")
    except Exception:  # noqa: BLE001
        return {}


def _build_digest(*, day, code, allowed, choice, score, reason, status, degraded,
                  r, snap, signal_ts, decision_ts, candidates_ref, plan_ref) -> tuple:
    sop_v, params_hash, rules = _sop_and_params()
    veto_reason = "" if allowed else "d6_c_reject"
    d = build_digest(
        decision_id=f"iv-{day}-{code}-{_next_seq(day)}",
        day=day, sym=code,
        # 被否决＝没有这笔买入 → `skip`（R1.6 明文：「没做什么」也必须有 digest 才可审计）
        side=("buy" if allowed else "skip"),
        signal_ts=signal_ts, decision_ts=decision_ts, recorded_at=decision_ts,
        candidate_snapshot_id=f"snap-D6-{snap[:8]}",
        market_snapshot_hash=snap, sop_version_id=sop_v, params_hash=params_hash[:16],
        rules_fired=rules,
        # R1.6：discretions 是**必填**且进 replay_hash；三件套（model/prompt_sha256/cli_version）
        # 是「可重放」的前提 —— 换模型 ⇒ model 变 ⇒ replay_hash 变。
        discretions=[dict(point_id="D6", output=choice, sym=code,
                          rationale=(reason or "")[:120],
                          model=(r or {}).get("model") or llm.DEFAULT_MODEL,
                          prompt_sha256=str((r or {}).get("prompt_hash") or "")[:16],
                          cli_version=(r or {}).get("cli_version") or llm.CLI_VERSION,
                          llm_status=status, degraded=bool(degraded))],
        risk_gate=dict(passed=bool(allowed),
                       veto_reason=veto_reason,
                       checks=["llm_veto:D6", f"llm_status:{status}"]),
        order_intent=dict(side=("buy" if allowed else "none"), qty=0, px_limit=0,
                          reason=(f"D6={choice}" if allowed else f"D6={choice}(veto)"),
                          plan_ref=plan_ref),
        executed=False,                 # 本模块只负责判定，成交由 ledger 另记（R-IMPL-5）
        kind=KIND_UNEXECUTED, authority=AUTHORITY_VETO,
        seq=1, tick_executor=False,
        narrative_refs=[f"candidates_ref:{candidates_ref}"] if candidates_ref else [],
    )
    errs = validate(d, require_registered_decision=False)
    return d, errs


def judge(code: str, *, day: str, df, prev_df=None, now: _dt.datetime | None = None,
          signal_ts: str = "", candidates_ref: str = "", plan_ref: str = "",
          use_llm: bool = True) -> dict:
    """盘中对一个**已触发买点**的标的行使 D6 否决权。

    **绝不抛异常、绝不阻断 scan** —— 任何内部失败都退化为「放行」（裁定 5）。

    返回 dict：
      allowed(bool) / choice / score / reason / status / degraded / cache_hit
      / veto_reason / features / snapshot_hash / digest / digest_errors
    """
    now = now or _dt.datetime.now()
    ts_decision = now.strftime("%Y-%m-%d %H:%M:%S")
    ts_signal = signal_ts or ts_decision
    # 纯函数、不会抛 → 提到 try 外，保证异常路径也能算出 digest 的 snapshot_hash
    snap = intraday_snapshot_hash(code, day)
    out = dict(code=code, allowed=True, choice=None, score=None, reason="",
               status="skipped", degraded=False, cache_hit=False,
               veto_reason="", features={}, snapshot_hash=snap,
               digest=None, digest_errors=[], model=None)
    raw = None
    try:
        feats = F.d6_features(df, prev_df) or {}
        out["features"] = feats

        if not use_llm:
            out.update(status="llm_disabled", veto_reason="llm_disabled")
        else:
            raw = llm.consult("D6", date=day, obs=dict(code=code, **feats),
                              snapshot_hash=snap, sop=_d6_sop(), cache_ttl=lock_ttl(now))
            st = raw.get("status")
            choice = raw.get("choice")
            vetoed = (st == "ok" and choice == llm.POINTS["D6"]["veto_choice"])
            out.update(
                allowed=not vetoed, choice=choice, score=raw.get("score"),
                reason=raw.get("reason") or "", status=st,
                degraded=bool(raw.get("_degraded")), cache_hit=bool(raw.get("cache_hit")),
                model=raw.get("model"),
                veto_reason=("d6_c_reject" if vetoed
                             else ("degraded_pass" if st != "ok" else "d6_not_reject")),
            )
    except Exception as e:  # noqa: BLE001  —— 裁定 5：任何失败都放行，绝不阻断 scan
        out.update(allowed=True, status="error",
                   veto_reason=f"exception:{type(e).__name__}",
                   error=f"{type(e).__name__}: {e}")

    # ⭐ **统一出口**：否决 / 放行 / 降级 / 异常**四条路径都必须留痕**。
    #   ⚠️ 首版把落盘写在 try 内 → LLM 抛异常时 digest 为 None ⇒「被放行的买入无痕」，
    #   违反 R4 验收「全链 provenance 完整」—— 由 `test_http_error_passes` 抓出。
    try:
        if out["cache_hit"]:
            # ⚠️ 当日锁定命中 ⇒ 结论**复用首次判定**，**不得再产 digest**：
            #   否则同一决策会拿到多个 digest_id（seq 递增），
            #   而 R1.6 明文「身份用 digest_id」——一个决策多个身份会让审计语义失效。
            #   改落一行轻量复用记录（引用首次 digest_id），保留缓存效率的可观测性。
            out["reused_digest_id"] = _first_digest_id(day, code)
            _append(day, dict(ts=ts_decision, code=code, allowed=out["allowed"],
                              choice=out["choice"], score=out["score"], status=out["status"],
                              veto_reason=out["veto_reason"], cache_hit=True, kind="reuse",
                              reuse_of=out["reused_digest_id"]))
        else:
            d, errs = _build_digest(
                day=day, code=code, allowed=out["allowed"], choice=out["choice"],
                score=out["score"], reason=out["reason"], status=out["status"],
                degraded=out["degraded"], r=raw, snap=snap,
                signal_ts=ts_signal, decision_ts=ts_decision,
                candidates_ref=candidates_ref, plan_ref=plan_ref)
            out["digest"], out["digest_errors"] = d, errs
            _append(day, dict(ts=ts_decision, code=code, allowed=out["allowed"],
                              choice=out["choice"], score=out["score"], status=out["status"],
                              veto_reason=out["veto_reason"], cache_hit=out["cache_hit"],
                              features=out["features"], kind="judge",
                              digest_id=d.get("digest_id"),
                              replay_hash=d.get("replay_hash"),
                              digest_errors=errs, digest=d))
    except Exception as e:  # noqa: BLE001
        # 连 digest 都构造失败 → 仍然放行，但把失败**显式登记**（绝不静默）
        out["digest_errors"] = [f"digest_failed:{type(e).__name__}: {e}"]
    return out


def _append(day: str, rec: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with _out_path(day).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def summarize(day: str) -> dict:
    """当日盘中否决汇总（供状态卡 / llm_health 使用）。

    ⚠️ 统计口径：只有**真调记录**（`kind != "reuse"`）计入 `n_judged`；
    缓存命中行是「同一判定的复用」，单列 `n_reuse`。
    否则同一只标的会被重复计数（实测：3 只标的各判 2 轮 → 旧口径报 `n_judged=6`）。
    """
    fp = _out_path(day)
    rows = _rows(day)
    judged = [r for r in rows if r.get("kind") != "reuse"]
    reused = [r for r in rows if r.get("kind") == "reuse"]
    vetoed = [r for r in judged if not r.get("allowed")]
    degraded = [r for r in judged if r.get("veto_reason") == "degraded_pass"]
    return dict(day=day, n_judged=len(judged), n_vetoed=len(vetoed),
                n_degraded_pass=len(degraded),
                n_reuse=len(reused),
                n_cache_hit=sum(1 for r in judged if r.get("cache_hit")) + len(reused),
                vetoed=[r.get("code") for r in vetoed],
                file=str(fp) if fp.exists() else "")
