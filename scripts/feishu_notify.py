"""Feishu reporting for the autonomous paper-trading team.

The webhook is read from the environment or an external local secret file. It is
never written to repository artifacts or logs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request
from datetime import datetime

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / "outputs" / "notifications"
SECRET_FILE = pathlib.Path(os.environ.get("YAOBAN_FEISHU_SECRET_FILE", r"C:\Users\YZP\WorkBuddy\yaoban_tasks\feishu_webhook.txt"))


def _webhook() -> str:
    value = os.environ.get("YAOBAN_FEISHU_WEBHOOK", "").strip()
    if not value and SECRET_FILE.exists():
        value = SECRET_FILE.read_text(encoding="utf-8").strip()
    if not value.startswith("https://open.feishu.cn/open-apis/bot/v2/hook/"):
        raise RuntimeError("Feishu webhook is missing or invalid")
    return value


def _atomic_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def send_text(text: str, *, event_key: str, kind: str, timeout: float = 8.0) -> bool:
    if not text.strip():
        raise ValueError("notification text is empty")
    OUT.mkdir(parents=True, exist_ok=True)
    state_file = OUT / "delivery_state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {"sent": {}}
    except Exception:
        state = {"sent": {}}
    if event_key in state.get("sent", {}):
        return True
    payload = json.dumps({"msg_type": "text", "content": {"text": text}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(_webhook(), data=payload, headers={"Content-Type": "application/json"}, method="POST")
    status = None
    business_code = None
    error = None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read(16384)
        body = json.loads(raw.decode("utf-8"))
        business_code = body.get("code")
        ok = status == 200 and business_code == 0
    except Exception as exc:
        ok = False
        error = type(exc).__name__
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    row = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "kind": kind,
           "event_key": event_key, "message_sha256": digest, "http_status": status,
           "business_code": business_code, "ok": ok, "error_type": error}
    audit = OUT / f"delivery_{datetime.now():%Y%m%d}.jsonl"
    with audit.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if ok:
        state.setdefault("sent", {})[event_key] = {"time": row["time"], "kind": kind, "message_sha256": digest}
        _atomic_json(state_file, state)
    return ok


def _plan_message(day: str) -> str:
    path = BASE / "outputs" / "plans" / f"{day}_plan.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    emotion = plan.get("emotion", {})
    picks = plan.get("picks", [])
    lines = [f"EvoAlpha｜盘前汇报 {day}",
             f"模式：10万元A股全自主模拟盘（真实资金未接入）",
             f"数据口径：{plan.get('mode', '未标注')}",
             f"市场温度：{emotion.get('temp', '未获取')}，阶段：{emotion.get('stage', '未获取')}",
             "候选观察：" + ("、".join(str(p.get("sym", "")) for p in picks) or "无"),
             "纪律：单票≤45%，总敞口≤90%，单日-5%熔断；关键数据失效则禁止新仓。"]
    return "\n".join(lines)


def _close_message(day: str) -> str:
    ledger = json.loads((BASE / "portfolio" / "ledger.json").read_text(encoding="utf-8"))
    account = ledger.get("account", {})
    curve = account.get("equity_curve", [])
    row = next((x for x in reversed(curve) if x.get("date") == day), None)
    fills = [x for x in account.get("fills", []) if x.get("date") == day]
    review_path = BASE / "outputs" / "reviews" / f"trader_daily_{day}.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    equity = row.get("equity") if row else None
    ret = ((float(equity) / float(ledger.get("start_cash", 100000)) - 1) * 100) if equity is not None else None
    return "\n".join([
        f"EvoAlpha｜盘后汇报 {day}",
        f"账户净值：{equity if equity is not None else '未完成估值'}" + (f"，累计收益 {ret:+.2f}%" if ret is not None else ""),
        f"现金：{account.get('cash', '未获取')}，持仓：{len(account.get('positions', {}))}只，当日成交：{len(fills)}笔",
        f"当日盈亏：{review.get('day_pnl', '样本首日/未获取')}%，累计闭环交易：{review.get('trades_total', 0)}笔，做T：{review.get('t_rounds', 0)}次",
        "状态：" + ("触发风险熔断" if ledger.get("risk_state", {}).get("blocked_date") == day else "未触发当日熔断"),
        "说明：模拟成交按成本和可成交约束记录，不代表真实资金收益。",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, choices=("test", "premarket", "close", "alert", "failure"))
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--event-key", default="")
    parser.add_argument("--message", default="")
    args = parser.parse_args()
    if args.kind == "premarket":
        text = _plan_message(args.date)
    elif args.kind == "close":
        text = _close_message(args.date)
    else:
        text = args.message
    key = args.event_key or f"{args.kind}:{args.date}"
    try:
        return 0 if send_text(text, event_key=key, kind=args.kind) else 2
    except Exception as exc:
        print(f"notification failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
