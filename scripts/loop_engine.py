"""LOOP ENGINE 任务生成器 v1

职责：读 docs/loops/state.json → 输出待执行任务卡 tasks/next_task.json
规则：
- status=pending → 生成任务卡（含执行命令、通过标准、输出文件）
- status=waiting_data → 检查 tick 数据量，达标转 pending
- status=review_ready → 输出评审任务卡
- status=done/failed/archived → 跳过
用法：python -B scripts/loop_engine.py [--mode P0-A]（缺省=检查全部，输出第一个可执行任务）
"""
import json, pathlib, sys, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE = ROOT / "docs" / "loops" / "state.json"
TASKS = ROOT / "docs" / "loops" / "tasks"
TICK_DIR = pathlib.Path("F:/WorkBuddyItem/a股level2/tick")

TASK_TEMPLATES = {
    "P0-A": {
        "action": "backtest",
        "cmd": "python -B scripts/backtest_market.py --year {year} --hold 1 --min-env 2 --max-zhaban 35 --ten-oclock",
        "verify": "outputs/backtest_market_{year}.md exists and mean>0",
    },
    "P0-B": {
        "action": "research",
        "question": "设计同日择股排序规则（连板数/封板时间/市值/板块位置），在 raw CSV 上对比规则化选取 vs 随机基线，2025/2026 独立验证",
        "verify": "outputs/selection_alpha_{year}.md exists; rule >= random + t significant",
    },
    "P0-C": {
        "action": "backtest",
        "cmd": "python -B scripts/portfolio_sim.py {year} --regime-switch",
        "verify": "outputs/regime_switch_{year}.md exists; switch keeps return & cuts DD",
    },
    "P0-D": {
        "action": "research",
        "question": "研究 4 板晋级 5 板的特征（炸板次数/板块跟风/情绪/量能），分组区分度统计",
        "verify": "outputs/promo4_findings.md exists; group diff significant",
    },
    "P2-A": {
        "action": "framework",
        "question": "基于 src/core/sell.py T 引擎构建分钟级持仓模拟，100 笔小样验证 T 贡献",
        "verify": "outputs/t_contribution.md exists; T contrib >= 0.3%/笔·日",
    },
    "P3": {
        "action": "research",
        "question": "滑点 0.2/0.3% 敏感性 + 卖出变体 + 板块×情绪二维交叉（仅报告）",
        "verify": "outputs/sensitivity_report.md exists",
    },
    "P1-A": {
        "action": "research",
        "cmd": "python -B scripts/p1a_formal.py",
        "question": "恐慌衰竭低吸正式论证（子模式分治：当日反弹 vs 隔日承接；因子 big_buy/ba_ratio 分治；统计检验 t/聚类/分月）",
        "verify": "outputs/p1a_formal.md exists; 子模式分治结果表",
    },
    "P1-B": {
        "action": "research",
        "cmd": "python -B scripts/p1b_formal.py",
        "question": "5+ 板内选优正式论证（封单强度=封单/流通市值 + 首封时间 + 炸板次数；可成交率与胜率提升）",
        "verify": "outputs/p1b_formal.md exists; 选优因子分层表",
    },
}


def tick_days():
    """有效数据天数：tick 目录中存在 parquet 文件的交易日数"""
    if not TICK_DIR.exists():
        return 0
    n = 0
    for p in TICK_DIR.iterdir():
        if not p.is_dir():
            continue
        if any(p.glob("*.parquet")):
            n += 1
    return n


def main():
    mode_sel = None
    if len(sys.argv) > 1 and sys.argv[1].startswith("--mode"):
        mode_sel = sys.argv[1].split("=")[1] if "=" in sys.argv[1] else sys.argv[2]
    state = json.loads(STATE.read_text(encoding="utf-8"))
    TASKS.mkdir(parents=True, exist_ok=True)
    now = datetime.date.today().isoformat()
    # 1) waiting_data 检查
    for mid, m in state["modes"].items():
        if m["status"] == "waiting_data" and "data_days_needed" in m:
            m["data_days_now"] = tick_days()
            if m["data_days_now"] >= m["data_days_needed"]:
                m["status"] = "pending"
                m["phase"] = "spec"
                print(f"[loop] {mid}: 数据达标 ({m['data_days_now']}天) → pending")
    # 2) 找第一个可执行任务
    chosen = None
    for mid, m in state["modes"].items():
        if mode_sel and mid != mode_sel:
            continue
        if m["status"] != "pending":
            continue
        chosen = (mid, m)
        break
    if chosen:
        mid, m = chosen
        tpl = TASK_TEMPLATES.get(mid, {})
        card = {
            "mode": mid, "title": m["title"], "created": now,
            "action": tpl.get("action", "research"),
            "cmd": tpl.get("cmd", ""),
            "question": tpl.get("question", m.get("next_action", "")),
            "verify": tpl.get("verify", "artifact exists"),
            "artifacts": m.get("artifacts", []),
            "gate": m.get("gate", state.get("gate", {})),
        }
        out = TASKS / "next_task.json"
        out.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        m["status"] = "in_progress"
        m["phase"] = "running"
        print(f"[loop] 任务卡已生成: {mid} → {out}")
    else:
        pending = [mid for mid, m in state["modes"].items() if m["status"] == "pending"]
        print(f"[loop] 无可执行任务。pending={pending}")
        print(f"[loop] 数据状态: tick_days={tick_days()}")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
