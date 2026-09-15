# -*- coding: utf-8 -*-
"""D11 brain 卖出裁量接线：上线自检（**只读**，不写任何产物）。

背景：`src/decision_chain/brain.py` 的 D11（走不走 / 减半还是全走）于 2026-09-13 上线并接入
`scripts/tick_monitor.py` 的生产卖出路径。此前 `TRIGGER_SOP` 用的是本模块自造命名
（`zhaban`/`below_ma5`/`break_vwap`…），与 `core.sell::manage_day` 实际吐出的 reason
（`zhaban_sell`/`ma5_halve`/`vwap_halve`…）**大面积对不上** ⇒ `_load_sop(None)` 返回 `{}`
⇒ LLM **静默退化成"不喂 SOP 凭常识判"**（9/11 诺普信事故形态被 `brain.py` 注释记录过）。

本脚本校验 5 组不变量：
  A. 映射覆盖：10 个生产 reason 全部在 `TRIGGER_SOP`，9 个 `_load_sop()` 真能取到非空 `stmt`
  B. 配置对齐：**从 `core.sell.DEFAULT_PARAMS` + tick_monitor 强制关闭项重算生产 reason 全集**，
     断言与预期一致且 ⊆ `TRIGGER_SOP`（捕获未来配置漂移 / 新卖点漏登记）
  C. 源码锚点：brain 已 import、判定先于执行、hold 用 continue、qty 用新鲜可卖量封顶、
     **可执行代码里 `return 4`/`return 5` 各仅 1 处**（不得新增终止路径）
  D. 开关状态：打印 `EVOALPHA_BRAIN_SELL` 解析结果
  E. 离线干跑：`sell_verdict(..., use_llm=False, persist=False)` → `hold`/`llm_disabled` 且不落盘

用法：python -X utf8 tools/verify_brain_sell_wiring.py
"""
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO), str(REPO / "src"), str(REPO / "scripts"), str(REPO / "portfolio")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

failures: list = []
oks: list = []


def check(cond, label: str, detail: str = "") -> bool:
    (oks if cond else failures).append(f"{label}{(' | ' + str(detail)) if detail else ''}")
    return bool(cond)


# 生产**实际会发出**的卖出 reason 全集（依据见各条注释）
PROD_REASONS = ["stop_loss", "vwap_halve", "vwap_break_all", "break_low", "profit_take",
                "zhaban_sell", "second_high", "ma10_clear", "ma5_halve", "time_stop"]
# 生产**不会出现**的：`ten_oclock`（config 关闭）、`t_sell`/`t_sell_out`（daemon 强制 t_enabled=False）、
# `dragon_link`/`sector_retreat`（daemon 强制关闭，且无 combo 上下文）—— 故不列入覆盖要求。
# 它们仍保留 TRIGGER_SOP 的 legacy 键（未来重启用时可立即生效）。


def _code_only(src: str) -> str:
    """只保留非注释行（注释里合法地会提到 return 4/5 等字样）。"""
    return "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))


