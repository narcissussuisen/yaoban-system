"""选股链条 S2「竞价初筛」/ S3「观察名单」—— **纯函数层**。

【为什么有这个模块】
选手的原话作息（`选手学习资料/选手战法画像-累计.md:112-126`，三处表述互证）：
    07:00 看全球新闻 → 09:00 盘前观点 → **09:20 看集合竞价做初步筛选**
    → **09:30 用战法筛选三只观察**
而系统现状：竞价数据每天落盘（`outputs/auction/auction_freeze_<day>.json`）但**决策链零消费**；
观察名单层根本不存在（`PATTERN_QUEUE_MAX=15` 是 `scan_and_confirm.py:58` 的设计值）。
本模块补齐 S2/S3 两段，**只产出留痕产物，不接入任何买卖判定**。

【三条纪律（照本仓已踩过的坑）】
1. **不引入未标定阈值**：S2 不产出任何硬性 keep/drop 策略判定，
   只产**特征 + 池内分位**（`turn_rank` / `amt_rank` / `chg_rank`）。
   ⇒ 因为「高开多少算高」「量多大算有量」目前**没有标定证据**，
     按本仓纪律不得凭直觉写死；先落痕、攒样本，再谈准入。
2. **数据缺失放行 + 标注**：无行情的标的只标 `drop_code='NO_QUOTE'`、`keep=False`（技术性：没特征没法排序），
   **绝不**因此禁用整条链路（否则重演 `rc=8` 全天零样本）。
3. **纯函数**：入参 dict/list，出参 dict/list；不读磁盘、不依赖 `core.strategies`、无网络 ⇒ 可离线重放。

【与生产排序的关系】
S3 的排序键**刻意与 `scan_and_confirm.py:420-426` 的语义对齐**（in_plan → 信号新鲜度 → 共振数 → 量能），
唯一新增项是「竞价量能分位」，且**放在最后**（未标定者不得压过有出处者）。
"""
from __future__ import annotations

from typing import Any, Iterable

# S2 的排除码：**只有技术性原因**，不含任何策略判定（见模块 docstring 纪律 1）
DROP_NO_QUOTE = "NO_QUOTE"        # 竞价快照里没有该标的（停牌/未授权/ST 被滤）⇒ 无法评估，非策略否决
DROP_NO_TURN = "NO_TURN"          # 有行情但换手/额字段缺失 ⇒ 量能无法计算

# 默认观察名单规模。依据：选手「09:30 用战法筛选**三只**观察」（画像:119）
#   与 SOP `GEN-DRAGON-05`「候选 ≤5」⇒ 取 5（上界），实际落痕同时给出 n_observed。
DEFAULT_TARGET_N = 5


