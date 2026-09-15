"""影子回归（Shadow Regression）：用确定性代码检验「卡片规则 + 阈值候选」。

两层口径，互不替代，分别报告（禁止合并冒充前向绩效）：
  A. 案例归因回归（case replay）——选手实际动作 vs 规则是否触发。
     召回率 = 规则在其被归属的战法族内命中实际入场/出场的比例。
     误杀率 = 规则在「选手看过但没买」样本上触发（纪律冲突）的比例。
  B. 市场影子盘（market shadow）——规则在全市场独立跑模拟盘，报告诚实容量指标。
     出场口径固定为硬基线（10 日线破位 / −5% 止损 / 5 日时间止损），
     避免出场变量污染入场对比；出场规则另行单独评估。

诚实边界：本模块不产生语义判断，不修改参数文件，不写账本。
"""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd

from . import market, rules
from .model import now_str

HORIZON = 5               # 前向评估窗口（交易日）
STOP_PCT = 5.0            # 事件级前向止损
BASE_EXIT_MA = 10         # 影子盘固定出场：破 10 日线
BASE_TIME_STOP = 5        # 影子盘固定出场：最长持有 5 日

# 案例归因：选手实际入场 → 其来源战法族（依据资料中选手自述/明确标注）
ENTRY_ATTRIBUTION = {
    "603615": ("zthuicai", "8/28 建仓，涨停回踩低吸战法（画像 §六 已解答）"),
    "605006": ("zthuicai", "9/10 买入①最优档，当日信号日 9/9 涨停回踩"),
    "002886": ("zthuicai", "9/10 买入②中等档（战法族未明确，按当日候选标注归属）"),
    "603162": ("fanbao", "9/9 选手明确标注「符合趋势反包战法」"),
    "002708": ("fanbao", "9/7 选手明确标注「趋势反包战法筛选」"),
    "603618": ("huigui", "9/8 选手明确标注「符合上升回档战法」"),
    "603118": ("huigui", "8/31 建仓（战法族未明确，画像记为新题材催化【低置信】）"),
    "000628": ("huigui", "9/3 建仓【低置信：资料未标注战法族】"),
    "002015": ("huigui", "9/4 回接【低置信：资料未标注战法族】"),
    "002181": ("none", "9/8 未公开买入：龙头示范下的补涨捕捉，非战法筛选"),
    "002215": ("none", "9/9 未公开买入：概念驱动，非战法筛选"),
}

# 案例归因：选手实际出场 → 其依据的出场规则族
EXIT_ATTRIBUTION = {
    "002909": ("exit_short", "9/9「短线拉升不板」兑现"),
    "000628": ("exit_ma10", "9/9 洗盘后 10 日线反弹了结（趋势票）"),
    "603615": ("exit_ma10", "9/9 反弹到支撑上方了结（趋势票/老仓）"),
    "002015": ("exit_ma10", "9/9 开盘跌破 10 日线破位止损"),
    "002181": ("exit_short", "9/9 板块龙头停牌情绪影响出局（1 日）"),
    "603162": ("exit_short", "9/10 短线拉升不板止盈（1 日）"),
    "002708": ("exit_short", "9/8 开盘后不久回落，短线低于预期"),
    "601208": ("exit_short", "9/8 同上"),
    "000892": ("exit_ma10", "9/7 分时上攻无买量（高位第 4 根）"),
    "600869": ("exit_ma10", "9/7 同上（主升中继，后续卖飞）"),
    "603118": ("exit_ma10", "9/2 共进式触发器：破倍量涨停柱 + 反弹乏力"),
}