def a_mapping():
    from decision_chain import brain as B

    missing = [r for r in PROD_REASONS if r not in B.TRIGGER_SOP]
    check(not missing, "A/10 个生产 reason 全部登记在 TRIGGER_SOP", f"缺失: {missing or '无'}")

    # ⭐ 关键：不只看字面量，还要**真读 persona/sop_v0.toml** 确认能取到非空原语
    no_stmt, weak_ok = [], []
    for r in PROD_REASONS:
        rule, weak = B.sop_for(r)
        if not rule:
            weak_ok.append(r)
            continue
        if not (B._load_sop(rule).get("stmt") or "").strip():
            no_stmt.append(f"{r}->{rule}")
    check(not no_stmt, "A/9 条映射的 SOP 原语非空（真读 sop_v0.toml）", f"空原语: {no_stmt or '无'}")
    check(weak_ok == ["vwap_break_all"], "A/唯一无依据的是 vwap_break_all", f"实际: {weak_ok}")

    expect = {"stop_loss": "GEN-HOLD-28", "vwap_halve": "GEN-HOLD-05", "vwap_break_all": "",
              "break_low": "GEN-HOLD-32", "profit_take": "GEN-HOLD-07",
              "zhaban_sell": "GEN-HOLD-08", "second_high": "GEN-HOLD-09",
              "ma10_clear": "GEN-HOLD-01", "ma5_halve": "GEN-HOLD-01", "time_stop": "GEN-HOLD-16"}
    diff = {k: (expect[k], B.TRIGGER_SOP.get(k)) for k in expect if B.TRIGGER_SOP.get(k) != expect[k]}
    check(not diff, "A/10 条映射逐一相符", f"不符: {diff or '无'}")
    check(B.SOP_WEAK == {"vwap_halve", "vwap_break_all"}, "A/SOP_WEAK 标记正确", str(B.SOP_WEAK))

    # legacy 键保留（含 9/11 诺普信回归锚点）
    legacy = ["ten_oclock", "break_vwap", "below_ma5", "below_ma10", "below_ma20",
              "zhaban", "structure_break", "expectation_fulfilled"]
    miss_l = [k for k in legacy if k not in B.TRIGGER_SOP]
    check(not miss_l, "A/legacy 键全部保留（含 below_ma10 事故锚点）", f"缺: {miss_l or '无'}")
    check(B.TRIGGER_SOP.get("below_ma10") == "GEN-HOLD-40", "A/below_ma10 → GEN-HOLD-40 未被改坏")

    # 未登记 reason 必须被判为弱依据（= sop_missing 留痕，防静默退化复发）
    check(B.sop_for("__unknown_reason__") == ("", True), "A/未登记 reason → 弱依据标记（防静默退化）")
    # brain 内部降级语义未被本次改动篡改
    v = B.sell_verdict("002215", day="2026-09-11", trigger="stop_loss", use_llm=False, persist=False)
    check(v.get("action") == "hold" and v.get("status") == "llm_disabled",
          "A/use_llm=False 时 brain 内部仍回 hold（语义未被篡改）",
          f"{v.get('action')}/{v.get('status')}")
    check(v.get("sop_rule") == "GEN-HOLD-28" and v.get("sop_weak") is False,
          "A/sop_rule 与 sop_weak 已写入返回 dict（可审计）",
          f"{v.get('sop_rule')}/{v.get('sop_weak')}")


def b_config_alignment():
    from core.sell import DEFAULT_PARAMS as P
    from decision_chain import brain as B

    src = (REPO / "scripts" / "tick_monitor.py").read_text(encoding="utf-8")
    m = re.search(r"SELL_ENGINE_PARAMS\s*=\s*\{[^}]*\}", src, re.S)
    check(bool(m), "B/能在 tick_monitor 中定位 SELL_ENGINE_PARAMS")
    forced_off = set(re.findall(r'"(\w+)":\s*False', m.group(0))) if m else set()
    check(forced_off == {"t_enabled", "dragon_link_sell", "sector_retreat_sell"},
          "B/daemon 侧强制关闭项未变（t_enabled / dragon_link_sell / sector_retreat_sell）",
          str(sorted(forced_off)))

    def on(key, default=False):
        return bool(P.get(key, default))

    prod = set()
    if on("vwap_halve"): prod.add("vwap_halve")
    if on("vwap_break_all"): prod.add("vwap_break_all")
    if on("break_low_clear"): prod.add("break_low")
    if on("zhaban_sell"): prod.add("zhaban_sell")
    if on("second_high_sell"): prod.add("second_high")
    if P.get("stop_loss_pct"): prod.add("stop_loss")
    if P.get("profit_take_pct"): prod.add("profit_take")
    if on("daily_ma5_halve"): prod.add("ma5_halve")
    if on("daily_ma10_clear"): prod.add("ma10_clear")
    if P.get("time_stop_days"): prod.add("time_stop")
    # tick_monitor 的 legacy 硬止损（L371）与引擎异常降级分支（zhaban_sell / vwap_halve）已含在内
    if not on("ten_oclock"):
        prod.discard("ten_oclock")

    check(prod == set(PROD_REASONS), "B/按配置重算的生产 reason 全集与预期一致",
          f"差集: 多={sorted(prod - set(PROD_REASONS))} 少={sorted(set(PROD_REASONS) - prod)}")
    not_reg = sorted(r for r in prod if r not in B.TRIGGER_SOP)
    check(not not_reg, "B/重算出的 reason 全部已登记映射", f"未登记: {not_reg or '无'}")
    # 明确断言：10 点纪律在生产配置里是**关闭**的（若哪天被打开，本行会 FAIL 提醒重新评估映射）
    check(not on("ten_oclock"), "B/ten_oclock 生产关闭（与 PROD_REASONS 口径一致）", str(P.get("ten_oclock")))
    check("t_enabled" in forced_off and "dragon_link_sell" in forced_off
          and "sector_retreat_sell" in forced_off,
          "B/做T腿与组合联动在 daemon 侧关闭（t_sell/dragon_link 等不产出）")


