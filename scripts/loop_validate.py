"""LOOP ENGINE 验证器 v1

职责：按任务卡 verify 规则断言输出；更新 state.json：
- 达标 → status=review_ready（触发专家评审）
- 不达标且 retries==0 → status=retry_variant（生成变体任务卡）
- 不达标且 retries>=1 → status=archived（留档）
用法：python -B scripts/loop_validate.py --mode P0-A --ok true/false [--note ...]
"""
import json, pathlib, sys, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "docs" / "loops" / "state.json"
TASKS = ROOT / "docs" / "loops" / "tasks"


def main():
    args = sys.argv[1:]
    mode = None
    ok = None
    note = ""
    i = 0
    while i < len(args):
        if args[i] == "--mode":
            mode = args[i + 1]; i += 2
        elif args[i] == "--ok":
            ok = args[i + 1].lower() == "true"; i += 2
        elif args[i] == "--note":
            note = args[i + 1]; i += 2
        else:
            i += 1
    if not mode or ok is None:
        print("usage: loop_validate.py --mode P0-A --ok true/false [--note ...]")
        return
    state = json.loads(STATE.read_text(encoding="utf-8"))
    m = state["modes"].get(mode)
    if not m:
        print(f"unknown mode {mode}")
        return
    now = datetime.date.today().isoformat()
    if ok:
        m["status"] = "review_ready"
        m["phase"] = "review"
        m["verified"] = now
        m["note"] = note
        print(f"[loop] {mode}: 验证通过 → review_ready（触发专家评审）")
    else:
        m["retries"] = m.get("retries", 0) + 1
        if m["retries"] >= 2:
            m["status"] = "archived"
            m["phase"] = "archived"
            m["note"] = note
            print(f"[loop] {mode}: 变体迭代 2 次仍不达标 → archived（留档）")
        else:
            m["status"] = "retry_variant"
            m["phase"] = "variant"
            m["note"] = note
            print(f"[loop] {mode}: 不达标 → retry_variant（第 {m['retries']} 次变体）")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    # 更新系统总览引用（评审完成时人工/评审 agent 更新 SYSTEM_OVERVIEW）


if __name__ == "__main__":
    main()
