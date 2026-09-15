"""知识卡片库：解析卡片 Markdown（机读投影）并累加去重。

卡片是「资料 → 系统」的唯一语义入口。本模块不产生任何新结论：
它只把人工/会话已确认的卡片做**确定性解析 + 累加 + 去重**，并按类别生成画像分节。

卡片文件格式（front-matter 式小节，见 data/iteration/knowledge_cards/*.md）：
    ### KC-0001 标题
    - **类别**：选股
    - **来源**：`path` @ 2026-09-08
    - **结论**：...
    - **可量化表述**：...
    - **可测试性**：`daily`
    - **样本数**：2
    - **关联参数**：`a.b`, `c.d`
"""
from __future__ import annotations

import pathlib
import re

from .model import Card

REQUIRED_FIELDS = ("类别", "来源", "结论", "可测试性")

_CARD_HEAD = re.compile(r"^###\s+(KC-\d+)\s+(.*)$")
_KV = re.compile(r"^-\s+\*\*(.+?)\*\*[：:]\s*(.*)$")
_SRC = re.compile(r"^`(.+?)`\s*@\s*(\S+)$")
_PARAM = re.compile(r"`([^`]+)`")


def _parse_block(card_id: str, title: str, lines: list[str]) -> Card | None:
    fields: dict[str, str] = {}
    for ln in lines:
        m = _KV.match(ln.strip())
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()
    if any(k not in fields for k in REQUIRED_FIELDS):
        return None
    src_m = _SRC.match(fields["来源"])
    source = src_m.group(1) if src_m else fields["来源"]
    source_date = src_m.group(2) if src_m else ""

    quantified = fields.get("可量化表述", "").strip()
    if quantified in ("", "—", "—（原文未给出量化阈值）"):
        quantified = None

    test_raw = fields.get("可测试性", "").strip().strip("`")
    sample_raw = fields.get("样本数", "").strip()
    sample_n = int(sample_raw) if sample_raw.isdigit() else None
    linked = _PARAM.findall(fields.get("关联参数", ""))

    return Card(
        card_id=card_id, title=title.strip(), category=fields["类别"].strip(),
        source=source, source_date=source_date, statement=fields["结论"].strip(),
        quantified=quantified, testability=test_raw or "qualitative",
        sample_n=sample_n, linked_params=linked,
    )


def parse_cards(path: pathlib.Path) -> list[Card]:
    """解析一个卡片文件。行级确定性解析，不猜测格式。"""
    cards: list[Card] = []
    cur_id: str | None = None
    cur_title = ""
    buf: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _CARD_HEAD.match(line.rstrip())
        if m:
            if cur_id:
                c = _parse_block(cur_id, cur_title, buf)
                if c:
                    cards.append(c)
            cur_id, cur_title, buf = m.group(1), m.group(2), []
        elif cur_id is not None:
            buf.append(line)
    if cur_id:
        c = _parse_block(cur_id, cur_title, buf)
        if c:
            cards.append(c)
    return cards


def load_library(cards_dir: pathlib.Path) -> tuple[list[Card], list[str]]:
    """加载卡片库（多文件合并）。返回 (cards, 告警)。同 id 重复时按 md5 内容取先出现者并告警。"""
    cards: list[Card] = []
    warn: list[str] = []
    seen: dict[str, str] = {}
    for f in sorted(cards_dir.glob("*.md")):
        for c in parse_cards(f):
            if c.card_id in seen and seen[c.card_id] != c.statement:
                warn.append(f"卡片 id 冲突：{c.card_id} 在 {f.name} 与既往内容不一致，保留先出现者")
                continue
            if c.card_id in seen:
                continue
            seen[c.card_id] = c.statement
            cards.append(c)
    return cards, warn


def summarize(cards: list[Card]) -> dict:
    by_cat: dict[str, int] = {}
    by_test: dict[str, int] = {}
    unquantified: list[str] = []
    for c in sorted(cards, key=lambda x: x.card_id):
        by_cat[c.category] = by_cat.get(c.category, 0) + 1
        by_test[c.testability] = by_test.get(c.testability, 0) + 1
        if c.testability == "qualitative" or not c.quantified:
            unquantified.append(c.card_id)
    return {
        "total": len(cards),
        "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
        "by_testability": dict(sorted(by_test.items(), key=lambda kv: -kv[1])),
        "unquantified": unquantified,
    }


def render_library(cards: list[Card]) -> str:
    """把卡片重排为合并视图（供 outputs/iteration/<date>/knowledge_cards.md）。"""
    out = ["# 知识卡片库（累计）", ""]
    s = summarize(cards)
    out += [f"> 共 {s['total']} 张卡片；可测试性分布：{s['by_testability']}",
            f"> 未量化（阻塞自动化）：{len(s['unquantified'])} 张 → {', '.join(s['unquantified']) or '—'}",
            ""]
    for cat in sorted({c.category for c in cards}):
        out += [f"## {cat}", ""]
        for c in sorted((x for x in cards if x.category == cat), key=lambda x: x.card_id):
            out += [f"### {c.card_id} {c.title}", "",
                    f"- **来源**：`{c.source}` @ {c.source_date}",
                    f"- **结论**：{c.statement}",
                    f"- **可量化表述**：{c.quantified or '—'}",
                    f"- **可测试性**：`{c.testability}`",
                    f"- **样本数**：{c.sample_n if c.sample_n else '—'}",
                    f"- **关联参数**：{', '.join('`' + p + '`' for p in c.linked_params) or '—'}",
                    ""]
    return "\n".join(out)
