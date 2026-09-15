"""画像：从知识卡片库确定性生成「选手战法画像（机读版）」，并做与上一版的差异对比。

边界：本模块**只重组卡片已有结论**，不新增判断。人工维护的叙述型画像
（選手学习资料/选手战法画像-累计.md）仍由会话侧撰写；本文件是其机读投影与变更审计。
"""
from __future__ import annotations

import json
import pathlib

from .model import Card

CATEGORY_ORDER = ["选股", "入场过滤", "出场", "风险", "仓位", "环境", "方法论",
                  "执行映射", "数据口径"]


def build_profile(cards: list[Card], as_of: str) -> dict:
    by_cat: dict[str, list[dict]] = {c: [] for c in CATEGORY_ORDER}
    for c in sorted(cards, key=lambda x: x.card_id):
        by_cat.setdefault(c.category, []).append(c.to_dict())
    return {
        "as_of": as_of,
        "card_total": len(cards),
        "categories": {k: v for k, v in by_cat.items() if v},
        "open_questions": [
            c.to_dict() for c in cards if c.testability == "qualitative"
        ],
        "note": "机读投影；叙述型画像见 選手学习资料/选手战法画像-累计.md",
    }


def render_markdown(profile: dict) -> str:
    out = [f"# 选手战法画像（机读版）· 截至 {profile['as_of']}", "",
           f"> 由知识卡片库确定性生成，共 {profile['card_total']} 张卡片。",
           "> 只重组卡片已有结论，不新增语义判断。", ""]
    for cat, items in profile["categories"].items():
        out += [f"## {cat}", "", "| 卡片 | 结论 | 可量化表述 | 可测试性 |", "|---|---|---|---|"]
        for it in items:
            st = it["statement"].replace("|", "\\|")
            q = (it["quantified"] or "—").replace("|", "\\|")
            out.append(f"| {it['card_id']} {it['title']} | {st} | {q} | `{it['testability']}` |")
        out.append("")
    if profile["open_questions"]:
        out += ["## 待量化（阻塞自动化）", ""]
        for it in profile["open_questions"]:
            out.append(f"- **{it['card_id']} {it['title']}**：{it['statement']}")
        out.append("")
    return "\n".join(out)


def diff_against(prev_path: pathlib.Path, cur: dict) -> dict:
    """与上一版机读画像对比卡片增减（用于「画像」阶段的前后差异报告）。"""
    if not prev_path.exists():
        return {"baseline": True, "added": sorted(
            c["card_id"] for items in cur["categories"].values() for c in items),
            "removed": [], "changed": []}
    try:
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"error": f"上一版画像不可读：{e}"}
    prev_ids = {c["card_id"]: c for items in prev.get("categories", {}).values() for c in items}
    cur_ids = {c["card_id"]: c for items in cur["categories"].values() for c in items}
    added = sorted(set(cur_ids) - set(prev_ids))
    removed = sorted(set(prev_ids) - set(cur_ids))
    changed = sorted(k for k in set(cur_ids) & set(prev_ids)
                     if cur_ids[k]["statement"] != prev_ids[k]["statement"]
                     or cur_ids[k]["quantified"] != prev_ids[k]["quantified"])
    return {"baseline": False, "added": added, "removed": removed, "changed": changed}