def _f(x: Any) -> float | None:
    """安全转 float；None/'' /非数值 → None。"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None       # 过滤 NaN


def pct_rank(values: Iterable[Any]) -> list[float]:
    """按升序算**池内分位**（0..1，越大越靠前=数值越大）。

    口径：`rank / (n-1)`，最小者 0、最大者 1；并列取平均名次；n<=1 或常量 ⇒ 全给 0.5
    （常量时无区分度，0.5 是"不可分辨"的诚实表达，而不是编造 0/1）。
    `None` 一律给 0.5 并把它们排到中间（不参与名次计算），避免缺失值伪装成极值。
    """
    seq = list(values)
    idx = [i for i, v in enumerate(seq) if _f(v) is not None]
    n = len(idx)
    out = [0.5] * len(seq)             # 缺失/不可分辨一律 0.5（诚实表达"无区分度"）
    if n <= 1:
        return out
    vals = [(i, _f(seq[i])) for i in idx]
    order = sorted(vals, key=lambda t: t[1])
    # 并列取平均名次
    ranks: dict[int, float] = {}
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and order[j + 1][1] == order[i][1]:
            j += 1
        avg = (i + j) / 2.0
        for k in range(i, j + 1):
            ranks[order[k][0]] = avg
        i = j + 1
    span = float(n - 1)
    for i in idx:
        out[i] = ranks[i] / span
    return out


def build_auction_screen(pool_records: list[dict], quotes_by_sym: dict,
                         target_n: int = DEFAULT_TARGET_N) -> dict:
    """S2：把「昨日战法池」逐只对齐到**当日竞价快照**，产出特征 + 池内分位。

    pool_records   `outputs/patterns/<day>_pattern_pool.json::pool`（asof=T−1）
    quotes_by_sym  `auction_monitor` 拉到的全市场竞价快照 {sym: {name,px,chg,vol,amt,turn}}
    target_n       仅用于回传（S3 用），此处不影响行内容

    返回 {'rows':[...], 'stats':{...}, 'n_target':target_n}
      rows 每行：sym/name/pattern/patterns/sig_date/bars_since_sig/close_asof/
                 has_quote/keep/drop_code/auc_px/auc_chg/auc_amt_wan/auc_turn/
                 turn_rank/amt_rank/chg_rank
    ⚠️ `keep` 只表示"有竞价特征、可参与 S3 排序"，**不是**买卖许可（本模块不产许可）。
    """
    recs = [r for r in (pool_records or []) if isinstance(r, dict) and r.get("sym")]
    rows: list[dict] = []
    for r in recs:
        sym = str(r["sym"])
        q = (quotes_by_sym or {}).get(sym) or {}
        turn = _f(q.get("turn"))
        amt = _f(q.get("amt"))
        chg = _f(q.get("chg"))
        has_quote = bool(q) and q.get("px") is not None
        drop = None
        if not has_quote:
            drop = DROP_NO_QUOTE
        elif turn is None and amt is None:
            drop = DROP_NO_TURN
        rows.append({
            "sym": sym,
            "name": q.get("name") or "",
            "pattern": r.get("pattern"),
            "patterns": list(r.get("patterns") or []),
            "sig_date": r.get("sig_date"),
            "bars_since_sig": r.get("bars_since_sig"),
            "close_asof": _f(r.get("close_asof")),
            "has_quote": has_quote,
            "keep": drop is None,                 # 技术性放行：有特征才可排序
            "drop_code": drop,
            "auc_px": _f(q.get("px")),
            "auc_chg": chg,
            "auc_amt_wan": amt,
            "auc_turn": turn,
        })
    # 分位只在「有行情」的子集内计算（缺失值不参与名次，见 pct_rank docstring）
    live = [x for x in rows if x["has_quote"]]
    tr = pct_rank([x["auc_turn"] for x in live])
    ar = pct_rank([x["auc_amt_wan"] for x in live])
    cr = pct_rank([x["auc_chg"] for x in live])
    for x, a, b, c in zip(live, tr, ar, cr):
        x["turn_rank"], x["amt_rank"], x["chg_rank"] = round(a, 4), round(b, 4), round(c, 4)
    for x in rows:
        x.setdefault("turn_rank", None)
        x.setdefault("amt_rank", None)
        x.setdefault("chg_rank", None)
    stats = {
        "n_pool": len(rows),
        "n_quoted": len(live),
        "n_no_quote": sum(1 for x in rows if x["drop_code"] == DROP_NO_QUOTE),
        "n_no_turn": sum(1 for x in rows if x["drop_code"] == DROP_NO_TURN),
        "by_pattern": {},
    }
    for x in rows:
        p = x.get("pattern") or "?"
        stats["by_pattern"][p] = stats["by_pattern"].get(p, 0) + 1
    return {"rows": rows, "stats": stats, "n_target": int(target_n)}


def build_watchlist(screen: dict, target_n: int = DEFAULT_TARGET_N) -> dict:
    """S3：从 S2 的 keep 行里排序取前 `target_n` 只，产出**观察名单**留痕。

    排序键（全部有出处；新增项放最后，见模块 docstring）：
      ① 日计划内优先（`in_plan` —— 与 `scan_and_confirm.py:420` 同口径，需上游注入该字段）
      ② 信号新鲜度（`bars_since_sig` 小者优先）⚠️ **只作排序权重，绝不作门槛**
         （实测「只留 T−1 当日信号」会把选手样本全筛掉，`pattern_pool.py:120-124`）
      ③ 多战法共振数多者优先（同为权重：实测「≥2 共振」会让选手只剩 1/4）
      ④ **竞价量能分位**降序（`turn_rank`；「有没有量」是选手的换手量纲，尚未标定 ⇒ 排最后）
      ⑤ 竞价额（tiebreak）
      ⑥ sym（保证可复现）
    """
    rows = [x for x in (screen or {}).get("rows", []) if x.get("keep")]
    rows.sort(key=lambda x: (
        not bool(x.get("in_plan")),
        x.get("bars_since_sig") if x.get("bars_since_sig") is not None else 99,
        -len(x.get("patterns") or []),
        -(x.get("turn_rank") if x.get("turn_rank") is not None else 0.5),
        -(x.get("auc_amt_wan") or 0.0),
        str(x.get("sym")),
    ))
    n = max(0, int(target_n))
    picks = rows[:n]
    return {
        "n_target": n,
        "n_observed": len(rows),          # 选手「筛选数量=市场温度计」的可读数（画像:1178）
        "rank_key": ["in_plan", "bars_since_sig", "n_resonance", "turn_rank_desc", "amt_desc"],
        "picks": picks,
        "note": ("观察名单为**留痕产物**，不接入买卖判定；排序键与 scan_and_confirm 的 confirm_queue 对齐，"
                 "唯一新增项（竞价量能分位）置于最后，因尚无标定证据。"),
    }


def build_sentiment_pre(market_rows: list[dict], pool_rows: list[dict]) -> dict:
    """S0/S2 情绪档：用**当日竞价快照**算一个盘前情绪画像（对应 SOP 第②档）。

    依据：`persona/sop_v0.toml:291` 与 `scan_and_confirm.py:496-498` 都自认
    「用户裁定的正确顺序是 ① 早盘外盘 → ② 早盘集合竞价 → ③ 前日收盘，**当前只实现了 ③**」。
    本函数落的正是第 ② 档，**只留痕、不参与温度闸**（温度闸仍用 T−1，未标定不擅改）。
    """
    mk = [r for r in (market_rows or []) if _f(r.get("chg_pct")) is not None]
    ups = sum(1 for r in mk if _f(r["chg_pct"]) > 0)
    downs = sum(1 for r in mk if _f(r["chg_pct"]) < 0)
    flats = len(mk) - ups - downs
    amt_total = sum(_f(r.get("amount_wan")) or 0.0 for r in mk)
    pool_chg = sorted(v for v in (_f(r.get("auc_chg")) for r in (pool_rows or [])) if v is not None)
    def _q(p: float) -> float | None:
        if not pool_chg:
            return None
        k = (len(pool_chg) - 1) * p
        lo, hi = int(k), min(int(k) + 1, len(pool_chg) - 1)
        return round(pool_chg[lo] + (pool_chg[hi] - pool_chg[lo]) * (k - lo), 4)
    by_pattern: dict[str, dict] = {}
    for r in (pool_rows or []):
        p = r.get("pattern") or "?"
        v = _f(r.get("auc_chg"))
        if v is None:
            continue
        d = by_pattern.setdefault(p, {"n": 0, "chg_sum": 0.0})
        d["n"] += 1
        d["chg_sum"] += v
    for p, d in by_pattern.items():
        d["median_chg"] = round(d.pop("chg_sum") / max(1, d["n"]), 4)
    return {
        "tier": "auction",                       # 对应 SOP 三档里的第②档
        "n_market": len(mk),
        "n_up_open": ups, "n_down_open": downs, "n_flat_open": flats,
        "up_open_ratio": round(ups / len(mk), 4) if mk else None,
        "amt_total_wan": round(amt_total, 1),
        "pool_n": len(pool_chg),
        "pool_median_chg": _q(0.5), "pool_chg_q25": _q(0.25), "pool_chg_q75": _q(0.75),
        "by_pattern": by_pattern,
        "note": ("仅留痕；温度闸仍读 T−1（sentiment_full_2026.csv 末行）。"
                 "本档用于事后检验「竞价档是否有增量」，有区分度才谈准入。"),
    }
