"""C1b: 窗口内异常 fills 的 append-only provenance reconciliation（P0 关门 G6 处置工具）。

语义（计划 toasty-pulse-turing-ZXjccYo0.md v2.1 终审 P1 + v2.2 终审二轮 3/8）：
- 原 fill 一律不动（不改 decision_id、不改任何字段）——append-only 审计原则；
- 对窗口内（P0 观察窗口）无法解析 provenance 的 fill，追加 reconciliation 记录（jsonl）：
  retroactive=true、decision_status=unresolved|reconstructed、原始 fill 完整快照 + sha256；
- 如需补建合法 decision（--reconstruct），record_autonomous_decision 生成 dec-auto-* 新 ID
  仅作映射关联（mapped_decision_id），不得冒充原始时点证据、不回写原 fill；
- 窗口外历史 fill → legacy_fills_exempt.json（原因=pre-P0.2 无 provenance 契约，不计入 G6）；
- G6 三层判定：① original_provenance_valid ② retroactive_reconciled ③ unresolved_legacy；
  G6 整体 = ①+② 全覆盖且无 ③；存在 ② 的日关门评审标注 pass-with-exception。

用法（生产 python）：
  python -X utf8 scripts/reconcile_fills.py --window-start 2026-09-02            # dry-run 清单
  python -X utf8 scripts/reconcile_fills.py --window-start 2026-09-02 --commit   # 落盘
  python -X utf8 scripts/reconcile_fills.py --window-start 2026-09-02 --commit --reconstruct
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pathlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "portfolio"))
sys.path.insert(0, str(BASE))

from ledger import load as load_ledger, record_autonomous_decision  # noqa: E402

RECONCILED = BASE / "outputs" / "acceptance" / "reconciled_fills.jsonl"
EXEMPT = BASE / "outputs" / "acceptance" / "legacy_fills_exempt.json"
RISK_EVENTS = BASE / "outputs" / "intraday" / "risk_events.jsonl"


def _now_sh() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def _fill_hash(fill: dict) -> str:
    """原始 fill 规范化 JSON 的 sha256（键排序，ensure_ascii=False）。"""
    canon = json.dumps(fill, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _load_risk_execute_index() -> dict:
    """risk_events 中 action=execute 的事件 → {(sym, date, time): ev}，增强 reconciliation 的 rule 语义。"""
    idx = {}
    if not RISK_EVENTS.exists():
        return idx
    with RISK_EVENTS.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("action") == "execute":
                idx[(ev.get("sym"), ev.get("date"), ev.get("time"))] = ev
    return idx


def _existing_reconciled_refs() -> set:
    """已 reconciliation 的 original_fill_hash 集合（幂等去重键）。"""
    refs = set()
    if not RECONCILED.exists():
        return refs
    with RECONCILED.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("original_fill_hash"):
                refs.add(row["original_fill_hash"])
    return refs


def enumerate_fills(state: dict, window_start: str, window_end: str) -> dict:
    """枚举 fills 并三层分类。返回 {valid, reconciled_needed, legacy_exempt}（原 fill 的深拷贝引用序号）。"""
    auto = state.get("autonomous_decisions", {})
    human = state.get("human_decisions", {})
    out = {"valid": [], "reconciled_needed": [], "legacy_exempt": []}
    for i, fill in enumerate(state["account"]["fills"]):
        d = fill.get("date", "")
        did = fill.get("decision_id", "")
        if d < window_start or d > window_end:
            out["legacy_exempt"].append((i, fill))
        elif did and (did in auto or did in human):
            out["valid"].append((i, fill))
        else:
            out["reconciled_needed"].append((i, fill))
    return out


def build_reconciliation_rows(state: dict, entries, window_start: str, window_end: str,
                              reconstruct: bool = False) -> list:
    """对窗口内异常 fills 生成 append-only reconciliation 记录（不动 state 的 fill 本体）。"""
    ev_idx = _load_risk_execute_index()
    rows = []
    for i, fill in entries:
        ts = fill.get("ts", "")
        ev = ev_idx.get((fill.get("sym"), ts[:10], ts[11:]))
        row = {
            "reconciled_at": _now_sh(),
            "original_fill_ref": f"account.fills[{i}]",
            "original_decision_id": fill.get("decision_id", ""),
            "original_fill_ts": ts,
            "sym": fill.get("sym"),
            "side": fill.get("side"),
            "qty": fill.get("qty"),
            "px": fill.get("px"),
            "reason": fill.get("reason", ""),
            "plan_ref": fill.get("plan_ref", ""),
            "rule": f"tick_risk:{ev['trigger']}" if ev else (fill.get("reason") or "unknown"),
            "retroactive": True,
            "decision_status": "unresolved",
            "original_provenance_valid": False,
            "original_fill_hash": _fill_hash(fill),
            "original_fill_snapshot": copy.deepcopy(fill),
            "window": {"start": window_start, "end": window_end},
        }
        if ev:
            row["risk_event_ref"] = {"date": ev.get("date"), "time": ev.get("time"),
                                     "trigger": ev.get("trigger"), "action": ev.get("action")}
        if reconstruct:
            # 补建合法 decision 仅作映射关联（dec-auto-* 新 ID），不回写原 fill、不冒充原始时点证据。
            did = record_autonomous_decision(state, {
                "sym": fill.get("sym"),
                "signal_ts": ts,
                "signal_px": fill.get("px"),
                "rule": row["rule"],
                "plan_ref": fill.get("plan_ref", ""),
                "retroactive_note": "reconstructed for mapping only; not original-time evidence",
            })
            row["decision_status"] = "reconstructed"
            row["mapped_decision_id"] = did
        rows.append(row)
    return rows


def build_exempt_entries(entries) -> list:
    out = []
    for i, fill in entries:
        out.append({
            "fill_ref": f"account.fills[{i}]",
            "date": fill.get("date", ""),
            "ts": fill.get("ts", ""),
            "sym": fill.get("sym"),
            "side": fill.get("side"),
            "qty": fill.get("qty"),
            "px": fill.get("px"),
            "decision_id": fill.get("decision_id", ""),
            "reason_code": "pre_p02_no_provenance_contract",
            "note": "窗口外历史 fill（P0.2 provenance 契约生效前），不计入 G6，仅登记豁免",
            "original_fill_hash": _fill_hash(fill),
        })
    return out


def g6_verdict(state: dict, entries: dict, recon_rows: list) -> dict:
    n_valid = len(entries["valid"])
    n_recon = len(recon_rows)
    n_unresolved = len(entries["reconciled_needed"]) - n_recon
    n_legacy = len(entries["legacy_exempt"])
    g6_pass = (n_unresolved == 0) and (n_valid + n_recon == len(state["account"]["fills"]) - n_legacy)
    return {"original_provenance_valid": n_valid, "retroactive_reconciled": n_recon,
            "unresolved_legacy_in_window": n_unresolved, "legacy_exempt_out_of_window": n_legacy,
            "g6_pass": g6_pass,
            "g6_note": "存在 retroactive_reconciled 的日，关门评审标注 pass-with-exception" if n_recon else ""}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window-start", default="2026-09-02", help="P0 观察窗口起（含）")
    ap.add_argument("--window-end", default="2026-09-08", help="P0 观察窗口止（含）")
    ap.add_argument("--commit", action="store_true", help="落盘（默认 dry-run 只打印清单）")
    ap.add_argument("--reconstruct", action="store_true",
                    help="为异常 fill 补建映射 decision（dec-auto-*，仅 mapped_decision_id 关联）")
    a = ap.parse_args()

    state = load_ledger()
    fills_before = copy.deepcopy(state["account"]["fills"])
    entries = enumerate_fills(state, a.window_start, a.window_end)
    recon_rows = build_reconciliation_rows(state, entries["reconciled_needed"],
                                            a.window_start, a.window_end, reconstruct=a.reconstruct)
    exempts = build_exempt_entries(entries["legacy_exempt"])
    verdict = g6_verdict(state, entries, recon_rows)

    print(f"=== C1b reconciliation {'COMMIT' if a.commit else 'DRY-RUN'} "
          f"window=[{a.window_start}, {a.window_end}] ===")
    print(f"① original_provenance_valid : {verdict['original_provenance_valid']}")
    print(f"② retroactive_reconciled   : {verdict['retroactive_reconciled']}")
    print(f"③ unresolved（窗口内未处置）: {verdict['unresolved_legacy_in_window']}")
    print(f"   legacy_exempt（窗口外）  : {verdict['legacy_exempt_out_of_window']}")
    print(f"G6 整体判定: {'PASS' if verdict['g6_pass'] else 'FAIL'}  {verdict['g6_note']}")
    for row in recon_rows:
        print(f"  [RECONCILE] {row['original_fill_ref']} {row['sym']} {row['side']} "
              f"{row['qty']}@{row['px']} ts={row['original_fill_ts']} "
              f"did={row['original_decision_id']!r} status={row['decision_status']} "
              f"hash={row['original_fill_hash'][:12]}..")
    for e in exempts:
        print(f"  [EXEMPT]    {e['fill_ref']} {e['sym']} {e['side']} {e['qty']}@{e['px']} "
              f"date={e['date']} did={e['decision_id']!r}")

    # 安全锚：fill 本体零修改
    assert state["account"]["fills"] == fills_before, "FATAL: 原 fill 被修改（违反 append-only）"

    if not a.commit:
        print("（dry-run，未落盘；--commit 写入 reconciled_fills.jsonl / legacy_fills_exempt.json）")
        return 0

    RECONCILED.parent.mkdir(parents=True, exist_ok=True)
    done = _existing_reconciled_refs()
    appended = 0
    with RECONCILED.open("a", encoding="utf-8") as f:
        for row in recon_rows:
            if row["original_fill_hash"] in done:
                print(f"  [SKIP-DUP] {row['original_fill_ref']} 已有同 hash 记录，幂等跳过")
                continue
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            appended += 1
    EXEMPT.write_text(json.dumps({
        "generated_at": _now_sh(),
        "window": {"start": a.window_start, "end": a.window_end},
        "entries": exempts,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"落盘：reconciled_fills.jsonl 追加 {appended} 行；legacy_fills_exempt.json {len(exempts)} 条")

    if a.reconstruct:
        from ledger import save as save_ledger
        save_ledger(state)
        print("（--reconstruct：映射 decisions 已随 ledger 保存；原 fills 未改动）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
