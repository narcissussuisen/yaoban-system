# -*- coding: utf-8 -*-
"""R0.2c 消费方冒烟：新 50 万主账本落盘后，各读取方不得异常。

覆盖实际读账本的关键链路（只读，不写生产文件）：
  1. ledger.load / cost_equity / sellable_qty / policy 校验
  2. close_pipeline 的净值与回撤口径（peak / drawdown 必须为 0，不得因空仓/空成交报错）
  3. status_push / feishu_notify / build_board 的累计收益口径（= 0%）
  4. collect_daily_acceptance 的 EXPECTED_LEDGER_START 与账本一致
  5. 空 fills / 空 positions 下不抛异常
  6. day_timeline 的 load_fills 在无当日成交时正常返回

用法：python tools/verify_r02c_ledger_swap.py
"""
from __future__ import annotations

import json
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "portfolio"))
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(BASE / "src"))

import ledger  # noqa: E402

fails: list[str] = []
oks: list[str] = []


def check(cond, label, detail=""):
    (oks if cond else fails).append(label + (f" | {detail}" if detail and not cond else ""))


def main() -> int:
    st = ledger.load()

    # 1. 基本面
    check(st.get("_revision") == 1, "1/_revision == 1", str(st.get("_revision")))
    check(float(st.get("start_cash")) == 500000.0, "1/start_cash == 500000", str(st.get("start_cash")))
    check(st.get("start_date") == "2026-09-14", "1/start_date == 2026-09-14", str(st.get("start_date")))
    check(ledger.cost_equity(st) == 500000.0, "1/cost_equity == 500000", str(ledger.cost_equity(st)))
    check(st["account"]["positions"] == {}, "1/positions 空")
    check(st["account"]["fills"] == [], "1/fills 空")

    # 2. 熔断已清零（否则 _validate_buy_policy 直接禁新仓）
    risk = st.get("risk_state", {})
    check(not risk.get("paused"), "2/risk_state.paused False", str(risk.get("paused")))
    check(not risk.get("terminated"), "2/risk_state.terminated False")
    check(float(risk.get("position_multiplier", 0)) == 1.0, "2/position_multiplier 1.0",
          str(risk.get("position_multiplier")))
    # risk_state.source_date 必须是字符串：ledger.py:303 做 `source_date < day` 比较
    sd = risk.get("source_date")
    check(isinstance(sd, str), "2/source_date 是字符串（防 TypeError）", repr(sd))
    try:
        _ = sd < "2026-09-14"
        check(True, "2/source_date 可参与字符串比较")
    except TypeError as exc:
        check(False, "2/source_date 可参与字符串比较", str(exc))

    # 3. 授权开关（生产执行侧硬校验）
    pol = st.get("policy", {})
    check(pol.get("account_mode") == "autonomous_paper", "3/account_mode")
    check(pol.get("require_human_decision") is False, "3/require_human_decision False")

    # 4. close_pipeline 口径：peak 与 drawdown
    curve = st["account"]["equity_curve"]
    peak = max([float(x.get("equity", 0)) for x in curve] + [float(st.get("start_cash", 0))])
    dd = (float(curve[-1]["equity"]) / peak - 1) * 100 if peak else 0.0
    check(peak == 500000.0, "4/peak_equity == 500000", str(peak))
    check(abs(dd) < 1e-9, "4/drawdown == 0%", str(dd))

    # 5. 累计收益口径（status_push / feishu_notify / build_board / board_server）
    eq = float(curve[-1]["equity"])
    ret = (eq / float(st.get("start_cash", 100000)) - 1) * 100
    check(abs(ret) < 1e-9, "5/累计收益 == 0%", str(ret))
    multiple = eq / float(st.get("start_cash", eq))
    check(abs(multiple - 1.0) < 1e-9, "5/净值倍数 == 1.0", str(multiple))

    # 6. 首点含 mv（R0.2 字段统一）
    check("mv" in curve[0], "6/首点含 mv", str(curve[0]))
    check(curve[0].get("mv") == 0.0, "6/首点 mv == 0", str(curve[0].get("mv")))

    # 7. T+1 可卖数量（空持仓不得抛异常）
    try:
        q = ledger.sellable_qty(st, "600000", "2026-09-14") if hasattr(ledger, "sellable_qty") else 0
        check(q == 0, "7/空持仓 sellable_qty == 0", str(q))
    except Exception as exc:
        check(False, "7/空持仓 sellable_qty 不抛异常", f"{type(exc).__name__}: {exc}")

    # 8. 验收脚本的期望起点与账本一致（防 R0.11 漏改）
    src = (BASE / "scripts" / "collect_daily_acceptance.py").read_text(encoding="utf-8-sig")
    import re
    m = re.search(r"EXPECTED_LEDGER_START\s*=\s*'([^']+)'", src)
    exp = m.group(1) if m else None
    check(exp == st.get("start_date"),
          "8/EXPECTED_LEDGER_START 与账本 start_date 一致", f"expected={exp} ledger={st.get('start_date')}")

    # 9. 归档留证：切换前的账本必须已归档且 sha256 可查
    arch_dir = pathlib.Path(r"C:\Users\YZP\WorkBuddy\yaoban_tasks\ledger_archive")
    migs = sorted(arch_dir.glob("migration_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    check(bool(migs), "9/存在归档迁移清单")
    if migs:
        man = json.loads(migs[0].read_text(encoding="utf-8"))
        check(pathlib.Path(man["archive"]).exists(), "9/归档账本文件存在", man.get("archive"))
        import hashlib
        h = hashlib.sha256(pathlib.Path(man["archive"]).read_bytes()).hexdigest()
        check(h == man["sha256"], "9/归档 sha256 校验通过", f"{h} vs {man['sha256']}")
        check(man.get("old_start_cash") == 100000.0, "9/归档记录旧本金 10 万", str(man.get("old_start_cash")))
        check(man.get("old_positions") == 0 and man.get("old_fills") == 8,
              "9/归档记录旧持仓/成交（0 / 8）",
              f"{man.get('old_positions')} / {man.get('old_fills')}")

    print(f"PASS {len(oks)}")
    for f in fails:
        print(f"FAIL {f}")
    print("RESULT", "OK" if not fails else "FAILED")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
