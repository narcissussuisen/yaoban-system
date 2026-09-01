"""evidence_guard: 证据失效表路径硬拒（蓝图 P0.5）.

Phase 1D promotion gate 的强制接入点：
    from evidence_guard import guard_paths
    guard_paths(evidence_refs)   # 命中失效表 artifact_paths 即抛 EvidenceInvalidatedError

规则:
  - 仅 artifact_paths 非空的条目参与硬拒（kind=cohort 的 reference_paths 不拒，见 guard_note）
  - 路径按 repo 相对 posix 归一后匹配
"""
from __future__ import annotations
import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parent.parent
INVALIDATION_JSON = BASE / 'docs' / 'loops' / 'evidence_invalidation.json'


class EvidenceInvalidatedError(Exception):
    """候选证据命中失效表——禁止用于晋级/放行."""


def _normalize(p) -> str:
    s = str(p).replace('\\', '/')
    s = s.lstrip('./')
    # 绝对路径 → repo 相对
    try:
        s = str(pathlib.Path(p).resolve().relative_to(BASE)).replace('\\', '/')
    except Exception:
        pass
    return s


def load_entries() -> list[dict]:
    data = json.loads(INVALIDATION_JSON.read_text(encoding='utf-8'))
    return data.get('entries', [])


def _blocked_map() -> dict[str, dict]:
    blocked: dict[str, dict] = {}
    for e in load_entries():
        for ap in (e.get('artifact_paths') or []):
            blocked.setdefault(_normalize(ap), e)
    return blocked


def check_paths(paths) -> list[dict]:
    """返回命中清单 [{path, entry_id, stale_claim, invalid_reason}]，不抛错."""
    blocked = _blocked_map()
    hits = []
    for p in paths or []:
        key = _normalize(p)
        if key in blocked:
            e = blocked[key]
            hits.append({'path': key, 'entry_id': e.get('id'),
                         'stale_claim': e.get('stale_claim'),
                         'invalid_reason': e.get('invalid_reason')})
    return hits


def guard_paths(paths) -> None:
    """晋级器入口：任一命中即抛 EvidenceInvalidatedError（fail-closed）."""
    hits = check_paths(paths)
    if hits:
        msgs = '; '.join(f"{h['path']} -> {h['entry_id']}({h['stale_claim']})" for h in hits)
        raise EvidenceInvalidatedError(f'证据已作废，禁止用于晋级/放行: {msgs}')
