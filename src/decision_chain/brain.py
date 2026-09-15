"""brain 层 —— **LLM 作为选手人格**，判「买不买 / 卖不卖 / 卖多少」。

## 架构（2026-09-13 用户确立）

> **机械 = 眼睛 + 尺子**（选标的、算位置、硬约束）· **LLM = 大脑 = 选手人格**（盘中拍板）

- **eye 事实** → `decision_chain.facts`（本模块的输入，只给事实不给判断）
- **brain 判断** → 本模块
- 依据 `docs/RULE_ROLE_SPLIT.md`：「决定买卖」的 86 条规则中 **54 条是 brain**（判断），30 条是 eye（事实）。

## 本模块当前覆盖

**卖出侧（裁量点 `D11`）** —— 此前卖出是**纯机械固定动作**（`core.sell::manage_day`），
但 SOP 里「**走不走 / 减半还是全走**」本质是判断（`GEN-HOLD-05` 10 点纪律 · `GEN-HOLD-06` 打板逻辑 ·
`GEN-HOLD-01/04/40` 均线纪律等）。现在改为：

    机械算出卖点（破 MA5/MA10/破 VWAP/10 点未板…）
        → 本模块把**事实**喂给 LLM（选手人格）
        → LLM 判 `clear_all`（清）/ `halve`（减半）/ `hold`（不动）
        → 调用方据此执行

⚠️ **降级语义**：LLM 不可用/输出不过 schema/异常 ⇒ **一律回 `hold`（不卖）**。
   卖出不可逆，降级时**不得擅自清仓**；与 D6「降级=放行买入」同族 —— 都是「退回本次改动之前的行为」。
⚠️ **锁定粒度**：`sha256(scope|day|code|trigger)` —— 同一标的**同一种卖点**当日只真调一次
   （不同卖点类型可分别判，通常一天 1–2 次），避免每分钟重复调用与中途改判。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent.parent
for _p in (str(BASE), str(BASE / "src"), str(BASE / "scripts"), str(BASE / "portfolio")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from decision_chain import facts as _facts  # noqa: E402
from decision_chain import intraday_veto as _veto  # noqa: E402
from decision_chain import llm as _llm  # noqa: E402

SCOPE = "intraday-d11-v1"
OUT_DIR = BASE / "outputs" / "decision_chain" / "brain"
POINT = "D11"


def _snapshot_hash(code: str, day: str, trigger: str) -> str:
    """按（标的 × 日 × 卖点类型）锁定 —— 键稳定 + TTL 到收盘 ⇒ 当日不重复真调、不中途改判。"""
    return hashlib.sha256(f"{SCOPE}|{day}|{code}|{trigger}".encode("utf-8")).hexdigest()


# ⭐ 卖点类型 → 对应 SOP 规则 id。
#   ⚠️ **必须喂 SOP 原语**：否则 LLM 是**凭常识**判，不是**按选手纪律**判 ——
#   实测（2026-09-11 诺普信 / trigger=below_ma10）：不喂 SOP 时 LLM 判 `halve`（理由合理但与选手不符），
#   而选手 SOP `GEN-HOLD-40` 是「破 10 日线**先走**」、实盘也确实**清仓**。
#   ⇒ 喂 SOP 才是「选手人格」，否则只是一个聪明的交易员。
#   ⚠️⚠️ 2026-09-13 修正（P1）：**键必须用生产 reason 字面量**。此前字典用的是本模块自造命名
#     （`zhaban`/`below_ma5`/`break_vwap`…），而 `core.sell::manage_day` 实际吐的是
#     `zhaban_sell`/`ma5_halve`/`vwap_halve`… ⇒ `TRIGGER_SOP.get(trigger)` 大面积返回 None
#     ⇒ `_load_sop(None)` 返回 `{}` ⇒ **静默退化成"不喂 SOP"**，正是上面那个事故的形态。
#   映射依据一律取自 `persona/sop_v0.toml` 的 `eng` 字段（规则 ↔ 生产触发的显式绑定），非语义猜测。
TRIGGER_SOP = {
    # ── 生产 reason（10 个）= tick_monitor 实际会发出的全集 ──────────────────
    #    依据 config/parameters.toml [sell.intraday] + tick_monitor 强制关闭项
    #    （ten_oclock=false、t_enabled=false、dragon_link_sell=false、sector_retreat_sell=false）
    "stop_loss":      "GEN-HOLD-28",   # −5% 低开止损（eng=stop_loss_pct）
    "vwap_halve":     "GEN-HOLD-05",   # ⚠️ 弱挂：该规则动作是「走」≠「减半」（见 SOP_WEAK）
    "vwap_break_all": "",              # ⚠️ sop_v0.toml 全文零命中，**无对应规则**（见 SOP_WEAK）
    "break_low":      "GEN-HOLD-32",   # eng=break_low_clear（note 明写「触发 reason=break_low」）
    "profit_take":    "GEN-HOLD-07",   # 分批止盈 10~20% 走 1/3~1/2
    "zhaban_sell":    "GEN-HOLD-08",   # 尾盘炸板 = 主力想撤退（eng=zhaban_sell）
    "second_high":    "GEN-HOLD-09",   # 次高点卖出（eng=second_high_sell）
    "ma10_clear":     "GEN-HOLD-01",   # eng 逐字含 daily_ma10_clear
    "ma5_halve":      "GEN-HOLD-01",   # eng 逐字含 daily_ma5_halve
    "time_stop":      "GEN-HOLD-16",   # 持股周期（eng=time_stop_days）
    # ── legacy 键（非生产 token；保留以兼容症状回放 / 未来重启用，勿删）────────
    #    ⚠️ below_ma10 → GEN-HOLD-40 是 9/11 诺普信事故的回归锚点，不可移除。
    #    ⚠️ 注意 GEN-HOLD-40 的 eng 实为**空**（L1216），故 ma10_clear 主挂 GEN-HOLD-01。
    "ten_oclock": "GEN-HOLD-05",     # 10 点纪律（生产配置为 false）
    "break_vwap": "GEN-HOLD-05",     # 分时跌破均价线就走
    "below_ma5": "GEN-HOLD-01",      # 破 5 日线减半
    "below_ma10": "GEN-HOLD-40",     # 破 10 日线先走（20 日线是最后防线）
    "below_ma20": "GEN-HOLD-40",
    "zhaban": "GEN-HOLD-08",
    "structure_break": "GEN-HOLD-22",  # 止损锚 = 结构（均线 + 量能）
    "expectation_fulfilled": "GEN-HOLD-37",  # 前高 + 大阴线爆巨量
}

# ⚠️ **弱依据**：喂给 LLM 的 SOP 与卖点语义**不完全对齐**（动作/出处不合）—— 落盘打标，
#    便于事后**单独统计「喂了弱依据的判定」的质量**，而不是把它混进正常判定的统计里。
#    · `vwap_halve`    ：GEN-HOLD-05 后半句「分时跌破均价线就走」动作是"走"，非"减半"；
#    · `vwap_break_all`：SOP 全文零命中，sop 为空 —— 仍走 brain（用户 2026-09-13 决定），
#                        但必须靠本标记把它与有依据的判定区分开。
SOP_WEAK = {"vwap_halve", "vwap_break_all"}


def sop_for(trigger: str) -> tuple[str, bool]:
    """`trigger` → (规则 id, 是否弱依据)。**未登记 reason 也返回弱依据** —— 天然完成
    `sop_missing` 留痕（否则新增卖点类型会像 9/11 那样静默退化）。"""
    rule = TRIGGER_SOP.get(trigger) or ""
    return rule, bool(trigger in SOP_WEAK or not rule)
_SOP_CACHE: dict = {}


def _load_sop(rule_id: str | None) -> dict:
    """从真相源 `persona/sop_v0.toml` 读该规则的原语（不硬编码）。失败返回 {}。"""
    if not rule_id:
        return {}
    if rule_id in _SOP_CACHE:
        return _SOP_CACHE[rule_id]
    out: dict = {}
    try:
        import tomllib
        doc = tomllib.loads((BASE / "persona" / "sop_v0.toml").read_text(encoding="utf-8"))
        for r in doc.get("rule") or []:
            if r.get("id") == rule_id:
                out = dict(stmt=r.get("stmt"), quote=r.get("stmt"), outputs=[],
                           src=r.get("src"), ev=r.get("ev"))
                break
    except Exception:  # noqa: BLE001
        out = {}
    _SOP_CACHE[rule_id] = out
    return out


def sell_verdict(code: str, *, day: str, trigger: str, df=None, state=None,
                 entry_px: float | None = None, now=None, use_llm: bool = True,
                 persist: bool = True) -> dict:
    """机械已触发卖点 → 由 LLM（选手人格）判定**轻重**。

    :param trigger: 机械触发类型，如 `ten_oclock` / `break_vwap` / `below_ma5` /
                    `below_ma10` / `zhaban` / `stop_loss` / `second_high`
    :return: dict（`action` ∈ clear_all / halve / hold；含 `reason` 与全部 `facts`）

    ⚠️ 本函数**绝不抛异常**（异常 ⇒ `hold`），调用方无需再包 try。
    """
    now = now or _dt.datetime.now()
    # sop_rule / sop_weak 不依赖 LLM ⇒ 在初始化就填好（use_llm=False 时也留痕，
    #   便于事后统计「有多少判定是在没喂 SOP 的情况下做出的」）。
    _rule, _weak = sop_for(trigger)
    out = dict(code=code, day=day, trigger=trigger, action="hold", choice=None,
               score=None, reason="", status="skipped", degraded=False, cache_hit=False,
               model=None, ts=now.strftime("%Y-%m-%d %H:%M:%S"),
               sop_rule=_rule, sop_weak=_weak)
    try:
        pack = _facts.fact_pack(code, day, df=df, state=state, entry_px=entry_px)
        out["facts"] = pack
        if not use_llm:
            out["status"] = "llm_disabled"
        else:
            # 只喂**与卖出决策相关**的事实（不给无关噪声）
            obs = dict(trigger=trigger, 持仓=pack.get("holding"), 位置=pack.get("position"),
                       分时=pack.get("intraday"), 环境=pack.get("env"))
            snap = _snapshot_hash(code, day, trigger)
            # ⚠️ `_rule` 由 sop_for() 给出：**未登记/弱依据的卖点也要如实喂**（空 dict 即"没依据"），
            #    并已由 out["sop_weak"] 打标 —— 不再有"静默不喂 SOP"的中间态。
            r = _llm.consult(POINT, date=day, obs=obs, snapshot_hash=snap,
                             sop=_load_sop(_rule) if _rule else {},
                             cache_ttl=_veto.lock_ttl(now))
            ch = r.get("choice")
            out.update(choice=ch, score=r.get("score"), reason=r.get("reason") or "",
                       status=r.get("status"), degraded=bool(r.get("_degraded")),
                       cache_hit=bool(r.get("cache_hit")), model=r.get("model"))
            # ⚠️ 只有 status=ok **且** choice 落在枚举内才采纳；否则维持 hold（保守）
            if r.get("status") == "ok" and ch in _llm.POINTS[POINT]["choices"]:
                out["action"] = ch
            else:
                out["action"] = "hold"
    except Exception as e:  # noqa: BLE001
        out.update(action="hold", status="error", reason=f"exception:{type(e).__name__}")
    if persist:
        try:
            record(day, out)
        except Exception:  # noqa: BLE001
            pass
    return out


def record(day: str, rec: dict) -> pathlib.Path:
    """落盘（append jsonl）—— 与 R4.1a 同族的「判定留痕」；完整 R1.6 digest 接线属后续。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fp = OUT_DIR / f"brain_{day}.jsonl"
    with fp.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    return fp


def summarize(day: str) -> dict:
    """当日 brain 判定汇总（供状态卡/复盘）。"""
    fp = OUT_DIR / f"brain_{day}.jsonl"
    rows = []
    if fp.exists():
        try:
            for line in fp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            pass
    import collections
    return dict(day=day, n=len(rows),
                actions=dict(collections.Counter(r.get("action") for r in rows)),
                degraded_pass=sum(1 for r in rows if r.get("action") == "hold"
                                  and r.get("status") != "ok"),
                file=str(fp) if fp.exists() else "")
