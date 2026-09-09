"""EvoAlpha 模拟账户核心。

所有账户写入必须经此模块：
- Windows 文件锁 + revision/CAS + 唯一临时文件，避免并发丢更新；
- 普通买入强制人工确认、单票45%、最多2仓、毛敞口90%、每日新仓1笔；
- 旧4仓为 transition_reduce_only，只允许减仓，不强制卖出；
- T+1 按可卖数量而非“当日有任意买入即全锁”；
- 日末净值按日期 upsert，禁止同日重复曲线点。
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import time
import uuid
from datetime import datetime

from timing_contract import validate_buy_timing

try:
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None

ROOT = pathlib.Path(__file__).resolve().parent
LEDGER = ROOT / "ledger.json"
LOCK = ROOT / "ledger.lock"
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001
DEFAULT_POLICY = {
    "max_positions": 2,
    "max_single_weight": 0.45,
    "max_gross_exposure": 0.90,
    "max_new_buys_per_day": 1,
    "require_human_decision": True,
    "account_mode": "human_confirmed_paper",
    "max_daily_loss_pct": 5.0,
    "pause_drawdown_pct": 10.0,
    "terminate_drawdown_pct": 15.0,
    "transition_reduce_only": False,
}


class ConcurrentLedgerUpdate(RuntimeError):
    pass


def buy_net(px):
    return px * (1 + COMM + SLIP)


def sell_net(px):
    return px * (1 - COMM - SLIP - STAMP)


def _default_state() -> dict:
    return {
        "_revision": 0,
        "start_cash": 100000.0,
        "start_date": "2026-08-31",
        "benchmark": "000852.SH",
        "policy": dict(DEFAULT_POLICY),
        "account": {"cash": 100000.0, "positions": {}, "fills": [], "equity_curve": []},
        "plans": {}, "reviews": {}, "rules_log": [],
        "signal_requests": {}, "human_decisions": {}, "risk_state": {},
    }


def _normalize(state: dict) -> dict:
    state.setdefault("_revision", 0)
    policy = state.setdefault("policy", {})
    for k, v in DEFAULT_POLICY.items():
        policy.setdefault(k, v)
    state.setdefault("signal_requests", {})
    state.setdefault("human_decisions", {})
    state.setdefault("autonomous_decisions", {})
    state.setdefault("risk_state", {})
    state.setdefault("rules_log", [])
    state.setdefault("plans", {})
    state.setdefault("reviews", {})
    acct = state.setdefault("account", {})
    acct.setdefault("cash", state.get("start_cash", 100000.0))
    acct.setdefault("positions", {})
    acct.setdefault("fills", [])
    acct.setdefault("equity_curve", [])
    return state


def _read() -> dict:
    if LEDGER.exists():
        return _normalize(json.loads(LEDGER.read_text(encoding="utf-8")))
    return _default_state()


def load() -> dict:
    return _read()


@contextlib.contextmanager
def ledger_lock(timeout: float = 10.0):
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = LOCK.open("a+b")
    try:
        if fh.tell() == 0:
            fh.write(b"0")
            fh.flush()
        if msvcrt is None:
            yield
            return
        deadline = time.monotonic() + timeout
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("ledger lock timeout")
                time.sleep(0.05)
        try:
            yield
        finally:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        fh.close()


def _write_locked(state: dict, expected_revision: int | None = None):
    current = _read() if LEDGER.exists() else _default_state()
    current_rev = int(current.get("_revision", 0))
    if expected_revision is not None and current_rev != expected_revision:
        raise ConcurrentLedgerUpdate(f"ledger revision changed: expected={expected_revision} actual={current_rev}")
    now = datetime.now()
    state["_revision"] = current_rev + 1
    state["date"] = now.strftime("%Y-%m-%d")
    state["updated_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    tmp = LEDGER.with_name(f"{LEDGER.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, LEDGER)
    finally:
        try: tmp.unlink(missing_ok=True)
        except Exception: pass


def save(state: dict):
    expected = int(state.get("_revision", 0))
    with ledger_lock():
        _write_locked(state, expected_revision=expected)
        new_revision = int(state.get("_revision", expected + 1))
    state["_revision"] = new_revision
    return state


def transact(mutator, retries: int = 3):
    last = None
    for _ in range(retries):
        with ledger_lock():
            state = _read()
            rev = int(state.get("_revision", 0))
            result = mutator(state)
            _write_locked(state, expected_revision=rev)
            return state, result
        time.sleep(0.05)
    raise last or ConcurrentLedgerUpdate("transaction failed")


def _cost_equity(state: dict) -> float:
    acct = state["account"]
    return float(acct["cash"]) + sum(float(p.get("cost", 0)) * int(p.get("qty", 0)) for p in acct["positions"].values())


def _gross_cost(state: dict) -> float:
    return sum(float(p.get("cost", 0)) * int(p.get("qty", 0)) for p in state["account"]["positions"].values())


def sellable_qty(state: dict, sym: str, day: str) -> int:
    bought_before = sum(int(f["qty"]) for f in state["account"]["fills"]
                        if f.get("sym") == sym and f.get("side") == "buy" and f.get("date", "") < day)
    sold_to_now = sum(int(f["qty"]) for f in state["account"]["fills"]
                      if f.get("sym") == sym and f.get("side") == "sell" and f.get("date", "") <= day)
    held = int(state["account"]["positions"].get(sym, {}).get("qty", 0))
    return max(0, min(held, bought_before - sold_to_now))


def record_signal_request(state: dict, request: dict) -> str:
    rid = request.get("request_id") or f"sig-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
    row = dict(request)
    row.update({"request_id": rid, "status": "pending", "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    state["signal_requests"][rid] = row
    return rid


def record_human_decision(state: dict, request_id: str, decision: str, actor: str,
                          reason: str, approved_px_min: float | None = None,
                          approved_px_max: float | None = None) -> str:
    if request_id not in state["signal_requests"]:
        raise ValueError(f"signal request missing: {request_id}")
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be approve/reject")
    did = f"dec-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
    state["human_decisions"][did] = {
        "decision_id": did, "request_id": request_id, "decision": decision,
        "actor": actor, "reason": reason,
        "approved_px_min": approved_px_min, "approved_px_max": approved_px_max,
        "decided_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    state["signal_requests"][request_id]["status"] = "approved" if decision == "approve" else "rejected"
    state["signal_requests"][request_id]["decision_id"] = did
    return did


def record_autonomous_decision(state: dict, record: dict) -> str:
    """登记自主决策（P0.4：autonomous_paper 路径的决策 provenance）。

    Phase 0 由确定性规则引擎代行"组合经理"角色；Phase 1C 六角色在同 schema 上
    扩展 artifacts 哈希与角色签名。record 建议字段：
    sym/name/signal_ts/signal_px/rule/candidates_ref/plan_pick_ref/off_plan_reason。
    """
    did = f"dec-auto-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}"
    row = dict(record)
    row.update({"decision_id": did, "mode": "autonomous_paper",
                "decided_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    state.setdefault("autonomous_decisions", {})[did] = row
    return did


def _validate_decision(state: dict, decision_id: str, sym: str, px: float):
    dec = state.get("human_decisions", {}).get(decision_id)
    if not dec or dec.get("decision") != "approve":
        raise ValueError("人工确认缺失或未批准")
    req = state.get("signal_requests", {}).get(dec.get("request_id"), {})
    if req.get("sym") != sym:
        raise ValueError("人工确认标的与订单不一致")
    lo, hi = dec.get("approved_px_min"), dec.get("approved_px_max")
    if lo is not None and px < float(lo): raise ValueError("订单价低于批准区间")
    if hi is not None and px > float(hi): raise ValueError("订单价高于批准区间")


def _validate_buy_policy(state: dict, sym: str, ts: str, order_cost: float, decision_id: str,
                         signal_ts: str | None = None, decision_ts: str | None = None,
                         plan_match: dict | None = None, off_plan_reason: dict | None = None):
    policy = state["policy"]
    acct = state["account"]
    positions = acct["positions"]
    day = ts[:10]
    if policy.get("account_mode") == "autonomous_paper" and policy.get("require_human_decision"):
        raise ValueError("自主模拟账户配置矛盾: 不得要求人工确认")
    if not policy.get("require_human_decision") and policy.get("account_mode") != "autonomous_paper":
        raise ValueError("只有 autonomous_paper 账户可关闭人工确认")
    if policy.get("transition_reduce_only") or len(positions) > int(policy["max_positions"]):
        raise ValueError("过渡态只减不增: 当前持仓超过新两仓上限")
    if sym not in positions and len(positions) >= int(policy["max_positions"]):
        raise ValueError(f"持仓已达上限 {policy['max_positions']} 只")
    non_t_buys = {f.get("sym") for f in acct["fills"] if f.get("date") == day and f.get("side") == "buy"
                  and not str(f.get("reason", "")).startswith("t_")}
    if sym not in non_t_buys and len(non_t_buys) >= int(policy["max_new_buys_per_day"]):
        raise ValueError(f"当日新买入已达上限 {policy['max_new_buys_per_day']} 笔")
    eq = _cost_equity(state)
    if eq <= 0: raise ValueError("账户权益异常")
    risk = state.get("risk_state", {})
    mult = float(risk.get("position_multiplier", 1.0)) if risk.get("source_date", "") < day else 1.0
    if risk.get("terminated") or risk.get("paused"):
        raise ValueError("组合回撤门禁禁止新仓")
    if risk.get("blocked_date") == day or mult <= 0:
        raise ValueError("风险熔断禁止当日新仓")
    current_single = float(positions.get(sym, {}).get("cost", 0)) * int(positions.get(sym, {}).get("qty", 0))
    if (current_single + order_cost) / eq > float(policy["max_single_weight"]) * mult + 1e-9:
        raise ValueError("单票权重超过熔断后上限")
    if (_gross_cost(state) + order_cost) / eq > float(policy["max_gross_exposure"]) * mult + 1e-9:
        raise ValueError("毛敞口超过熔断后上限")
    # ---- P0.4 决策 provenance（蓝图 B0-P0-4: 任何空 decision_id 拒绝）----
    auto = state.get("autonomous_decisions", {}).get(decision_id)
    human = state.get("human_decisions", {}).get(decision_id)
    if not decision_id or not (auto or human):
        raise ValueError("decision_id 缺失或不可解析(人工/自主决策登记均无)")
    if auto and auto.get("sym") != sym:
        raise ValueError("自主决策标的与订单不一致")
    # ---- P0.2 时序契约（蓝图 B0-P0-2: signal_ts <= decision_ts <= recorded_at, 120s 新鲜度）----
    if signal_ts is None or decision_ts is None:
        raise ValueError("买入缺少 signal_ts/decision_ts 时序契约")
    ok, reason = validate_buy_timing(signal_ts, decision_ts, ts,
                                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if not ok:
        raise ValueError(f"时序契约违规: {reason}")
    # ---- P0.4 计划匹配契约（计划外允许但须显式理由, off_plan_reason 嵌套于 plan_match）----
    pm = plan_match if isinstance(plan_match, dict) else {}
    if pm.get("in_plan"):
        if not pm.get("pick_id"):
            raise ValueError("计划内买入必须携带 pick_id")
    else:
        reason_obj = pm.get("off_plan_reason") or off_plan_reason or {}
        if not reason_obj.get("code"):
            raise ValueError("计划外买入必须携带 off_plan_reason.code")
    if policy.get("require_human_decision"):
        dec = state.get("human_decisions", {}).get(decision_id)
        if not dec or dec.get("decision") != "approve":
            raise ValueError("人工确认缺失或未批准")


def buy(state: dict, sym: str, ts: str, px: float, qty: int, reason: str,
        stop_pct: float = 5.0, plan_ref: str = "", decision_id: str = "",
        signal_ts: str | None = None, decision_ts: str | None = None,
        candidates_ref: str | None = None, plan_match: dict | None = None,
        off_plan_reason: dict | None = None) -> dict:
    """普通买入：统一执行决策 provenance、时序契约与账户风险约束（P0.2/P0.4）。

    ts 语义 = 成交时刻（扫描路径为触发 bar 下一根的标签，即 fill.ts）。
    """
    cost = buy_net(px) * qty
    _validate_buy_policy(state, sym, ts, cost, decision_id,
                         signal_ts=signal_ts, decision_ts=decision_ts,
                         plan_match=plan_match, off_plan_reason=off_plan_reason)
    if state["policy"].get("require_human_decision"):
        _validate_decision(state, decision_id, sym, px)
    if cost > state["account"]["cash"] + 1e-6:
        raise ValueError(f"现金不足: {sym} 需 {cost:.0f} 可用 {state['account']['cash']:.0f}")
    state["account"]["cash"] -= cost
    pos = state["account"]["positions"].setdefault(sym, {})
    if pos.get("qty", 0) > 0:
        old_q, old_c = pos["qty"], pos["cost"]
        pos["qty"] = old_q + qty
        pos["cost"] = (old_q * old_c + cost) / pos["qty"]
    else:
        pos.update({"qty": qty, "cost": cost / qty, "entry_ts": ts,
                    "stop_px": px * (1 - stop_pct / 100.0), "days": 0})
    _fill(state, ts, sym, "buy", qty, px, reason, plan_ref, decision_id,
          signal_ts=signal_ts, decision_ts=decision_ts,
          candidates_ref=candidates_ref, plan_match=plan_match)
    return state


def sell(state: dict, sym: str, ts: str, px: float, qty: int, reason: str,
         plan_ref: str = "", signal_ts: str | None = None,
         decision_ts: str | None = None, decision_id: str = "") -> dict:
    pos = state["account"]["positions"].get(sym)
    if not pos or pos.get("qty", 0) < qty: raise ValueError("持仓不足")
    day = ts[:10]
    available = sellable_qty(state, sym, day)
    if qty > available: raise ValueError(f"T+1 违规: {sym} 可卖 {available} 需卖 {qty}")
    state["account"]["cash"] += sell_net(px) * qty
    pos["qty"] -= qty
    if pos["qty"] <= 0: del state["account"]["positions"][sym]
    _fill(state, ts, sym, "sell", qty, px, reason, plan_ref, decision_id,
          signal_ts=signal_ts, decision_ts=decision_ts)
    return state


def t_buy(state: dict, sym: str, ts: str, px: float, qty: int, plan_ref: str = "",
          signal_ts: str | None = None, decision_ts: str | None = None) -> dict:
    if sym not in state["account"]["positions"]: raise ValueError("T进仅允许已有底仓")
    cost = buy_net(px) * qty
    if cost > state["account"]["cash"] + 1e-6: raise ValueError("现金不足(T进)")
    state["account"]["cash"] -= cost
    pos = state["account"]["positions"][sym]
    pos["qty"] += qty
    _fill(state, ts, sym, "buy", qty, px, "t_buy", plan_ref, "",
          signal_ts=signal_ts, decision_ts=decision_ts)
    return state


def t_sell(state: dict, sym: str, ts: str, px: float, qty: int, plan_ref: str = "",
           signal_ts: str | None = None, decision_ts: str | None = None) -> dict:
    pos = state["account"]["positions"].get(sym)
    if not pos or pos.get("qty", 0) < qty: raise ValueError("T出持仓不足")
    available = sellable_qty(state, sym, ts[:10])
    if qty > available: raise ValueError(f"T+1 违规(T出): {sym} 可卖 {available} 需卖 {qty}")
    state["account"]["cash"] += sell_net(px) * qty
    pos["qty"] -= qty
    if pos["qty"] <= 0: del state["account"]["positions"][sym]
    _fill(state, ts, sym, "sell", qty, px, "t_sell", plan_ref, "",
          signal_ts=signal_ts, decision_ts=decision_ts)
    return state


def _fill(state, ts, sym, side, qty, px, reason, plan_ref, decision_id="",
          signal_ts=None, decision_ts=None, candidates_ref=None, plan_match=None):
    """写入成交记录。ts = 成交时刻（fill_ts）；provenance 字段仅在提供时写入（历史 fill 不回改）。"""
    row = {
        "date": ts[:10], "ts": ts, "sym": sym, "side": side, "qty": qty,
        "px": round(px, 3), "reason": reason, "plan_ref": plan_ref,
        "decision_id": decision_id,
        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if signal_ts is not None: row["signal_ts"] = signal_ts
    if decision_ts is not None: row["decision_ts"] = decision_ts
    if candidates_ref is not None: row["candidates_ref"] = candidates_ref
    if plan_match is not None: row["plan_match"] = plan_match
    state["account"]["fills"].append(row)


def equity(state: dict, date: str, mark_px: dict) -> float:
    missing = [s for s in state["account"]["positions"] if s not in mark_px or mark_px[s] is None]
    if missing: raise ValueError(f"估值缺少持仓价格: {missing}")
    mv = sum(p["qty"] * float(mark_px[s]) for s, p in state["account"]["positions"].items())
    eq = state["account"]["cash"] + mv
    row = {"date": date, "equity": round(eq, 2), "cash": round(state["account"]["cash"], 2), "mv": round(mv, 2)}
    curve = state["account"]["equity_curve"]
    curve[:] = [old for old in curve if old.get("date") != date]
    curve.append(row)
    curve.sort(key=lambda x: x["date"])
    return eq


def record_plan(state, date, plan):
    plan["published_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["plans"][date] = plan


def record_review(state, date, review):
    state["reviews"][date] = review
