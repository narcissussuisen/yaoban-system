"""eye 事实层 —— 代码算出的**事实 / 位置 / 数值**（供 LLM 大脑决策使用）。

## 为什么有这个模块

依据 `docs/RULE_ROLE_SPLIT.md`（2026-09-13）：SOP 中「决定买卖」的三段 86 条规则里，
**eye（代码算事实）30 条 · brain（LLM 判决定）54 条**。架构共识是：

> **机械 = 眼睛 + 尺子**（选标的、算位置）· **LLM = 大脑 = 选手人格**（盘中拍板买/卖/卖多少）

⇒ 本模块负责**眼睛**：把 brain 规则所需的输入算准（`RULE_ROLE_SPLIT.md §二` 的事实清单）。
brain 的判断质量完全取决于这里的输入是否齐全准确。

## 与既有设施的关系（不重复造轮子）

- **分时事实** → 复用 `decision_chain.figures`（VWAP / D6 特征 / 破均价线事实，已有且已冻结口径）
- **环境事实** → 读 `outputs/sentiment_full_2026.csv`（真相源）与 R2.2/R2.3 产物
- **本模块新增**：`position_facts()`（个股位置）—— 此前只在散落脚本里各算一份，无统一出口

⚠️ **口径纪律**：本模块只产出**事实**，**不做判断、不给建议、不投否决票**（判断属 brain）。
"""
from __future__ import annotations

import datetime as _dt
import pathlib
import sys

import numpy as np