def c_source_anchors():
    src = (REPO / "scripts" / "tick_monitor.py").read_text(encoding="utf-8")
    code = _code_only(src)

    check("from decision_chain import brain as _brain" in src, "C/已 import brain")
    check("def brain_sell_decide(" in src, "C/已定义 brain_sell_decide")
    try:
        check(src.index("brain_sell_decide(sym,trig") < src.index("return execute_tick_risk_sell("),
              "C/brain 判定先于执行（读在前、卖在后）")
    except ValueError:
        check(False, "C/定位 brain_sell_decide 调用点与 execute_tick_risk_sell 调用点")
    check("if b['hold']:" in src and "action':'brain_hold'" in src,
          "C/hold 分支存在且用 action=brain_hold 留痕")
    check("flush=True);continue" in src, "C/hold 用 continue，不落入执行块")
    check("min(qty,sellable_qty(st,sym,day))" in src,
          "C/qty 用新鲜 sellable_qty 封顶（防 execute_tick_risk_sell 抛错 → return 4）")
    check("min(qty,int(t1))" not in src, "C/已移除循环外 t1 快照算式")

    n4 = len(re.findall(r"\breturn 4\b", code))
    n5 = len(re.findall(r"\breturn 5\b", code))
    check(n4 == 1, "C/可执行代码中 return 4 仍仅 1 处（未新增终止路径）", f"实得 {n4}")
    check(n5 == 1, "C/可执行代码中 return 5 仍仅 1 处（未新增终止路径）", f"实得 {n5}")

    check("EVOALPHA_BRAIN_SELL" in src, "C/急停开关已定义")
    m = re.search(r"BRAIN_SELL_ENABLED\s*=\s*os\.environ\.get\([^\n]*", src)
    check(bool(m) and "== '1'" in m.group(0),
          "C/开关为白名单式启用（未设=关闭，符合「先观察」流程）",
          (m.group(0) if m else ""))
    check("brain_sell_decide(sym,trig,day,df,st,t1,qty," in src,
          "C/trigger 原样透传（未拼接 bar 时间戳 —— 保护 brain 的当日锁）")


def e_dry_run():
    from decision_chain import brain as B

    before = sorted((B.OUT_DIR).glob("*.jsonl")) if B.OUT_DIR.exists() else []
    v = B.sell_verdict("002215", day="2026-09-11", trigger="vwap_break_all",
                       use_llm=False, persist=False)
    after = sorted((B.OUT_DIR).glob("*.jsonl")) if B.OUT_DIR.exists() else []
    check(v.get("action") == "hold" and v.get("status") == "llm_disabled",
          "E/离线干跑（无网/无 key）：安全降级为 hold", f"{v.get('action')}/{v.get('status')}")
    check(before == after, "E/干跑未落盘（persist=False）")
    check(v.get("sop_weak") is True and v.get("sop_rule") == "",
          "E/vwap_break_all 被正确标为弱依据", f"{v.get('sop_rule')}/{v.get('sop_weak')}")


def main() -> int:
    a_mapping()
    b_config_alignment()
    c_source_anchors()
    e_dry_run()

    print("=" * 68)
    print("EVOALPHA_BRAIN_SELL =", repr(os.environ.get("EVOALPHA_BRAIN_SELL")))
    enabled = os.environ.get("EVOALPHA_BRAIN_SELL", "").strip().lower() == "1"
    print("D/开关解析：", "**启用**（brain 介入卖出）" if enabled else "关闭（纯机械卖出，与接线前一致）")
    print("=" * 68)
    print(f"PASS {len(oks)}")
    for f in failures:
        print(f"FAIL {f}")
    print("RESULT", "OK" if not failures else "FAILED")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
