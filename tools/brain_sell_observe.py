# -*- coding: utf-8 -*-
"""D11 卖出裁量（brain）**只读观察**：把一个交易日的"脑判"与"实际执行"对齐，给出可判的观察量。

背景：2026-09-13 接线完成（`scripts/tick_monitor.py` ←→ `src/decision_chain/brain.py`），
`EVOALPHA_BRAIN_SELL=1` 后才真正介入。本工具用于上线后逐日回答三个问题：

  ① 脑到底有没有在工作？（降级率 / 真调 vs 缓存命中）
  ② 脑改了机器多少动作？（`brain_hold` = 机械要卖、脑判不卖；这是复刻保真度的核心观察量）
  ③ 判定是否可审计？（SOP 依据、弱依据打标、每 (标的×卖点) 唯一性 = 日锁是否生效）

⚠️ **只读**：不写任何生产状态；`--push` 仅走 `scripts/feishu_notify.py` 的既有推送口径。

用法：
  python -X utf8 tools/brain_sell_observe.py                  # 观察今天
  python -X utf8 tools/brain_sell_observe.py --date 2026-09-14
  python -X utf8 tools/brain_sell_observe.py --date 2026-09-14 --push
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = pathlib.Path(__file__).resolve().parent.parent
BRAIN_DIR = BASE / "outputs" / "decision_chain" / "brain"
EVENTS = BASE / "outputs" / "intraday" / "risk_events.jsonl"
TZ = ZoneInfo("Asia/Shanghai")


def _rows(fp: pathlib.Path) -> list[dict]:
    if not fp.exists():
        return []
    out = []
    for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    return out


def collect(day: str) -> dict:
    verdicts = _rows(BRAIN_DIR / f"brain_{day}.jsonl")
    events = [e for e in _rows(EVENTS) if str(e.get("date")) == day]
    brain_events = [e for e in events if e.get("brain_status") is not None]
    return dict(day=day, verdicts=verdicts, events=events, brain_events=brain_events)


def _entry(code: str, v: dict) -> tuple:
    f = v.get("facts") or {}
    pos = f.get("position") or {}
    hold = f.get("holding") or {}
    px = pos.get("close") or pos.get("px") or hold.get("last") or hold.get("close")
    entry = pos.get("entry_px") or pos.get("cost")
    return px, entry


def report(day: str) -> dict:
    d = collect(day)
    vs, bes = d["verdicts"], d["brain_events"]
    st = collections.Counter(v.get("status") for v in vs)
    act = collections.Counter(v.get("action") for v in vs)
    sop_weak = [v for v in vs if v.get("sop_weak")]
    no_live = [v for v in vs if v.get("status") != "ok"]          # 未取到真实裁量的
    degraded_pass = [v for v in no_live if v.get("action") == "hold"]
    keys = collections.Counter((v.get("code"), v.get("trigger")) for v in vs)
    dup = {f"{k[0]}/{k[1]}": n for k, n in keys.items() if n > 1}
    ev_act = collections.Counter(e.get("action") for e in bes)
    holds = [e for e in bes if e.get("action") == "brain_hold"]

    L = []
    ad = L.append
    ad(f"EvoAlpha D11 卖出裁量观察 | {day}")
    ad("─" * 46)
    ad(f"脑判定条数     : {len(vs)}   status={dict(st) or '{}'}")
    ad(f"动作分布       : {dict(act) or '{}'}")
    ad(f"  其中降级兜底 : {len(degraded_pass)} 条（LLM 不可用/不过 schema ⇒ 退回机械卖出）")
    ad(f"SOP            : 弱依据 {len(sop_weak)} 条"
       + (f"（{', '.join(sorted({v.get('trigger') for v in sop_weak}))}）" if sop_weak else ""))
    ad(f"LLM 调用       : 真调 {sum(1 for v in vs if not v.get('cache_hit'))}"
       f" / 缓存命中 {sum(1 for v in vs if v.get('cache_hit'))}")
    ad(f"daemon 执行侧  : {len(bes)} 条 brain 关联事件  {dict(ev_act) or '{}'}")
    ad(f"  ⭐ 脑判不卖   : {len(holds)} 笔（机械要卖被脑拦下 —— 复刻动作的核心观察量）")

    ad("")
    ad("【明细】时间 标的 卖点 | 脑判(status/choice) | 动作 量 | SOP | 弱 | 理由")
    for v in vs:
        px, entry = _entry(v.get("code"), v)
        ad(f"  {str(v.get('ts'))[-8:]} {v.get('code')} {v.get('trigger')} | "
           f"{v.get('status')}/{v.get('choice')} | {v.get('action')} | "
           f"{v.get('sop_rule') or '—'}{' 弱' if v.get('sop_weak') else ''} | "
           f"{str(v.get('reason') or '')[:70]}")
        if px is not None or entry is not None:
            ad(f"        px={px} entry={entry}")
    if not vs:
        ad("  （当日无脑判定 —— 要么没有卖点触发，要么开关仍未生效）")

    ad("")
    ad("【执行侧事件】时间 标的 卖点 动作 量 px brain_status/choice")
    for e in bes:
        ad(f"  {e.get('time')} {e.get('sym')} {e.get('trigger')} {e.get('action')} "
           f"qty={e.get('qty')} px={e.get('px')} "
           f"{e.get('brain_status')}/{e.get('brain_choice')}")
    if not bes:
        ad("  （无）")

    ad("")
    ad("【观察指标（判据先登记，逐日填）】")
    deg_rate = (len(no_live) / len(vs) * 100) if vs else 0.0
    ad(f"  ① 降级率           : {deg_rate:.0f}%   {'PASS' if len(no_live) == 0 else 'CHECK'}"
       "   （>0 说明 LLM 通路不稳，当日脑≈没工作）")
    ad(f"  ② 脑判不卖笔数     : {len(holds)}   （每笔都要能解释：为什么机械该卖而脑不卖）")
    ad(f"  ③ 日锁唯一性       : {'PASS' if not dup else 'FAIL ' + str(dup)}"
       "   （同一 (标的×卖点) 当日应只有 1 条真判定）")
    ad(f"  ④ 弱依据占比       : {len(sop_weak)}/{len(vs)}"
       "   （弱依据的判定单独统计，不与有依据者混算）")
    ad(f"  ⑤ 执行侧可对齐     : {'PASS' if (not vs or bes) else 'CHECK'}"
       "   （有脑判定就应有对应执行/留痕事件）")

    summary = (f"EvoAlpha｜D11 卖出裁量日报 {day}\n"
               f"脑判定：{len(vs)} 条（ok={st.get('ok', 0)} 降级={len(no_live)}）\n"
               f"动作：hold={act.get('hold', 0)} / halve={act.get('halve', 0)} "
               f"/ clear_all={act.get('clear_all', 0)}\n"
               f"脑判不卖：{len(holds)} 笔｜弱依据：{len(sop_weak)} 条"
               f"｜日锁唯一：{'OK' if not dup else '异常'}\n"
               + (f"降级率 {deg_rate:.0f}% —— 请检查 LLM 通路。\n" if len(no_live) else "")
               + (f"事件见 outputs/intraday/risk_events.jsonl；判定见 "
                  f"outputs/decision_chain/brain/brain_{day}.jsonl"))
    return dict(text="\n".join(L), summary=summary, n=len(vs), holds=len(holds),
                degraded=len(no_live), dup=dup)


def main() -> int:
    ap = argparse.ArgumentParser(description="D11 卖出裁量只读观察")
    ap.add_argument("--date", default=datetime.now(TZ).strftime("%Y-%m-%d"))
    ap.add_argument("--push", action="store_true", help="把摘要推到 EvoAlpha 飞书 hook")
    ap.add_argument("--json", action="store_true", help="额外输出机器可读摘要")
    a = ap.parse_args()

    r = report(a.date)
    print(r["text"])
    if a.json:
        print(json.dumps({k: r[k] for k in ("n", "holds", "degraded", "dup")},
                         ensure_ascii=False))
    if a.push:
        sys.path.insert(0, str(BASE / "scripts"))
        from feishu_notify import send_text  # noqa: PLC0415
        ok = send_text(r["summary"], event_key=f"brain-observe:{a.date}", kind="alert")
        print(f"[push] {'OK' if ok else 'FAILED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