BASE = pathlib.Path(__file__).resolve().parent.parent.parent
for _p in (str(BASE), str(BASE / "src"), str(BASE / "scripts"), str(BASE / "portfolio")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _kdj(high, low, close, n: int = 9, k_smooth: int = 3, d_smooth: int = 3) -> dict:
    """KDJ(9,3,3) —— SOP `GEN-HOLD-27` 指定参数；K<20 为超卖区。

    标准算法：RSV = (C − L_n) / (H_n − L_n) × 100；K = 2/3·K_prev + 1/3·RSV；D 同理；J = 3K − 2D。
    ⚠️ 只给**当前值**与**是否死叉**（K 下穿 D）这两个事实，不作卖出判断。
    """
    h, l, c = np.asarray(high, float), np.asarray(low, float), np.asarray(close, float)
    if len(c) < n + 2:
        return {"k": None, "d": None, "j": None, "dead_cross": None}
    rsv = np.full(len(c), np.nan)
    for i in range(n - 1, len(c)):
        hn, ln = h[i - n + 1:i + 1].max(), l[i - n + 1:i + 1].min()
        rsv[i] = 50.0 if hn == ln else (c[i] - ln) / (hn - ln) * 100.0
    k = d = 50.0
    ks, ds = [], []
    for x in rsv:
        if not np.isfinite(x):
            ks.append(k)
            ds.append(d)
            continue
        k = (2.0 / 3.0) * k + (1.0 / 3.0) * x
        d = (2.0 / 3.0) * d + (1.0 / 3.0) * k
        ks.append(k)
        ds.append(d)
    j = 3 * ks[-1] - 2 * ds[-1]
    dead = bool(ks[-2] >= ds[-2] and ks[-1] < ds[-1]) if len(ks) >= 2 else None
    return {"k": round(ks[-1], 2), "d": round(ds[-1], 2), "j": round(j, 2),
            "dead_cross": dead, "oversold": bool(ks[-1] < 20)}


def position_facts(code: str, day: str, entry_px: float | None = None,
                   prev_high_lookback: int = 120) -> dict:
    """个股**位置事实**（`RULE_ROLE_SPLIT.md §二「个股位置」组`）。

    产出：均线体系（MA5/10/20/60）· 收盘相对各均线 · 乖离率 bias5 · KDJ(9,3,3) ·
         前期高点（默认回看 120 日）· 距前高 % · 形态 bar 高低 · （可选）相对成本价盈亏。

    ⚠️ 全部为**事实**，不含任何买卖判断。数据不足时对应项返回 None（不抛异常、不猜）。
    """
    out: dict = {"code": code, "day": day}
    try:
        sys.path.insert(0, str(BASE / "scripts"))
        from core.daily_src import load_daily  # 日线源：F:/WorkBuddyItem/a股level2/daily_rebuilt
        df = load_daily(code)
    except Exception as e:  # noqa: BLE001
        return {**out, "error": f"load_daily_failed:{type(e).__name__}"}
    if df is None or len(df) < 6:
        return {**out, "error": "no_daily"}

    # ⚠️⚠️ **按 `day` 截断**：`load_daily` 返回的是**全序列**（会随新交易日增长）。
    #    不截断 ⇒ 若数据已更新到 `day` 之后，就会把「未来」的位置算进事实里（**事实层同样禁止前视**）。
    if "date" in df.columns:
        df = df[df["date"].astype(str) <= day]
        if len(df) < 6:
            return {**out, "error": "no_data_asof"}

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    last = float(close.iloc[-1])

    # ① 均线体系六级（5/10/20/30/60）—— SOP `GEN-HOLD-39`
    ma = {}
    for n in (5, 10, 20, 30, 60):
        ma[f"ma{n}"] = round(float(close.iloc[-n:].mean()), 4) if len(df) >= n else None
    out["ma"] = ma

    # ② 收盘相对各均线（%）—— 「上方=支撑/上升趋势，下方=压力/不玩」
    out["close_vs_ma_pct"] = {k: (round((last / v - 1) * 100, 3) if v else None)
                              for k, v in ma.items()}

    # ③ 乖离率 bias5 —— SOP `GEN-HOLD-26`（⚠️「极大值」阈值未标定，此处只给数）
    out["bias5_pct"] = round((last / ma["ma5"] - 1) * 100, 3) if ma.get("ma5") else None

    # ④ KDJ(9,3,3) —— SOP `GEN-HOLD-27`
    out["kdj"] = _kdj(high.to_numpy(), low.to_numpy(), close.to_numpy())

    # ⑤ 前期高点（最大压力位）与距前高 % —— SOP `GEN-HOLD-37/38`（预期兑现 / 持有理由）
    lb = min(prev_high_lookback, len(df) - 1)
    if lb > 0:
        ph = float(high.iloc[-lb - 1:-1].max())
        out["prior_high"] = round(ph, 4)
        out["prior_high_lookback"] = lb
        out["dist_to_prior_high_pct"] = round((ph / last - 1) * 100, 3) if last else None
    else:
        out["prior_high"] = out["dist_to_prior_high_pct"] = None

    # ⑥ 最近一根 bar 与形态 bar（供 P3「止损锚 = 长上影当天最低价 − ε」类规则）
    out["last_bar"] = {"high": round(float(high.iloc[-1]), 4),
                       "low": round(float(low.iloc[-1]), 4),
                       "close": round(last, 4)}
    # ⑦ 相对成本价盈亏（有底仓时）—— 事实，不是判断
    if entry_px:
        out["pnl_vs_entry_pct"] = round((last / float(entry_px) - 1) * 100, 3)
    return out


# ══════════════════════════════════════════════════════════════════════
#  ② 环境事实（①环境闸门段的 eye 输入）
# ══════════════════════════════════════════════════════════════════════
def env_facts(day: str) -> dict:
    """当日**环境事实**（真相源：`outputs/sentiment_full_2026.csv`）。

    ⚠️ 取 **≤ day 的最近一行**（与 `store.sentiment_as_of` 同口径）——盘前只有 T−1 可知，
       若直接取末行会在回看历史时**误用未来数据**（同一族的「前视」陷阱）。
    ⚠️ 已知缺口（**待补，不假装已有**）：`eq_weight_index_ret`（全A等权 vs 加权）、
       `overnight_us_tech_up`（隔夜外盘）、`bz50_excess_ret_pulse`（北证脉冲）、
       `first_board_premium`（首板次日溢价，`GEN-GATE-18`）—— 这些**数据源尚未接入**。
    """
    out: dict = {"day": day}
    try:
        import pandas as pd
        fp = BASE / "outputs" / "sentiment_full_2026.csv"
        sf = pd.read_csv(fp)
        sub = sf[sf["date"].astype(str) <= day]
        if sub.empty:
            return {**out, "error": "no_sentiment_asof"}
        r = sub.iloc[-1]
        from core.sentiment import emotion_thermometer
        _dt7 = r.get("dt7_count")
        th = emotion_thermometer(
            int(r["zt"]), int(r["dt"]) if pd.notna(r["dt"]) else None,
            float(r["zhaban_rate"]), int(r["max_h"]),
            float(r["lianban_rate"]) if pd.notna(r["lianban_rate"]) else None,
            float(r["median_pct"]) if pd.notna(r["median_pct"]) else None,
            int(_dt7) if (_dt7 is not None and pd.notna(_dt7)) else None)
        out.update(
            source_date=str(r["date"]),
            stale_prev_day=(str(r["date"]) != day),
            limit_up_count=int(r["zt"]),
            limit_down_count=int(r["dt"]) if pd.notna(r["dt"]) else None,
            touch_count=int(r["touch"]) if pd.notna(r["touch"]) else None,
            zhaban_rate=float(r["zhaban_rate"]),
            max_consec_limit_up=int(r["max_h"]),
            lianban_rate=float(r["lianban_rate"]) if pd.notna(r["lianban_rate"]) else None,
            median_chg_pct=float(r["median_pct"]) if pd.notna(r["median_pct"]) else None,
            up_count=int(r["up_count"]) if pd.notna(r["up_count"]) else None,
            down_count=int(r["down_count"]) if pd.notna(r["down_count"]) else None,
            deep_drop_count=int(_dt7) if (_dt7 is not None and pd.notna(_dt7)) else None,
            is_bingdian=bool(r["is_bingdian"]) if pd.notna(r.get("is_bingdian")) else None,
            sentiment_temp=th.get("temp"), temp_stage=th.get("stage"), temp_dims=th.get("dims"),
        )
    except Exception as e:  # noqa: BLE001
        out["error"] = f"env_facts_failed:{type(e).__name__}"
    return out


# ══════════════════════════════════════════════════════════════════════
#  ③ 板块事实（②定主线 / ③选真龙的 eye 输入）
# ══════════════════════════════════════════════════════════════════════
def sector_facts(day: str, top: int = 8) -> dict:
    """当日**板块事实**（真相源：`outputs/sector_strength_<day>.json`，R2.2 产物）。

    ⚠️ **只能“当日跑”**：该产物由 `build_sector_strength.py` 盘后生成，**不可回算历史日期**；
       缺文件时返回 `error=no_sector_strength`（不猜、不空转）。
    ⚠️ M1/M2/M3 的阈值是**占位值**（`calibrated=false`）⇒ 这里只给**事实**（pass/数值/score），
       不下“是不是强主线”的结论（那是 brain 的事）。
    """
    out: dict = {"day": day}
    try:
        import json as _json
        fp = BASE / "outputs" / f"sector_strength_{day}.json"
        if not fp.exists():
            return {**out, "error": "no_sector_strength"}
        d = _json.loads(fp.read_text(encoding="utf-8"))
        secs = d.get("sectors") or []
        out["calibrated"] = bool((d.get("_meta") or {}).get("calibrated"))
        out["market"] = d.get("market")
        out["n_sectors"] = len(secs)
        ranked = sorted(secs, key=lambda s: (-(s.get("score") or 0), -(s.get("ret_20d") or 0)))
        out["top_sectors"] = [{
            "l2": s.get("l2"), "name": s.get("name"), "n": s.get("n"),
            "score": s.get("score"),
            "m1": (s.get("m1") or {}).get("pass_"),
            "m2": (s.get("m2") or {}).get("pass_"),
            "m3": (s.get("m3") or {}).get("pass_"),
            "zt": (s.get("d3") or {}).get("zt"),
            "max_h": (s.get("d3") or {}).get("max_h"),
            "ret_20d": s.get("ret_20d"), "ret_5d": s.get("ret_5d"),
        } for s in ranked[:top]]
        # 三判据全过的板块数 —— 独立于主观“强主线”判断的可复核事实
        out["n_all_criteria_pass"] = sum(
            1 for s in secs
            if (s.get("m1") or {}).get("pass_") and (s.get("m2") or {}).get("pass_")
            and (s.get("m3") or {}).get("pass_"))
    except Exception as e:  # noqa: BLE001
        out["error"] = f"sector_facts_failed:{type(e).__name__}"
    return out


# ══════════════════════════════════════════════════════════════════════
#  ④ 分时事实（④找低吸 / ⑤稳持仓的 eye 输入）
# ══════════════════════════════════════════════════════════════════════
def intraday_facts(code: str, day: str, df=None) -> dict:
    """**分时事实** —— 直接复用口径已冻结的 `decision_chain.figures`（不另起一套）。

    :param df: 盘中可直接传**实时**分钟 df（`scan_and_confirm.pull_minutes()` 的产物，列契约一致）；
               缺省则从 R2.4 落盘读（`data/minute/1m/<code>.parquet`）。

    产出：VWAP 相关事实（含「第一波回落跌破均价线」的**权威事实** `first_break*`）·
          D6 五项特征 · 日内高/低与关键回落幅度。
    ⚠️⚠️ **落盘数据只覆盖「候选池」**（`data/minute/1m` 由 R2.4 按当日候选池并集落盘）：
       若该标的当日不在池内，parquet 会**只有前几十根**（实测 `002215` 9/11 仅 23 根 = 到 09:53）。
       ⇒ **盘中必须传实时 `df=`**；用落盘做回看时**必须先校验 `bars` 是否接近全日**，否则会在残缺序列上算事实。
    ⚠️ 全部为事实；「走不走 / 减半还是全走」属 brain，不在这里。
    """
    out: dict = {"code": code, "day": day}
    try:
        from decision_chain import figures as F
        d = df if df is not None else F.load_day_minute(code, day)
        if d is None or len(d) < 5:
            return {**out, "error": "no_minute"}
        f = F.vwap_facts(d)
        feats = F.d6_features(d, None)
        close = d["close"].astype(float)
        high = d["high"].astype(float)
        low = d["low"].astype(float)
        last = float(close.iloc[-1])
        hi, lo = float(high.max()), float(low.min())
        peak = float(high.iloc[-1]) if len(high) else None
        run_max = float(high.max())
        out.update(
            bars=len(d), ts_last=str(d["ts"].iloc[-1]),
            vwap=round(f["vwap_last"], 4) if f.get("vwap_last") is not None else None,
            close_last=round(last, 4),
            px_vs_vwap_pct=feats.get("px_vs_vwap_pct"),
            vwap_slope_pct=feats.get("vwap_slope_pct"),
            # ⭐「第一波回落跌破均价线」= 权威事实，只取 first_break_below_vwap 的结果
            first_break=bool((f.get("break_result") or {}).get("veto")),
            first_break_ts=(f.get("break_result") or {}).get("break_ts"),
            first_break_below_minutes=(f.get("break_result") or {}).get("below_minutes"),
            intraday_high=round(hi, 4), intraday_low=round(lo, 4),
            intraday_high_pct=round((hi / float(d["open"].iloc[0]) - 1) * 100, 3) if len(d) else None,
            fall_from_peak_pct=round((run_max - last) / run_max * 100, 3) if run_max else None,
            amplitude_pct=feats.get("amplitude_pct"),
            up_down_vol_ratio=feats.get("up_down_vol_ratio"),
            tail_above_vwap=feats.get("tail_above_vwap"),
        )
    except Exception as e:  # noqa: BLE001
        out["error"] = f"intraday_facts_failed:{type(e).__name__}"
    return out


# ══════════════════════════════════════════════════════════════════════
#  ⑤ 持仓账务事实（⑤稳持仓 / ⑥仓位的 eye 输入）
# ══════════════════════════════════════════════════════════════════════
def holding_facts(code: str, day: str, state: dict | None = None) -> dict:
    """**持仓账务事实**（真相源：`portfolio/ledger.json`，可用 `EVOALPHA_LEDGER` 覆盖）。

    ⚠️⚠️ **`holding_days` 必须从 `entry_ts` 自算** —— 生产里 `pos['days']` **恒为 0**
       （`ledger.buy()` 只在买入时置 0，没有任何脚本 +1）⇒ 任何依赖它的时间门槛都是死代码。
       这里按**自然日差**给出事实（交易日口径需日线序列，属 brain/上层判断）。
    """
    out: dict = {"code": code, "day": day}
    try:
        import ledger as L
        st = state if state is not None else L.load()
        acct = st.get("account", {}) or {}
        pos = (acct.get("positions") or {}).get(code) or {}
        out["has_position"] = bool(pos)
        equity = float(L.cost_equity(st)) if hasattr(L, "cost_equity") else None
        out["equity"] = round(equity, 2) if equity else None
        out["cash"] = round(float(acct.get("cash", 0)), 2)
        gross = sum(float(p.get("cost", 0)) * int(p.get("qty", 0))
                    for p in (acct.get("positions") or {}).values())
        out["gross_exposure_pct"] = round(gross / equity * 100, 3) if equity else None
        out["cash_ratio_pct"] = round(float(acct.get("cash", 0)) / equity * 100, 3) if equity else None
        out["n_positions"] = len(acct.get("positions") or {})
        out["policy"] = {k: (st.get("policy") or {}).get(k)
                         for k in ("max_positions", "max_single_weight", "max_gross_exposure",
                                   "max_new_buys_per_day")}
        if pos:
            entry_ts = str(pos.get("entry_ts") or "")
            out.update(
                qty=int(pos.get("qty", 0)), cost=round(float(pos.get("cost", 0)), 4),
                entry_ts=entry_ts or None,
                holding_days=((_dt.date.fromisoformat(day)
                               - _dt.date.fromisoformat(entry_ts[:10])).days
                              if len(entry_ts) >= 10 else None),
                weight_pct=(round(float(pos.get("cost", 0)) * int(pos.get("qty", 0))
                                  / equity * 100, 3) if equity else None),
            )
    except Exception as e:  # noqa: BLE001
        out["error"] = f"holding_facts_failed:{type(e).__name__}"
    return out


def fact_pack(code: str, day: str, *, df=None, state: dict | None = None,
              entry_px: float | None = None) -> dict:
    """**事实包** —— 一次取齐该标的决策所需的全部 eye 事实（喂给 LLM 的输入）。

    ⚠️ 设计原则：`brain` 规则的判断质量取决于这里的输入是否齐全准确；
       任何一项失败**不阻断**其余项（各自带 `error` 字段，不抛异常）。
    """
    return {
        "code": code, "day": day,
        "env": env_facts(day),
        "sector": sector_facts(day),
        "position": position_facts(code, day, entry_px=entry_px),
        "intraday": intraday_facts(code, day, df=df),
        "holding": holding_facts(code, day, state=state),
    }
