# -*- coding: utf-8 -*-
"""R3.1 · 六段决策环引擎（骨架 + ①② 段实装）。

## 权威定义
`docs/EVOALPHA_V2_RESTRUCTURE_PLAN.md` §4.1 的六段表 + §R3.1
「六段决策环引擎，**每段产出结构化 artifact（含所依据数据快照 hash）**」。
R3 验收：「连续 10 个交易日产出完整决策链 artifact；**给定快照可重放同一输出（不可重放率 = 0）**」。

## ⭐ 关键设计：每段的 artifact = R1.6 的 `decision_digest`
这让 R1.6 从「纸面契约」变成**被真实使用**，且**天然满足「可重放」验收** ——
`replay_hash` 只含决策内容（排除 timing/outcome/执行渠道），同输入必得同 hash。
每段的 `inputs.market_snapshot_hash` 就是「所依据数据」的快照哈希。

## 本文件实现深度（**显式分级，不含糊**）
| 段 | 状态 | 说明 |
|---|---|---|
| ① 环境闸门 | ✅ **实装** | 情绪表（CSV，含 dt7 跌侧）+ 指数日线 → regime 分档 + 硬规则（GEN-GATE-01/02/04/17） |
| ② 定主线 | ✅ **实装** | ⭐ **R2.2 `sector_strength` + R2.3 `fund_flow` + `sector_themes` 的首次真实消费** |
| ③ 选真龙 | ⏳ stub | 复用 `plan_daily.detect_huigui_v5` 属后续增量（形态筛已有实现，需适配器） |
| ④ 找低吸 | ⏳ stub | 需候选池 1m 分时三档分级（D6）；⚠️「破均价线」只作诊断不作拦截 |
| ⑤ 稳持仓 | ⏳ stub | 复用 `core.sell::manage_day` 只读评估 |
| ⑥ 仓位 | ⏳ stub | 需 `ledger.cost_equity` 动态权益（R0.9 已就绪） |

→ **stub 不是占位符**：每段都真实产出 digest 并**显式写明 `status=stub` + 缺什么 + 依赖哪个已有实现**，
   不假装已实现（与 R1.2/R2 的 `calibrated=false` 同一纪律）。

## 用法
    python scripts/run_decision_chain.py --date 2026-09-11
    python scripts/run_decision_chain.py --date 2026-09-11 --replay   # 重放一致性校验
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import time

import numpy as np

BASE = pathlib.Path(__file__).resolve().parents[2]      # = yaoban-system（本文件在 src/decision_chain/ 下）
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

from core.decision_digest import SCHEMA_VERSION, build_digest, replay_hash, validate  # noqa: E402
from decision_chain import arbiter, llm  # noqa: E402  （R3.2 LLM 裁量层 / R3.4 三方裁定器）

SEGMENTS = [
    ("GATE", "① 环境闸门"),
    ("MAIN", "② 定主线"),
    ("DRAGON", "③ 选真龙"),
    ("ENTRY", "④ 找低吸"),
    ("HOLD", "⑤ 稳持仓"),
    ("SIZE", "⑥ 仓位"),
]

# ⭐ R1.6 `discretions[].rationale` 的字数上限（与 llm.REASON_MAX 同量级；摘要层不必留全文）
REASON_MAX_DIGEST = 120

# 各段声明的硬规则（SOP 规则 id）—— 供 digest.rules_fired 使用
HARD_RULES = {
    "GATE": ["GEN-GATE-01", "GEN-GATE-02", "GEN-GATE-04", "GEN-GATE-17"],
    "MAIN": ["GEN-MAIN-01", "GEN-MAIN-03", "GEN-MAIN-04"],
    "DRAGON": ["GEN-DRAGON-01", "GEN-DRAGON-02"],
    "ENTRY": ["GEN-ENTRY-01", "GEN-ENTRY-04", "GEN-ENTRY-06"],
    "HOLD": ["GEN-HOLD-01", "GEN-HOLD-03", "GEN-HOLD-22"],
    "SIZE": ["GEN-SIZE-02", "GEN-SIZE-04", "GEN-SIZE-05"],
}


def _sha(o) -> str:
    return hashlib.sha256(json.dumps(o, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def _sop_version() -> str:
    reg = BASE / "persona" / "versions" / "registry.json"
    if not reg.exists():
        return "v0"
    try:
        vs = json.loads(reg.read_text(encoding="utf-8")).get("versions", [])
        return vs[-1]["version"] if vs else "v0"
    except Exception:
        return "v0"


def _read_json(p: pathlib.Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ══════════════════════════════════════════════ ① 环境闸门（实装）
# ⚠️⚠️ CSV 与 DB 的**列名不同**（不只是值不同）—— 2026-09-13 实测：
#   CSV: `zt` / `dt` / `max_h` / `zhaban` / `zhaban_rate` / `touch` / `lianban_rate` / ...
#   DB : `zt_count` / `dt_count` / `max_height` / `zb_count` / `break_rate` / ...
#   若按 DB 键名直接读 CSV，`row.get("zt_count")` 会得 None → 下游 `or 0` 变成 **0**
#   → 实测把 `zt=40 dt=20` 静默读成 **`zt=0 dt=0`**，**足以让市场分档判错**（冰点/磨底/中性）。
#   故此处显式归一化为 **DB 口径**（下游只用 DB 键名）。
_CSV_TO_DB_KEYS = {
    "zt": "zt_count", "dt": "dt_count", "max_h": "max_height",
    "zhaban": "zb_count", "zhaban_rate": "break_rate",
}


def _coerce_num(v):
    """CSV 读进来全是字符串；把纯数字转成 int/float，其余原样返回。"""
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return v


def _sentiment_row(day: str) -> tuple[dict | None, str, str]:
    """取情绪行 —— **优先 CSV 真相源**（`r5p` 每日重建），缺失才回落 DB。

    ⚠️ 2026-09-13 修（原因见 `seg_gate` 内注释）：原先只读 DB，而 `r5p` **只重建 CSV 不写 DB**
    （DB 由 `sync_sentiment_db.py` 同步），`Store.sentiment_as_of(day)` 的语义又是
    「**≤ day 的最近一条**」→ DB 未同步当日行时会**静默返回上一交易日**。
    → 改为**优先读 CSV 的当日行**（真相源，不依赖 DB 同步）；回落 DB 时如实标注来源与源日期。

    返回 `(row, source, src_date)`；`source ∈ {"csv", "db"}`。
    """
    import csv as _csv
    fp = BASE / "outputs" / f"sentiment_full_{str(day)[:4]}.csv"
    if fp.exists():
        try:
            with fp.open("r", encoding="utf-8-sig", newline="") as fh:
                for r in _csv.DictReader(fh):
                    if str(r.get("date")) == str(day):
                        norm = {_CSV_TO_DB_KEYS.get(k, k): _coerce_num(v) for k, v in r.items()}
                        return (norm, "csv", str(day))
        except Exception:
            pass
    from data.store import Store
    st = Store()
    row = st.sentiment_as_of(day)
    st.close()
    return (row, "db", str((row or {}).get("date") or ""))


def seg_gate(day: str) -> dict:
    """情绪表（含 dt7 跌侧）+ 指数日线 → regime 分档。

    硬规则：GEN-GATE-01 涨停家数分档 / 02 冰点模板 / 04 成交额缩量 / 17 恐慌度量（含跌侧）
    """
    row, s_src, src_date = _sentiment_row(day)
    if not row:
        return dict(status="no_data", reason=f"情绪表无 {day} 的记录（CSV 与 DB 均缺）")
    # ⚠️ 2026-09-13：即便优先读 CSV，仍**显式标注**来源与源日期 —— 绝不静默。
    #    若 `src_date != day`（CSV 当日行缺失且 DB 也是旧行）→ `stale_sentiment=True`，
    #    下游（含人工复盘）能立刻看出 regime 判定基于**旧情绪**。
    stale = bool(src_date and src_date != day)
    zt = row.get("zt_count") or 0
    dt = row.get("dt_count") or 0
    dt7 = row.get("dt7_count")
    up = row.get("up_count")
    dn = row.get("down_count")
    med = row.get("median_pct")
    # 硬规则：冰点模板（GEN-GATE-02：涨停<40 且 跌停>涨停）
    bingdian = bool(zt < 40 and dt > zt)
    # 分档（GEN-GATE-01 的分档值；此处只做档位归属，不做动作）
    if bingdian:
        bucket = "冰点"
    elif zt < 64:
        bucket = "磨底"
    elif zt < 99:
        bucket = "中性"
    else:
        bucket = "活跃"
    # ①段的裁量点 D1 的**输入特征**（本阶段不调用 LLM，只把特征备齐）
    feats = dict(sentiment_date=row.get("date"), zt=zt, dt=dt, dt7=dt7,
                 up=up, down=dn, median_pct=med,
                 break_rate=row.get("break_rate"), max_height=row.get("max_height"),
                 breadth=(round(zt / (zt + dt), 4) if (zt + dt) else None),
                 bucket=bucket, is_bingdian=bingdian)
    # regime 判定（GEN-SIZE-02 的市况映射用）：冰点/普跌 → weak
    regime = "weak" if (bingdian or (med is not None and med < -1.0)) else "neutral"
    return dict(status="ok", regime=regime, features=feats,
                sentiment_source=s_src, sentiment_source_date=src_date, stale_sentiment=stale,
                decisable_by=["D1"],
                note=("①段**只产出特征与硬规则档位**；「今天做还是不做」的判定属 D1（LLM 裁量，R3.2）。"
                      f"情绪行来源＝{'CSV 真相源' if s_src == 'csv' else 'DB 回落'}"
                      + ("。⚠️ **情绪数据 stale**：源日期 %s ≠ 目标日 %s —— regime 判定基于**旧情绪**"
                         "（CSV 当日行缺失，且 DB 为旧行）" % (src_date, day) if stale else "")))


# ══════════════════════════════════════════════ ② 定主线（实装）
def seg_main(day: str) -> dict:
    """⭐ R2.2 `sector_strength` + R2.3 `fund_flow` + `sector_themes` 的**首次真实消费**。

    硬规则：GEN-MAIN-01 选板块三信号（钱往哪流/核心逻辑/业绩）· 03 板块强度三层 · 04 龙头三关键
    """
    ss = _read_json(BASE / "outputs" / f"sector_strength_{day}.json")
    if not ss:
        return dict(status="no_data", reason=f"缺 outputs/sector_strength_{day}.json（先跑 build_sector_strength.py）")
    ff = _read_json(BASE / "data" / "fund_flow" / f"fund_flow_{day}.json")
    themes = _read_json(BASE / "data" / "sector_themes.json")

    sectors = ss.get("sectors", [])
    # 「钱往哪流」：把 R2.3 的个股主力净额按板块汇总（用 sector_themes 的成分映射）
    flow_by_l2: dict[str, float] = {}
    n_flow = 0
    if ff and themes:
        mem = themes.get("sector_members", {})
        code2l2 = {}
        for l2, info in mem.items():
            for c in info.get("members", []):
                code2l2[c] = l2
        for r in ff.get("records", []):
            l2 = code2l2.get(r["code"])
            if l2:
                flow_by_l2[l2] = flow_by_l2.get(l2, 0.0) + r.get("net_main", 0.0)
                n_flow += 1
    for s in sectors:
        s["_flow"] = flow_by_l2.get(s["l2"], None)

    # 候选主线 = 强度分（M1/M2/M3）+ 资金流正的，按 (score, flow, ret_20d) 排序
    ranked = sorted([s for s in sectors if s.get("_flow") is not None],
                    key=lambda s: (-s.get("score", 0), -(s.get("_flow") or 0), -s.get("ret_20d", 0)))
    top = ranked[:8] if ranked else sorted(sectors, key=lambda s: -s.get("score", 0))[:8]
    lines = [dict(l2=s["l2"], name=s["name"], score=s["score"],
                  m1=s["m1"]["pass_"], m2=s["m2"]["pass_"], m3=s["m3"]["pass_"],
                  net_main=round((s.get("_flow") or 0) / 1e8, 2),  # 亿元
                  zt=s["d3"]["zt"], max_h=s["d3"]["max_h"], cm20=s["d3"]["cm20"],
                  ret_20d=s["ret_20d"])
             for s in top]
    return dict(
        status="ok",
        mainlines=lines,
        upstream=dict(
            sector_strength=f"sector_strength_{day}.json",
            fund_flow_codes=n_flow,
            sector_themes_blocks=len((themes or {}).get("sector_members", {})),
        ),
        decisable_by=["D2", "D3"],
        note="②段**只产出板块排序与三信号特征**；三信号的「加权与取舍」属 D2/D3（LLM 裁量，R3.2）。"
             "⚠️ 上游 M1/M2/M3 与资金流阈值均为 `calibrated=false` 的占位值，排序仅供 shadow 观察，**不得据此下单**。",
    )


# ══════════════════════════════════════════════ ③ 选真龙（实装）
# 战法 → SOP 入口规则 id（用于 digest.rules_fired）
FORMULA_RULES = {
    "huigui": "P1-DRAGON-01",          # 上升回档（母战法）
    "qu_shi_fanbao": "P2-DRAGON-01",   # 趋势反包
    "xianren": "P3-DRAGON-01",         # 仙人指路
    "zt_huicai": "P4-DRAGON-01",       # 涨停回踩低吸
}


def seg_dragon(day: str, main_payload: dict | None) -> dict:
    """三入口形态筛。**复用 `core.strategies.detect` 统一入口**，不重写形态逻辑。

    硬规则：P1 上升回档（五条件）/ P2 趋势反包 / P3 仙人指路（公式零歧义）/ P4 涨停回踩低吸

    ⚠️ 扫描范围说明（本增量的**显式边界**）：计划书 §4.1 ③ 的数据依赖写「全市场日线」，
    但全市场 5500 只 × 4 算子在本增量成本过高 → **先扫「当日候选池 ∪ 上线主线板块成分」**，
    并在 payload 里显式标 `scan_scope` 与 `full_market=False`，**不假装已全市场扫描**。
    """
    import pandas as pd
    from core import strategies as S

    scope: dict[str, str] = {}          # sym -> 来源
    if main_payload and main_payload.get("status") == "ok":
        themes = _read_json(BASE / "data" / "sector_themes.json") or {}
        for l in main_payload.get("mainlines", [])[:5]:
            for c in (themes.get("sector_members", {}).get(l["l2"], {}) or {}).get("members", []):
                scope.setdefault(c, f"主线:{l['name'] or l['l2']}")
    try:
        sys.path.insert(0, str(BASE / "scripts"))
        from minute_incremental_snapshot import candidate_union
        cands, _ = candidate_union(day)
        for c in cands:
            scope.setdefault(c, "候选池")
    except Exception:
        pass
    if not scope:
        return dict(status="no_data", reason="既无候选池也无主线板块成分可用")

    hits = []
    read_fail = 0
    for sym, src in scope.items():
        fp = pathlib.Path(r"F:\WorkBuddyItem\a股level2\daily_rebuilt") / f"{sym}.parquet"
        if not fp.exists():
            read_fail += 1
            continue
        try:
            df = pd.read_parquet(fp)
        except Exception:
            read_fail += 1
            continue
        # ⚠️ 2026-09-14 修复（**防前视 + 重放确定性**）：
        #   原代码直接取 parquet 的**最新一根**（`ser.iloc[-1]` / `df.iloc[-1]`），
        #   而 `F:/WorkBuddyItem/a股level2/daily_rebuilt/*.parquet` 会被**收盘链与
        #   minute-snapshot 任务持续更新** ⇒ 两重后果：
        #     ① **前视**：replay 2026-09-11 时实际用上了更新的 bar
        #        （实测 hits 里 `date=2026-09-14`）；
        #     ② **不确定性**：同一天内多次 run 会读到不同的「最新一根」
        #        ⇒ `chain_replay_hash` 漂移 ⇒ `ReplayTests::test_replay_identical` 间歇失败。
        #   修法：**先按 `day` 截断**，只用 ≤ day 的 bar（与 `core.pattern_pool` 的 asof 口径一致）。
        df = df[df["date"].astype(str) <= day].reset_index(drop=True)
        if df is None or len(df) < 61:
            read_fail += 1
            continue
        fired = []
        for name in FORMULA_RULES:
            try:
                ser = S.detect(df, name)
                if bool(ser.iloc[-1]):
                    fired.append(name)
            except Exception:
                continue
        if fired:
            hits.append(dict(sym=sym, from_=src, formulas=fired,
                             close=float(df["close"].iloc[-1]),
                             date=str(df["date"].iloc[-1])))
    hits.sort(key=lambda h: (h["from_"] != "候选池", -len(h["formulas"])))
    rules = sorted({FORMULA_RULES[f] for h in hits for f in h["formulas"]})
    return dict(
        status="ok",
        hits=hits,
        n_scope=len(scope), n_hits=len(hits), n_read_fail=read_fail,
        scan_scope="候选池 ∪ 上线主线板块成分",
        full_market=False,
        formulas_used=sorted(FORMULA_RULES),
        upstream=dict(operator="core.strategies.detect（统一入口，复用不重写）",
                      daily_source="F:/WorkBuddyItem/a股level2/daily_rebuilt（只读）"),
        decisable_by=["D4", "D5", "D9", "D10"],
        note="③段**只做形态命中**（公式级、可机械判定）；「形态像不像 / 有无涨停基因与题材热度 / 是洗盘还是走坏」"
             "属 D4/D5/D9/D10（LLM 裁量，R3.2）。⚠️ 本增量先扫「候选池 ∪ 主线成分」，"
             "**全市场扫描待下个增量**（full_market=False）。",
    )


# ══════════════════════════════════════════════ ④⑤⑥ stub（显式，不假装）
def _discretion_def(point_id: str) -> dict:
    """从真相源 `persona/discretion_v0.toml` 读裁量点定义（不硬编码 SOP 原语）。"""
    import tomllib
    fp = BASE / "persona" / "discretion_v0.toml"
    try:
        doc = tomllib.loads(fp.read_text(encoding="utf-8"))
        for d in doc.get("discretion", []):
            if d.get("id") == point_id:
                return dict(stmt=d.get("name"), quote=d.get("sop_quote"),
                            outputs=d.get("outputs"), inputs=d.get("inputs"),
                            why_hard=d.get("why_hard"), source=str(fp.name))
    except Exception:
        pass
    return {}


# ══════════════════════════════════════════════ ④ 找低吸（实装）
def seg_entry(day: str, use_llm: bool = True, codes: list[str] | None = None) -> dict:
    """候选池 1m 分时：**机械事实 + D6 裁量（LLM 行使否决权）**。

    ⭐ R3.2 接入后，本段的口径升级为：
      - `GEN-ENTRY-04`（`px < vwap`，`mech=full`）**仍只产出事实**，不单独投否决票；
      - **否决权由 `D6` 行使**：LLM 按 SOP 的 5 项 inputs 综合定 A/B/C 三档，
        **`C_reject` → 排除该候选**（SOP `outputs` 明文如此），**非机械一票否决**。
      - LLM 不可用/输出不过 schema/不可重放 → **降级到保守档 `B_medium`（可观察不优先，不排除）**
        —— 协议第 6 条「降级为硬规则 + 保守默认档」，绝不自由发挥。

    :param codes: **覆盖观察范围**。默认 None＝当日候选池（生产口径）；
        R3.3 双轨 shadow 传入**选手实际决策的标的** —— 因为要回答的是
        「**对选手这些标的，EvoAlpha 会怎么做**」；候选池只是我们自己的观察范围，
        **不应用来限制对照样本**（否则绝大多数选手标的会落进 `not_in_pool`，对照失去意义）。
    """
    from decision_chain import figures as F
    if codes is None:
        try:
            sys.path.insert(0, str(BASE / "scripts"))
            from minute_incremental_snapshot import candidate_union
            cands, diag = candidate_union(day)
        except Exception as e:  # noqa: BLE001
            return dict(status="no_data", reason=f"候选池不可得: {type(e).__name__}: {e}")
        scope_desc = f"候选池并集({diag['files']} files / {diag['union']} 只)"
    else:
        cands = [str(c) for c in codes if str(c).strip()]
        scope_desc = f"显式标的集({len(cands)} 只)"
    if not cands:
        return dict(status="no_data", reason="无可评估标的（候选池产物缺失或标的集为空）")

    prev_day = _prev_trading_day(day)
    items, no_minute, n_break = [], 0, 0
    feat_cover = {k: 0 for k in ("px_vs_vwap_pct", "up_down_vol_ratio", "low_shift_pct",
                                 "amplitude_pct", "tail_above_vwap")}
    llm_stat = {"consulted": 0, "ok": 0, "degraded": 0, "unreproducible": 0, "skipped": 0}
    sop_d6 = _discretion_def("D6") if use_llm else {}
    for code in cands:
        d = F.load_day_minute(code, day)
        if d is None:
            no_minute += 1
            continue
        facts = F.vwap_facts(d)
        dprev = F.load_day_minute(code, prev_day) if prev_day else None
        feats = F.d6_features(d, dprev)
        broke = F.break_below_vwap(facts)
        n_break += 1 if broke else 0
        for k in feat_cover:
            if feats.get(k) is not None:
                feat_cover[k] += 1
        row = dict(code=code, break_below_vwap=broke,
                   px_vs_vwap_pct=(round(facts["close_last"] / facts["vwap_last"] - 1, 5)
                                   if facts.get("vwap_last") and np.isfinite(facts["vwap_last"]) else None),
                   vwap=round(facts.get("vwap_last") or 0, 4),
                   close=round(facts.get("close_last") or 0, 4),
                   d6_features=feats)
        if use_llm:
            # 候选级调用（协议限流：≤50 只/日）；snapshot 用「D6 输入特征」的哈希
            snap = _sha(dict(code=code, day=day, feats=feats))
            r = llm.consult("D6", date=day, obs=dict(code=code, **feats), snapshot_hash=snap, sop=sop_d6)
            llm_stat["consulted"] += 1
            st = r.get("status")
            if st == "ok":
                llm_stat["ok"] += 1
            elif st == "unreproducible":
                llm_stat["unreproducible"] += 1
            else:
                llm_stat["degraded"] += 1
            row["d6"] = dict(choice=r.get("choice"), score=r.get("score"),
                             reason=r.get("reason"), confidence=r.get("confidence"),
                             status=st, degraded=bool(r.get("_degraded")),
                             # ⭐ R1.6 `discretions[]` 要求的可重放前提三件套
                             model=r.get("model"), prompt_hash=r.get("prompt_hash"),
                             cli_version=r.get("cli_version"))
            row["excluded"] = (r.get("choice") == llm.POINTS["D6"]["veto_choice"])
        else:
            llm_stat["skipped"] += 1
            row["d6"] = None
            row["excluded"] = False
        items.append(row)

    # ⭐ R1.6 `decision.discretions` 是**必填**且进 `replay_hash`（`IMPLEMENTATION_HASH_FIELDS`）——
    #    其字段含 `model` / `prompt_sha256` / `cli_version`，是**可重放性的前提（§4.2）**。
    #    ⚠️ 此前本函数传的是 `discretions=[]`，等于**绕过了一个必填契约**；现按「每被裁量的标的产出一项」填实。
    #    语义收益：**换模型 → model 变 → replay_hash 变**，即「换模型＝决策体身份变更」在摘要层自动生效。
    discretions = []
    if use_llm:
        for x in items:
            d6 = x.get("d6") or {}
            if not d6:
                continue
            discretions.append(dict(
                point_id="D6", output=d6.get("choice"), sym=x["code"],
                rationale=(d6.get("reason") or "")[:REASON_MAX_DIGEST],
                model=d6.get("model") or llm.DEFAULT_MODEL,
                prompt_sha256=(d6.get("prompt_hash") or "")[:16],
                cli_version=d6.get("cli_version") or llm.CLI_VERSION,
                llm_status=d6.get("status"), degraded=bool(d6.get("degraded")),
            ))

    excluded = [x["code"] for x in items if x.get("excluded")]
    return dict(
        status="ok",
        scope=scope_desc,
        n_candidates=len(cands),
        n_with_minute=len(items),
        no_minute=no_minute,
        n_break_below_vwap_fact=n_break,          # 事实计数（**不是**否决结果）
        feature_coverage=feat_cover,
        items=items,
        discretions=discretions,                  # ⭐ 供 R1.6 digest 使用（含 model/prompt_sha256/cli_version）
        llm_enabled=use_llm, llm_stats=llm_stat,
        excluded=excluded,                        # ← 由 D6 的 C_reject 产生
        n_excluded=len(excluded),
        veto_owner="D6",
        produces_exclude=bool(use_llm),
        sop_basis=sop_d6,
        vwap_source=F.VWAP_SOURCE, vwap_why=F.VWAP_WHY, vwap_params=F.VWAP_PARAMS_FROZEN,
        decisable_by=["D6"],
        note=("④段：机械事实 + **D6 裁量**。`n_break_below_vwap_fact` 是**事实计数**，本身不构成排除；"
              "排除只能来自 `D6` 的 `C_reject`（SOP outputs 明文）。LLM 降级时回落保守档 `B_medium`"
              "（可观察不优先、**不排除**）—— 不用被证伪的判据去否决。"
              if use_llm else
              "④段：仅机械事实与特征（`--no-llm`），**不产生任何排除**。"),
    )


def _prev_trading_day(day: str) -> str:
    """前一交易日（用 daily_rebuilt 的日期轴反查，避免自造日历）。"""
    fp = pathlib.Path(r"F:\WorkBuddyItem\a股level2\daily_rebuilt") / "600519.parquet"
    try:
        import pandas as pd
        ds = pd.read_parquet(fp, columns=["date"])["date"].astype(str).tolist()
        ds = sorted(set(ds))
        prev = [d for d in ds if d < day]
        return prev[-1] if prev else ""
    except Exception:
        return ""


# ══════════════════════════════════════════════ ⑤ 稳持仓（实装，只读）
def seg_hold(day: str) -> dict:
    """持仓票的卖点引擎**只读评估** —— 记录「引擎会怎么卖」，**不执行**（供 R3.3 决策差异报告）。"""
    sys.path.insert(0, str(BASE / "portfolio"))
    try:
        from ledger import LEDGER, cost_equity  # noqa: F401
        state = json.loads(pathlib.Path(LEDGER).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return dict(status="no_data", reason=f"账本不可读: {type(e).__name__}: {e}")
    positions = (state.get("account") or {}).get("positions") or {}
    if not positions:
        return dict(status="no_positions", n_positions=0,
                    note="账本空仓 → ⑤ 段无标的可评。**这是正常状态，不是错误**（当前主账本 pos=0）。",
                    decisable_by=["D7", "D9"])

    from core.sell import manage_day
    from decision_chain import figures as F
    prev_day = _prev_trading_day(day)
    out, missing = [], []
    for sym, pos in positions.items():
        d = F.load_day_minute(sym, day)
        if d is None:
            out.append(dict(code=sym, status="no_minute"))
            continue
        need = {}
        for k in ("base0", "stop_px", "low_track"):
            if pos.get(k) is None:
                need[k] = "missing"
        if need:
            missing.append({"code": sym, "fields": sorted(need)})
        prev_close = None
        if prev_day:
            dp = F.load_day_minute(sym, prev_day)
            if dp is not None and not dp.empty:
                prev_close = float(dp["close"].iloc[-1])
        try:
            eng = manage_day(d, prev_close=prev_close or float(d["close"].iloc[0]),
                             base0=int(pos.get("qty", 0)), stop_px=pos.get("stop_px"),
                             low_track=pos.get("low_track"),
                             params=pos.get("params"))
        except Exception as e:  # noqa: BLE001
            out.append(dict(code=sym, status="engine_error", error=f"{type(e).__name__}: {e}"))
            continue
        out.append(dict(code=sym, status="ok",
                        would_sell=eng.get("fills", []),
                        qty_end=eng.get("qty"), low_track_after=eng.get("low_track")))
    return dict(status="ok", n_positions=len(positions), evaluations=out,
                missing_fields=missing,
                executed=False, authority="shadow",
                decisable_by=["D7", "D9"],
                note="⑤段**只读**：取 `manage_day` 的返回但**不调 `ledger.transact`**，"
                     "digest 强制 `executed=False` / `kind=shadow`。"
                     + (f" ⚠️ {len(missing)} 只缺字段，已显式记录、**未猜值**。" if missing else ""))


# ══════════════════════════════════════════════ ⑥ 仓位（实装）
REGIME_CAP = {"bear": (None, 0.20), "neutral": (0.30, 0.50), "bull": (0.50, 0.70)}


def seg_size(day: str, gate_payload: dict | None, entry_payload: dict | None = None,
             use_llm: bool = True) -> dict:
    """动态权益 + **三档 regime 判据** + `GEN-SIZE-04/05` 单笔上限。

    ⚠️ 权益必须用 `ledger.cost_equity`，**禁用 `state['start_cash']`**（`portfolio/ledger.py:214` 明文裁决）。
    """
    sys.path.insert(0, str(BASE / "portfolio"))
    try:
        from ledger import LEDGER, cost_equity
        state = json.loads(pathlib.Path(LEDGER).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return dict(status="no_data", reason=f"账本不可读: {type(e).__name__}: {e}")
    equity = float(cost_equity(state))

    feats = (gate_payload or {}).get("features") or {}
    ev, regime = {}, "neutral"
    # ── 三档判据（合成规则由本增量提出；**每一维的阈值都引到出处**）
    # 维1 情绪档（GEN-GATE-01 分档值 / GEN-GATE-02 冰点模板）
    bucket = feats.get("bucket")
    is_bing = bool(feats.get("is_bingdian"))
    ev["sentiment_bucket"] = dict(value=bucket, is_bingdian=is_bing, source="GEN-GATE-01/02")
    # 维2 指数趋势（全A等权净值 vs MA20/MA60；源＝R0.7 基准臂）
    trend = _index_trend(day)
    ev["index_trend"] = trend
    # 维3 广度（① 段已有）
    med = feats.get("median_pct")
    ev["breadth"] = dict(median_pct=med, up=feats.get("up"), down=feats.get("down"), source="① 段")
    # 合成
    if is_bing or (med is not None and med < -1.0 and trend.get("below_ma60") is True):
        regime = "bear"
    elif (bucket == "活跃" and trend.get("above_ma20") is True
          and trend.get("ma20_up") is True and trend.get("vol_shrink") is False):
        regime = "bull"
    lo, hi = REGIME_CAP[regime]
    cap = dict(lo=lo, hi=hi, chosen=(lo if lo is not None else hi),
               basis="GEN-SIZE-02（牛50~70/震30~50/熊≤20，值来自 风控与仓位总纲.md:291-293）")
    # ── ⭐ R3.2：`D8`「仓位档位选择与弹性调节」由 LLM 在两档区间**内**裁量
    d8 = None
    if use_llm:
        span = [lo if lo is not None else 0.0, hi]
        n_cand = len(((entry_payload or {}).get("items")) or [])
        n_excl = (entry_payload or {}).get("n_excluded", 0)
        obs = dict(regime=regime, cap_band=span, equity=round(equity, 2),
                   n_candidates=n_cand, n_excluded_by_d6=n_excl,
                   market_bucket=feats.get("bucket"), median_pct=feats.get("median_pct"),
                   index_below_ma60=trend.get("below_ma60"), index_above_ma20=trend.get("above_ma20"))
        snap = _sha(dict(seg="SIZE", day=day, obs=obs))
        r = llm.consult("D8", date=day, obs=obs, snapshot_hash=snap,
                        sop=_discretion_def("D8"))
        d8 = dict(choice=r.get("choice"), score=r.get("score"), reason=r.get("reason"),
                  confidence=r.get("confidence"), status=r.get("status"),
                  degraded=bool(r.get("_degraded")),
                  model=r.get("model"), prompt_hash=r.get("prompt_hash"),
                  cli_version=r.get("cli_version"))
        weight = {"floor": 0.0, "mid": 0.5, "ceiling": 1.0}.get(r.get("choice"), 0.0)
        if r.get("status") != "ok":
            weight = 0.0      # 降级 → 区间下沿（少下注）
        cap["chosen_band_position"] = r.get("choice")
        cap["chosen"] = round(span[0] + (span[1] - span[0]) * weight, 4)
    # ── GEN-SIZE-04 × 05
    stop = {"pct": 0.05, "basis": "placeholder_5pct",
            "note": "⚠️ 无候选止损位输入时的**占位**（5%）；有真值时以真值为准"}
    per_trade = min(0.02 * equity / stop["pct"], 0.30 * equity)
    return dict(
        status="ok", equity=round(equity, 2),
        equity_source="ledger.cost_equity（**禁用 start_cash**）",
        regime=regime, regime_evidence=ev,
        cap=cap, per_trade_cap=round(per_trade, 2), stop_distance=stop,
        d8=d8, llm_enabled=use_llm,
        # ⭐ 供 R1.6 digest（含 model/prompt_sha256/cli_version —— 可重放前提）
        discretions=([dict(point_id="D8", output=d8.get("choice"), sym="-",
                           rationale=(d8.get("reason") or "")[:REASON_MAX_DIGEST],
                           model=d8.get("model") or llm.DEFAULT_MODEL,
                           prompt_sha256=(d8.get("prompt_hash") or "")[:16],
                           cli_version=d8.get("cli_version") or llm.CLI_VERSION,
                           llm_status=d8.get("status"), degraded=bool(d8.get("degraded")))]
                      if d8 else []),
        composed=True, calibrated=False,
        composed_note="⚠️ 三档**合成规则**由本增量提出（`composed=true`）；"
                      "但每一维的阈值都有出处：情绪档=GEN-GATE-01/02、趋势=均线体系、广度=① 段。"
                      "`calibrated=false` —— 整体未经标定，归 R1.3/R5.0。",
        decisable_by=["D8"],
        note="⑥段产出 cap 与单笔上限；**档位弹性（D8）留 R3.2**。",
    )


def _index_trend(day: str) -> dict:
    """全A等权净值的趋势维（源＝R0.7 基准臂 `portfolio/benchmarks/arms.json`）。"""
    fp = BASE / "portfolio" / "benchmarks" / "arms.json"
    d = _read_json(fp)
    if not d:
        return dict(available=False, reason="基准臂文件不可读（先跑 build_benchmark_arms.py）")
    arms = d.get("arms") or {}
    series = None
    for k in ("all_a_equal", "small_cap_2000"):
        v = arms.get(k) or {}
        if v.get("points"):
            series = v["points"]
            picked = k
            break
    if not series:
        return dict(available=False, reason="arms.json 内未找到 arms.<name>.points 净值序列")
    # ⚠️ all_a_equal 仅 168 点（series_start 起算）；不足 61 点时退化到可用的另一臂
    if len(series) < 61:
        for k in ("small_cap_2000",):
            v = arms.get(k) or {}
            if v.get("points") and len(v["points"]) >= 61:
                series, picked = v["points"], k
                break
    pts = [(x.get("date"), x.get("nav")) for x in series if isinstance(x, dict) and x.get("date") and x.get("nav")]
    pts = sorted(pts)
    if len(pts) < 61:
        return dict(available=False, reason=f"净值序列仅 {len(pts)} 点（<61，不足以算 MA60）")
    upto = [p for p in pts if p[0] <= day] or pts
    nav = [float(p[1]) for p in upto]
    ma20 = sum(nav[-20:]) / 20
    ma20_prev = sum(nav[-21:-1]) / 21 if len(nav) >= 21 else ma20
    ma60 = sum(nav[-60:]) / 60
    return dict(available=True, arm=picked, as_of=upto[-1][0], nav_last=round(nav[-1], 6),
                ma20=round(ma20, 6), ma60=round(ma60, 6), n_points=len(nav),
                above_ma20=bool(nav[-1] > ma20), below_ma60=bool(nav[-1] < ma60),
                ma20_up=bool(ma20 > ma20_prev),
                # ⚠️ 量能维（GEN-GATE-04 成交额环比）**本增量未实装** → 显式置 None，
                #    因此 `bull` 条件（要求 vol_shrink is False）当前**不可达**。不静默。
                vol_shrink=None,
                vol_shrink_note="量能维未实装（需成交额序列）→ bull 当前不可达",
                source="R0.7 基准臂 arms.json")


STUBS: dict = {}


def run(day: str, use_llm: bool = True) -> dict:
    """跑六段，每段产出 decision_digest。返回整条链的 artifact。"""
    sop_v = _sop_version()
    params = BASE / "config" / "parameters.toml"
    params_hash = hashlib.sha256(params.read_bytes()).hexdigest() if params.exists() else ""
    ts = f"{day} 15:35:00"

    out = dict(meta=dict(
        schema_version="decision-chain/v1",
        date=day, built_at=time.strftime("%Y-%m-%d %H:%M:%S"),
        mode="shadow",                       # ⚠️ 全 shadow：不产生任何成交
        sop_version_id=sop_v, params_hash=params_hash[:16],
        authority="docs/EVOALPHA_V2_RESTRUCTURE_PLAN.md §4.1 + §R3.1",
        discipline="shadow：只产出 artifact，**不产生成交**；LLM 裁量层（R3.2）未接，"
                   "本阶段只备齐各段的特征与硬规则判定",
    ), segments={}, digests={})

    main_payload = None
    gate_payload = None
    entry_payload = None
    for seg, label in SEGMENTS:
        if seg == "GATE":
            payload, rules = seg_gate(day), HARD_RULES["GATE"]
            gate_payload = payload
        elif seg == "MAIN":
            payload, rules = seg_main(day), HARD_RULES["MAIN"]
            main_payload = payload
        elif seg == "DRAGON":
            payload, rules = seg_dragon(day, main_payload), list(FORMULA_RULES.values())
            rules = sorted({FORMULA_RULES[f] for h in payload.get("hits", [])
                            for f in h["formulas"]}) or HARD_RULES["DRAGON"]
        elif seg == "ENTRY":
            payload, rules = seg_entry(day, use_llm=use_llm), HARD_RULES["ENTRY"]
            entry_payload = payload
        elif seg == "HOLD":
            payload, rules = seg_hold(day), HARD_RULES["HOLD"]
        elif seg == "SIZE":
            payload, rules = seg_size(day, gate_payload, entry_payload, use_llm=use_llm), HARD_RULES["SIZE"]
        else:
            payload = dict(STUBS[seg])
            rules = []
        snap = _sha(payload)
        # ⭐ R3.4：本段的 risk_gate 经**三方裁定器**产出（优先级写死：风控 veto > 硬规则(SOP) > LLM 裁量）。
        #    「独立意见可见」＝ 裁定结果里的 `opinions`（三方原始意见，含被否决方）与 `dissent`（异议留痕）
        #    都会随 artifact 落盘，可被 R3.3 决策差异报告与人工复盘引用。
        verdict = arbiter.arbitrate(
            sop=dict(passed=(payload.get("status") in ("ok", "no_positions")),
                     reason=("" if payload.get("status") in ("ok", "no_positions")
                             else str(payload.get("reason") or payload.get("missing") or "")),
                     rules=rules),
            discretion=((payload.get("discretions") or [None])[0]),
            risk=dict(veto=False, reason="", checks=["shadow_mode", "no_order"]),
            point_enums={k: v["choices"] for k, v in llm.POINTS.items()},
            # 各点「代表否决」的枚举（单一事实源＝llm.POINTS[*].veto_choice），
            # 裁定器据此判断 LLM 是否在投否决，进而判定它与结论是否一致（异议留痕用）
            veto_outputs={k: v.get("veto_choice") for k, v in llm.POINTS.items()},
        )
        d = build_digest(
            decision_id=f"dc-{day}-{seg}", day=day, sym="-", side="skip",
            signal_ts=ts, decision_ts=ts, recorded_at=ts,
            candidate_snapshot_id=f"snap-{seg}-{snap[:8]}",
            market_snapshot_hash=snap, sop_version_id=sop_v,
            params_hash=params_hash[:16],
            rules_fired=rules,
            # ⭐ 真实填入裁量记录（含 model/prompt_sha256/cli_version —— R1.6 明文列为可重放前提）。
            #    `decision.discretions` 在 `IMPLEMENTATION_HASH_FIELDS` 内 → **换模型会改 replay_hash**，
            #    即「换模型＝决策体身份变更」自动生效（§4.2 要求模型版本冻结）。
            discretions=payload.get("discretions") or [],
            risk_gate=arbiter.to_risk_gate(verdict),
            order_intent=dict(side="none", qty=0, px_limit=0,
                              reason=f"shadow:{seg}", plan_ref=f"dc-{day}"),
            executed=False, kind="shadow", authority="shadow",
            seq=list(SEGMENTS).index((seg, label)) + 1,
            tick_executor=False,
        )
        errs = validate(d, require_registered_decision=False)
        out["segments"][seg] = dict(label=label, status=payload.get("status"), payload=payload,
                                    snapshot_hash=snap, digest_errors=errs,
                                    # ⭐ R3.4「独立意见可见」：三方意见与异议随 artifact 落盘
                                    arbitration=verdict)
        out["digests"][seg] = dict(digest_id=d["digest_id"], replay_hash=d["replay_hash"])
    out["meta"]["segments_ok"] = sum(1 for s in out["segments"].values() if s["status"] == "ok")
    out["meta"]["segments_stub"] = sum(1 for s in out["segments"].values() if s["status"] == "stub")
    out["meta"]["chain_replay_hash"] = _sha({k: v["replay_hash"] for k, v in out["digests"].items()})
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--replay", action="store_true", help="跑两遍比对 chain_replay_hash")
    ap.add_argument("--no-llm", action="store_true", help="关闭 R3.2 裁量层（只看机械部分）")
    ap.add_argument("--llm-replay", action="store_true", help="额外做一次 LLM 可重放校验（D6/D8）")
    args = ap.parse_args()

    use_llm = not args.no_llm
    a = run(args.date, use_llm=use_llm)
    print(f"决策链 {args.date}  mode={a['meta']['mode']}  sop={a['meta']['sop_version_id']}")
    print(f"  segments_ok={a['meta']['segments_ok']}  stub={a['meta']['segments_stub']}")
    for seg, label in SEGMENTS:
        s = a["segments"][seg]
        print(f"  {label:<12} status={s['status']:<8} snap={s['snapshot_hash'][:8]} "
              f"digest={a['digests'][seg]['digest_id']} err={len(s['digest_errors'])}")
    g = a["segments"]["GATE"]["payload"]
    if g.get("status") == "ok":
        f = g["features"]
        print(f"  ① 特征: zt={f['zt']} dt={f['dt']} **dt7={f['dt7']}** 中位={f['median_pct']} "
              f"涨/跌={f['up']}/{f['down']} 档={f['bucket']} 冰点={f['is_bingdian']} → regime={g['regime']}")
    m = a["segments"]["MAIN"]["payload"]
    if m.get("status") == "ok":
        print(f"  ② 上游: {m['upstream']}")
        print("  ② 主线排序（板块 | 分 | M1/M2/M3 | 主力净额亿 | 涨停/高标/20cm | 20日）")
        for l in m["mainlines"]:
            print(f"     {(l['name'] or l['l2'])[:14]:<16}{l['score']}  "
                  f"{int(l['m1'])}{int(l['m2'])}{int(l['m3'])}  {l['net_main']:>9.2f}  "
                  f"{l['zt']}/{l['max_h']}/{l['cm20']}  {l['ret_20d']*100:>6.1f}%")

    d3 = a["segments"]["DRAGON"]["payload"]
    if d3.get("status") == "ok":
        print(f"  ③ 形态筛: 范围={d3['scan_scope']}（full_market={d3['full_market']}）"
              f" 扫描 {d3['n_scope']} 只 → **命中 {d3['n_hits']} 只**（读失败 {d3['n_read_fail']}）")
        for h in d3["hits"][:12]:
            print(f"     {h['sym']}  [{h['from_']}]  {'+'.join(h['formulas']):<32} close={h['close']:.2f}")

    d4 = a["segments"]["ENTRY"]["payload"]
    if d4.get("status") == "ok":
        print(f"  ④ 找低吸: 候选 {d4['n_candidates']} 只 → 有分钟 {d4['n_with_minute']} / 缺 {d4['no_minute']}"
              f"；**「破均价线」事实计数 {d4['n_break_below_vwap_fact']}**（veto_owner={d4['veto_owner']}，"
              f"produces_exclude={d4['produces_exclude']}）")
        print(f"     D6 五项特征覆盖: {d4['feature_coverage']}")
        ls = d4.get("llm_stats") or {}
        print(f"     ⭐ D6 裁量（LLM）：consulted={ls.get('consulted')} ok={ls.get('ok')} "
              f"degraded={ls.get('degraded')} unreproducible={ls.get('unreproducible')}"
              f" → **排除 {d4.get('n_excluded')} 只**（C_reject）")
        for it in (d4.get("items") or [])[:6]:
            dd = it.get("d6") or {}
            print(f"       {it['code']}  D6={dd.get('choice')} score={dd.get('score')} "
                  f"({'排除' if it.get('excluded') else '保留'})  {str(dd.get('reason'))[:40]}")
    else:
        print(f"  ④ 找低吸: {d4.get('status')} — {d4.get('reason','')}")

    d5 = a["segments"]["HOLD"]["payload"]
    print(f"  ⑤ 稳持仓: {d5.get('status')}"
          + (f"（持仓 {d5.get('n_positions')} 只）" if d5.get("status") == "ok" else "")
          + (f" — {d5.get('note','')[:60]}" if d5.get("status") == "no_positions" else ""))

    d6p = a["segments"]["SIZE"]["payload"]
    if d6p.get("status") == "ok":
        c = d6p["cap"]
        print(f"  ⑥ 仓位: equity={d6p['equity']}（{d6p['equity_source']}）→ regime=**{d6p['regime']}**"
              f" → cap={c['lo']}~{c['hi']}｜单笔上限={d6p['per_trade_cap']}")
        e = d6p["regime_evidence"]
        print(f"     逐维: 情绪档={e['sentiment_bucket']['value']}(冰点={e['sentiment_bucket']['is_bingdian']})"
              f" · 趋势={e['index_trend'].get('available')} · 广度中位={e['breadth']['median_pct']}%")
    else:
        print(f"  ⑥ 仓位: {d6p.get('status')} — {d6p.get('reason','')}")

    outdir = BASE / "outputs" / "decision_chain"
    outdir.mkdir(parents=True, exist_ok=True)
    p = outdir / f"chain_{args.date}.json"
    p.write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ {p} ({p.stat().st_size}B)")

    rc = 0
    if args.replay:
        b = run(args.date, use_llm=use_llm)
        same = a["meta"]["chain_replay_hash"] == b["meta"]["chain_replay_hash"]
        dr = [seg for seg in a["digests"] if a["digests"][seg]["replay_hash"] != b["digests"][seg]["replay_hash"]]
        print(f"重放一致性: chain_hash {'一致 ✓' if same else '不一致 ✗'}；分段漂移={dr}")
        print(f"  不可重放率 = {len(dr)}/{len(SEGMENTS)}")
        rc = 0 if same and not dr else 1

    if args.llm_replay:
        from decision_chain import llm as _llm
        e = a["segments"]["ENTRY"]["payload"]
        done = 0
        for it in (e.get("items") or []):
            r = _llm.replay_check("D6", date=args.date, obs=dict(code=it["code"], **it["d6_features"]),
                                  snapshot_hash=_sha(dict(code=it["code"], day=args.date,
                                                          feats=it["d6_features"])),
                                  sop=_discretion_def("D6"))
            done += 1 if r.get("reproducible") else 0
            if done >= 3:            # 抽样 3 只，控制成本
                break
        print(f"LLM 可重放抽样: {done}/3 复现一致")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