def load_case_table(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _dates_map(df: pd.DataFrame) -> dict[str, int]:
    return {str(d): i for i, d in enumerate(df["date"])}


def _signal_dates(sig: pd.Series, df: pd.DataFrame) -> list[str]:
    return [str(df["date"].iloc[i]) for i in np.flatnonzero(sig.to_numpy())]


def case_replay(rule_name: str, params: dict, case: dict,
                window: int = 4) -> dict:
    """单参数组合的案例归因回归。"""
    spec = rules.RULES[rule_name]
    kind = spec["kind"]
    fn = spec["fn"]
    clean = {k: v for k, v in params.items() if not k.startswith("_")}

    if kind == "entry":
        rows = [e for e in case["entries"]
                if e.get("entry_px") and ENTRY_ATTRIBUTION.get(e["code"], ("none",))[0] == rule_name]
        hits, miss, detail = 0, [], []
        for e in rows:
            df = market.load_daily(e["code"])
            if df is None or not len(df):
                miss.append({"code": e["code"], "name": e["name"], "why": "无日线数据"})
                continue
            try:
                sig = fn(df, e["code"], **clean)
            except Exception as ex:  # noqa: BLE001
                miss.append({"code": e["code"], "name": e["name"], "why": f"规则异常 {ex}"})
                continue
            dmap = _dates_map(df)
            ei = dmap.get(str(e["entry_date"]))
            if ei is None:
                miss.append({"code": e["code"], "name": e["name"], "why": "建仓日不在日线范围"})
                continue
            lo = max(0, ei - window)
            got = [d for d in _signal_dates(sig, df)[: ] if lo <= dmap[d] <= ei]
            if got:
                hits += 1
                detail.append({"code": e["code"], "name": e["name"], "entry": e["entry_date"],
                               "signal_dates": got, "lag_days": ei - dmap[got[-1]],
                               "attribution": ENTRY_ATTRIBUTION.get(e["code"], ("", ""))[1]})
            else:
                miss.append({"code": e["code"], "name": e["name"],
                             "why": f"{window} 个交易日内未触发",
                             "attribution": ENTRY_ATTRIBUTION.get(e["code"], ("", ""))[1]})
        # 误杀：选手看过但明确没买的样本
        false_kill = []
        for c in case["candidates"]:
            if c.get("executed") or ENTRY_ATTRIBUTION.get(c["code"], ("none",))[0] not in (rule_name, "none"):
                continue
            df = market.load_daily(c["code"])
            if df is None or not len(df):
                continue
            try:
                sig = fn(df, c["code"], **clean)
            except Exception:  # noqa: BLE001
                continue
            dmap = _dates_map(df)
            pi = dmap.get(str(c["pool_date"]))
            if pi is None:
                continue
            if any(pi - window <= dmap[d] <= pi for d in _signal_dates(sig, df)):
                false_kill.append({"code": c["code"], "name": c["name"],
                                   "pool_date": c["pool_date"], "note": c["note"]})
        return {
            "rule": rule_name, "kind": kind, "params": clean,
            "cases": len(rows), "hits": hits,
            "recall_pct": round(hits / len(rows) * 100.0, 1) if rows else None,
            "misses": miss, "hit_detail": detail,
            "false_kill": false_kill, "false_kill_n": len(false_kill),
        }

    # ---------------- 出场规则 ----------------
    rows = [x for x in case["exits"]
            if EXIT_ATTRIBUTION.get(x["code"], ("none",))[0] == rule_name]
    hits, miss, detail = 0, [], []
    fwd_all, fwd_sig = [], []
    for x in rows:
        df = market.load_daily(x["code"])
        if df is None or not len(df):
            continue
        try:
            sig = fn(df, x["code"], **clean)
        except Exception as ex:  # noqa: BLE001
            miss.append({"code": x["code"], "why": f"规则异常 {ex}"})
            continue
        dmap = _dates_map(df)
        xi = dmap.get(str(x["exit_date"]))
        if xi is None:
            miss.append({"code": x["code"], "why": "清仓日不在日线范围"})
            continue
        fired = bool(sig.iloc[xi])
        if fired:
            hits += 1
        else:
            miss.append({"code": x["code"], "name": x["name"], "why": "清仓当日未触发",
                         "attribution": EXIT_ATTRIBUTION.get(x["code"], ("", ""))[1]})
        o = df["open"].to_numpy(float)
        h = df["high"].to_numpy(float)
        c = df["close"].to_numpy(float)
        fm = market.forward_metrics(o, h, c, xi, HORIZON, stop_pct=None)
        if fm:
            fwd_all.append(fm["ret_pct"])
            detail.append({"code": x["code"], "name": x["name"], "exit_date": x["exit_date"],
                           "fired": fired, "next5_ret_pct": round(fm["ret_pct"], 2),
                           "realized_pct": x.get("realized_pct")})
        # 信号日的前向收益（评估规则触发时点是否更早/更准）
        sd = [d for d in _signal_dates(sig, df)]
        if sd:
            si = min(dmap[d] for d in sd if dmap[d] >= xi - 2)
            fm2 = market.forward_metrics(o, h, c, si, HORIZON, stop_pct=None)
            if fm2:
                fwd_sig.append(fm2["ret_pct"])
    return {
        "rule": rule_name, "kind": kind, "params": clean,
        "cases": len(rows), "hits": hits,
        "recall_pct": round(hits / len(rows) * 100.0, 1) if rows else None,
        "misses": miss, "hit_detail": detail,
        "next5_after_exit_mean_pct": round(float(np.mean(fwd_all)), 2) if fwd_all else None,
        "next5_after_exit_n": len(fwd_all),
        "next5_after_signal_mean_pct": round(float(np.mean(fwd_sig)), 2) if fwd_sig else None,
        "next5_after_signal_n": len(fwd_sig),
    }


# --------------------------------------------------------------------------
# B. 市场影子盘
# --------------------------------------------------------------------------

def _prep(df: pd.DataFrame) -> dict:
    return {
        "date": df["date"].to_numpy(),
        "open": df["open"].to_numpy(float),
        "high": df["high"].to_numpy(float),
        "low": df["low"].to_numpy(float),
        "close": df["close"].to_numpy(float),
        "volume": df["volume"].to_numpy(float),
    }


def market_shadow_signals(rule_name: str, params: dict, codes: list[str],
                          start: str | None = None, end: str | None = None,
                          cap: int = 4000, seed: int = 20260911) -> dict:
    """在全市场跑规则，收集事件级前向指标（入场规则）。"""
    spec = rules.RULES[rule_name]
    if spec["kind"] != "entry":
        return {"n": 0, "error": "非入场规则"}
    fn = spec["fn"]
    clean = {k: v for k, v in params.items() if not k.startswith("_")}
    events, skipped = [], 0
    for code in codes:
        df = market.load_daily(code)
        if df is None or len(df) < 80:
            skipped += 1
            continue
        try:
            sig = fn(df, code, **clean)
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        idxs = np.flatnonzero(sig.to_numpy())
        if not len(idxs):
            continue
        d = _prep(df)
        for i in idxs:
            ds = str(d["date"][i])
            if start and ds < start:
                continue
            if end and ds > end:
                continue
            fm = market.forward_metrics(d["open"], d["high"], d["close"], int(i),
                                        HORIZON, STOP_PCT)
            if fm is None:
                continue
            fm["date"], fm["code"] = ds, code
            events.append(fm)
    if len(events) > cap:
        rnd = np.random.default_rng(seed)
        sel = rnd.choice(len(events), size=cap, replace=False)
        events = [events[i] for i in sorted(sel)]
    agg = market.agg_metrics(events)
    agg["codes_scanned"] = len(codes) - skipped
    agg["codes_skipped"] = skipped
    if events:
        ds = sorted(e["date"] for e in events)
        agg["date_range"] = [ds[0], ds[-1]]
        agg["events_per_day"] = round(len(events) / max(len({e["date"] for e in events}), 1), 2)
    return agg


def market_shadow_portfolio(rule_name: str, params: dict, codes: list[str],
                            *, max_positions: int = 4, start_cash: float = 1_000_000.0,
                            time_stop: int = BASE_TIME_STOP, exit_ma: int = BASE_EXIT_MA,
                            stop_pct: float = STOP_PCT,
                            start: str | None = None) -> dict:
    """影子盘：规则信号按日排序取前 N 建仓，固定出场口径，输出组合指标。

    成交假设（诚实标注）：次日开盘等权买入，收盘价触发离场，双边成本 0.1%。
    """
    spec = rules.RULES[rule_name]
    if spec["kind"] != "entry":
        return {"error": "非入场规则"}
    fn = spec["fn"]
    clean = {k: v for k, v in params.items() if not k.startswith("_")}

    data: dict[str, dict] = {}
    mabuf: dict[str, np.ndarray] = {}
    for code in codes:
        df = market.load_daily(code)
        if df is None or len(df) < 80:
            continue
        try:
            sig = fn(df, code, **clean)
        except Exception:  # noqa: BLE001
            continue
        d = _prep(df)
        d["sig"] = sig.to_numpy()
        d["ma"] = df["close"].rolling(exit_ma).mean().to_numpy(float)
        data[code] = d
    if not data:
        return {"error": "无可用标的"}

    all_dates = sorted({str(x) for d in data.values() for x in d["date"]})
    if start:
        all_dates = [x for x in all_dates if x >= start]
    pos: dict[str, dict] = {}
    cash = start_cash
    equity_curve, trades = [], []
    COST = 0.001

    for di, day in enumerate(all_dates):
        # ---- 先处理离场（收盘价触发）----
        for code in list(pos):
            p = pos[code]
            d = data[code]
            row = np.flatnonzero(d["date"] == day)
            if not len(row):
                continue
            i = int(row[0])
            c = float(d["close"][i])
            p["days"] += 1
            p["hwm"] = max(p["hwm"], float(d["high"][i]))
            reason = None
            if c <= p["entry"] * (1 - stop_pct / 100.0):
                reason = "stop"
            elif not np.isnan(d["ma"][i]) and c < float(d["ma"][i]):
                reason = "ma_break"
            elif p["days"] >= time_stop:
                reason = "time"
            if reason:
                proceeds = p["shares"] * c * (1 - COST)
                cash += proceeds
                ret = (c * (1 - COST)) / (p["entry"] * (1 + COST)) - 1.0
                trades.append({"code": code, "entry_date": p["entry_date"], "entry": p["entry"],
                               "exit_date": day, "exit": c, "days": p["days"],
                               "ret_pct": round(ret * 100, 3), "reason": reason})
                del pos[code]
        # ---- 再处理入场（次日开盘价成交，按信号日期排序）----
        if di + 1 < len(all_dates):
            nxt = all_dates[di + 1]
            cands = []
            for code, d in data.items():
                if code in pos:
                    continue
                row = np.flatnonzero(d["date"] == day)
                if not len(row):
                    continue
                i = int(row[0])
                if not d["sig"][i]:
                    continue
                if np.isnan(d["ma"][i]) or float(d["close"][i]) < float(d["ma"][i]):
                    continue           # 入场前置：须在出场线之上，避免自相矛盾
                cands.append((code, i))
            slots = max_positions - len(pos)
            if slots > 0 and cands:
                n = min(slots, len(cands))
                alloc = cash / n
                for code, i in cands[:n]:
                    d = data[code]
                    j = np.flatnonzero(d["date"] == nxt)
                    if not len(j):
                        continue
                    px = float(d["open"][int(j[0])])
                    if not np.isfinite(px) or px <= 0:
                        continue
                    shares = int(alloc / (px * (1 + COST)) // 100 * 100)
                    if shares <= 0:
                        continue
                    cost = shares * px * (1 + COST)
                    if cost > cash:
                        continue
                    cash -= cost
                    pos[code] = {"shares": shares, "entry": px, "entry_date": nxt,
                                 "days": 0, "hwm": px}
        # ---- 记权益 ----
        mv = 0.0
        for code, p in pos.items():
            d = data[code]
            row = np.flatnonzero(d["date"] == day)
            if len(row):
                mv += p["shares"] * float(d["close"][int(row[0])])
        equity_curve.append({"date": day, "equity": round(cash + mv, 2)})

    if len(equity_curve) < 2:
        return {"error": "区间过短"}
    eq = np.array([x["equity"] for x in equity_curve], dtype=float)
    rets = np.diff(eq) / eq[:-1]
    peak = np.maximum.accumulate(eq)
    dd = (eq / peak - 1.0) * 100.0
    tr = [t["ret_pct"] for t in trades]
    days = len(equity_curve)
    return {
        "rule": rule_name, "params": clean,
        "days": days,
        "start": equity_curve[0]["date"], "end": equity_curve[-1]["date"],
        "total_return_pct": round((eq[-1] / eq[0] - 1.0) * 100.0, 3),
        "annualized_pct": round(((eq[-1] / eq[0]) ** (244.0 / max(days, 1)) - 1.0) * 100.0, 2),
        "max_drawdown_pct": round(float(dd.min()), 2),
        "trades": len(trades),
        "win_rate_pct": round(float(np.mean([r > 0 for r in tr]) * 100.0), 1) if tr else None,
        "mean_trade_ret_pct": round(float(np.mean(tr)), 3) if tr else None,
        "avg_hold_days": round(float(np.mean([t["days"] for t in trades])), 2) if trades else None,
        "trades_detail_top": sorted(trades, key=lambda t: -t["ret_pct"])[:10],
        "trades_detail_worst": sorted(trades, key=lambda t: t["ret_pct"])[:10],
        "equity_curve": equity_curve[::max(1, days // 60)],
        "assumptions": {"fill": "next_open", "cost_one_way": COST, "max_positions": max_positions,
                        "exit": f"close<MA{exit_ma} | -{stop_pct}% | {time_stop}d"},
    }


def run(rule_names: list[str], config: dict, case: dict, codes: list[str],
        *, case_window: int = 4, market_cap: int = 4000,
        market_start: str | None = None, with_portfolio: bool = False,
        portfolio_start: str | None = None) -> dict:
    """跑完所有规则族 × 参数组合的影子回归。"""
    out: dict = {"generated_at": now_str(), "horizon": HORIZON, "stop_pct": STOP_PCT,
                 "search": "single_axis", "rules": {}}
    for name in rule_names:
        spec = rules.RULES[name]
        combos = rules.single_axis_variants(name, config)
        entries = []
        for params in combos:
            rep = case_replay(name, params, case, window=case_window)
            rep["_axis"] = params.get("_axis", {})
            entries.append(rep)
        block: dict = {"label": spec["label"], "kind": spec["kind"],
                       "card_ids": spec["card_ids"], "variants": entries}
        if spec["kind"] == "entry":
            block["market"] = []
            for params in combos:
                agg = market_shadow_signals(name, params, codes,
                                            start=market_start, cap=market_cap)
                agg["_axis"] = params.get("_axis", {})
                block["market"].append(agg)
            if with_portfolio:
                base = rules.rule_params(name, config) or {}
                if not base:
                    base = {k: v for k, v in combos[0].items() if not k.startswith("_")}
                block["portfolio"] = market_shadow_portfolio(
                    name, base, codes, start=portfolio_start)
        out["rules"][name] = block
    return out
