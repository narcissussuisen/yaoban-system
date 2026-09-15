# -*- coding: utf-8 -*-
"""R0.2 主账本初始化 —— 50 万独立主账本（2026-09-12 用户裁决 8.1）。

背景与依据
----------
- v2.0 判据改为「自身前向净值为唯一晋级闸门」→ 账本本身必须可信；
- 8.1 裁决：主账本资金 = 500,000（原 100,000），start_date 随之变更；
- 现有生产账本必须**归档留证、不得覆盖无痕**（v2.0 计划 §7.9）；
- 冻结存证见 `baseline/manifests/baseline-v2-pre-restructure/`（manifest_hash 6ee6057e…），
  该账本当时 risk_state 为 `paused=true / drawdown_pct=-10.64 / position_multiplier=0.0`
  （9/11 触发 portfolio_drawdown_pause 熔断），**新账本必须清零，否则新仓被直接禁掉**。

安全设计
--------
1. **默认 dry-run**：不带 `--apply` 只预演、不写任何文件（含归档）。
2. **先归档后覆写**：归档含 sha256 + 迁移清单 + **回读校验**；归档失败即中止。
3. **锁内写入**：复用 `ledger.ledger_lock()`，遵守「所有账户写入经 ledger 模块」的单一写入纪律。
4. **写后断言不变量**：回读校验 cash/positions/policy/risk_state/revision，任一不符即非零退出。
5. **幂等**：目标已是 50 万全新空仓 → 报 `already_initialized` 且不改动。

用法
----
    # 预演（默认；不写文件）
    python scripts/initialize_main_ledger.py --capital 500000 --start-date 2026-09-14

    # 正式执行
    python scripts/initialize_main_ledger.py --capital 500000 --start-date 2026-09-14 --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
from datetime import datetime

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "portfolio"))
import ledger  # noqa: E402

ARCHIVE = pathlib.Path(r"C:\Users\YZP\WorkBuddy\yaoban_tasks\ledger_archive")
SCHEMA_KEYS = (
    "_revision", "start_cash", "start_date", "benchmark", "policy", "account",
    "plans", "reviews", "rules_log", "signal_requests", "human_decisions",
    "autonomous_decisions", "risk_state", "date", "updated_at",
)


def atomic_write(path: pathlib.Path, state: dict) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid_hex()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def uuid_hex() -> str:
    import uuid
    return uuid.uuid4().hex


def build_state(capital: float, start_date: str, benchmarks: list[str]) -> dict:
    policy = dict(ledger.DEFAULT_POLICY)
    policy.update({
        "account_mode": "autonomous_paper",          # ledger._validate_buy_policy 强校验
        "require_human_decision": False,             # 同上
        # 2026-09-13：0.45 → 0.30，对齐 SOP「单票 ≤30%」（见 ledger.DEFAULT_POLICY 注释）
        "max_single_weight": 0.30,
        "max_positions": 2,
        "max_gross_exposure": 0.90,
        "max_new_buys_per_day": 1,
        "transition_reduce_only": False,
    })
    now = datetime.now()
    return {
        "_revision": 1,
        "start_cash": float(capital),
        "start_date": start_date,
        # 原为 "000852.SH"；无任何消费方（已 grep 确认），改记基准臂组合以便审计
        "benchmark": "+".join(benchmarks) if benchmarks else "000852.SH",
        "policy": policy,
        "account": {
            "cash": float(capital),
            "positions": {},
            "fills": [],
            # R0.2：统一写 mv —— 历史首点曾写 market_value（baseline: True 是布尔标志，非数值），
            # 导致读 mv 的消费方（Vibe 看板）漏首点。ledger._normalize 另有兼容归并兜底。
            "equity_curve": [{"date": start_date, "equity": float(capital),
                              "cash": float(capital), "mv": 0.0}],
        },
        "plans": {},
        "reviews": {},
        "rules_log": [{
            "date": start_date,
            "rule": "main_ledger_50w_rebuild",
            "status": "active",
            "source": "user_ruling_2026-09-12_8.1",
        }],
        "signal_requests": {},
        "human_decisions": {},
        "autonomous_decisions": {},
        # 清零 9/11 熔断态（paused / drawdown_pct / position_multiplier）。
        # source_date 必须是字符串：ledger.py:303 做 `risk.get("source_date","") < day` 比较，
        # 若该键存在但为 None 会抛 TypeError。
        "risk_state": {
            "position_multiplier": 1.0,
            "paused": False,
            "terminated": False,
            "drawdown_pct": 0.0,
            "peak_equity": float(capital),
            "source_date": start_date,
            "reason": "fresh_main_ledger",
        },
        "date": now.strftime("%Y-%m-%d"),
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }


def assert_invariants(state: dict, capital: float, start_date: str) -> list[str]:
    problems = []
    if state.get("_revision") != 1:
        problems.append(f"_revision={state.get('_revision')} != 1")
    if float(state.get("start_cash", -1)) != float(capital):
        problems.append(f"start_cash={state.get('start_cash')} != {capital}")
    acct = state.get("account", {})
    if float(acct.get("cash", -1)) != float(capital):
        problems.append(f"account.cash={acct.get('cash')} != {capital}")
    if acct.get("positions"):
        problems.append("positions 非空")
    if acct.get("fills"):
        problems.append("fills 非空")
    curve = acct.get("equity_curve") or []
    if len(curve) != 1 or curve[0].get("date") != start_date:
        problems.append(f"equity_curve 首点异常: {curve}")
    elif "mv" not in curve[0]:
        problems.append("equity_curve 首点缺 mv（R0.2 字段统一失败）")
    if state.get("start_date") != start_date:
        problems.append(f"start_date={state.get('start_date')} != {start_date}")
    risk = state.get("risk_state", {})
    if risk.get("paused") or risk.get("terminated"):
        problems.append(f"risk_state 未清零: {risk}")
    if float(risk.get("position_multiplier", 0)) != 1.0:
        problems.append(f"position_multiplier={risk.get('position_multiplier')} != 1.0")
    pol = state.get("policy", {})
    if pol.get("account_mode") != "autonomous_paper":
        problems.append(f"account_mode={pol.get('account_mode')}")
    if pol.get("require_human_decision") is not False:
        problems.append(f"require_human_decision={pol.get('require_human_decision')}")
    missing = [k for k in SCHEMA_KEYS if k not in state]
    if missing:
        problems.append(f"schema 缺键: {missing}")
    return problems


def archive_existing(target: pathlib.Path, raw: bytes) -> dict:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(raw).hexdigest()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = ARCHIVE / f"ledger_before_main50w_{stamp}_{digest[:12]}.json"
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_bytes(raw)
    # 回读校验：归档内容必须与源逐字节一致
    if hashlib.sha256(tmp.read_bytes()).hexdigest() != digest:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("归档回读校验失败，中止（未覆写目标）")
    os.replace(tmp, dst)

    old = json.loads(raw.decode("utf-8")) if raw else {}
    oacct = old.get("account", {}) or {}
    manifest = {
        "archived_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(target),
        "archive": str(dst),
        "sha256": digest,
        "size_bytes": len(raw),
        "old_revision": old.get("_revision"),
        "old_start_cash": old.get("start_cash"),
        "old_start_date": old.get("start_date"),
        "old_positions": len(oacct.get("positions") or {}),
        "old_fills": len(oacct.get("fills") or []),
        "old_equity_points": len(oacct.get("equity_curve") or []),
        "old_risk_state": old.get("risk_state"),
        "reason": "R0.2 重建 50 万独立主账本（用户裁决 8.1）",
    }
    with (ARCHIVE / f"migration_{stamp}.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=500000.0)
    ap.add_argument("--start-date", default="2026-09-14",
                    help="净值守恒起点（应为新观察期首个交易日）")
    ap.add_argument("--ledger", default="", help="目标账本路径；缺省用 ledger.LEDGER（env-aware canonical）")
    ap.add_argument("--benchmarks", default="all_a_equal,csi2000")
    ap.add_argument("--apply", action="store_true", help="真正写入；不带则仅预演")
    args = ap.parse_args()

    target = pathlib.Path(args.ledger) if args.ledger else ledger.LEDGER
    benchmarks = [b.strip() for b in args.benchmarks.split(",") if b.strip()]
    mode = "APPLY" if args.apply else "DRY-RUN"

    result: dict = {"mode": mode, "target": str(target), "capital": args.capital,
                    "start_date": args.start_date, "benchmarks": benchmarks}

    existing_raw = target.read_bytes() if target.exists() else b""
    result["existing_size_bytes"] = len(existing_raw)

    if existing_raw:
        try:
            old = json.loads(existing_raw.decode("utf-8"))
        except Exception as exc:
            old = {}
            result["existing_parse_error"] = str(exc)
        result["existing"] = {
            "revision": old.get("_revision"),
            "start_cash": old.get("start_cash"),
            "start_date": old.get("start_date"),
            "positions": len((old.get("account") or {}).get("positions") or {}),
            "fills": len((old.get("account") or {}).get("fills") or []),
            "account_mode": (old.get("policy") or {}).get("account_mode"),
            "risk_state": old.get("risk_state"),
        }
        # 幂等：已是目标状态则不重复覆写
        if (float(old.get("start_cash") or -1) == float(args.capital)
                and old.get("start_date") == args.start_date
                and not ((old.get("account") or {}).get("positions"))
                and not ((old.get("account") or {}).get("fills"))):
            result["status"] = "already_initialized"
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

    new_state = build_state(args.capital, args.start_date, benchmarks)

    # 写前自检（对新状态本身）
    problems_pre = assert_invariants(json.loads(json.dumps(new_state)), args.capital, args.start_date)
    if problems_pre:
        result["status"] = "precheck_failed"
        result["problems"] = problems_pre
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3

    if not args.apply:
        result["status"] = "dry_run_ok"
        result["would_archive_to"] = str(ARCHIVE)
        result["would_write"] = {k: new_state[k] for k in
                                 ("_revision", "start_cash", "start_date", "benchmark", "risk_state")}
        result["would_write"]["equity_curve"] = new_state["account"]["equity_curve"]
        result["would_write"]["policy"] = new_state["policy"]
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # ---- 正式执行：先归档，再锁内覆写 ----
    try:
        manifest = archive_existing(target, existing_raw) if existing_raw else {"skipped": "no existing ledger"}
        result["archive"] = manifest
    except Exception as exc:
        result["status"] = "archive_failed"
        result["error"] = str(exc)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 4

    with ledger.ledger_lock():
        atomic_write(target, new_state)

    back = json.loads(target.read_text(encoding="utf-8"))
    problems = assert_invariants(back, args.capital, args.start_date)
    result["post_verify"] = {"problems": problems, "ok": not problems}
    result["status"] = "applied" if not problems else "applied_VERIFY_FAILED"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not problems else 5


if __name__ == "__main__":
    raise SystemExit(main())
