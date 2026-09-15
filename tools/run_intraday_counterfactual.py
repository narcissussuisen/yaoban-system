"""分时反事实检验：把「分时跌破均价线 → 不玩」从 18 条案例扩到全市场事件。

问题：8/9 条案例的完全吻合只是**小样本拟合**吗？
做法（样本外、无调参）：
  1. 取修正后的入场规则在 2026-08-20 之后触发的**全部信号事件**（不参与任何参数选择）；
  2. 对每个事件，用当日分钟线判定是否触发均价线否决；
  3. 比较「被否决」与「未被否决」两组的前向收益分布。

判据（先登记，后看数）：
  * 若否决组的前向收益显著劣于通过组 → 该纪律有**真实筛选价值**；
  * 若两组无差异 → 该纪律只是复现了选手的措辞，没有可交易的边际。

诚实边界：样本外仅指「未用于选参数」；窗口仅 15 个交易日（2026-08-20~09-10），
仍属同一市场状态，不能替代更长周期的前向影子记录。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent

# R2.4（2026-09-12）：分钟快照根目录由 src/data/minute_paths.py 统一解析
# （EVOALPHA_MINUTE_SNAPSHOT 可覆盖；便于快照整体迁到 F 盘而无需改代码）
try:
    from data.minute_paths import snapshot_root as _minute_root
    MINUTE_ROOT = _minute_root()
except Exception:  # noqa: BLE001
    MINUTE_ROOT = ROOT / "data" / "minute"

if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from iteration import intraday, market, rules, shadow   # noqa: E402

# 分时口径：由 18 条案例的网格搜索选出（准确率 94.4%），**在此处冻结、不再调参**
VWAP_PARAMS = {"window_start": "09:35", "window_end": "10:00",
               "confirm_minutes": 5, "tol": 0.003}
SINCE = "2026-08-20"
HORIZON = 5
STOP_PCT = 5.0
RULE_NAMES = ("huigui", "zthuicai", "fanbao")


def collect_signals(config: dict, codes: list[str], since: str) -> list[dict]:
    events = []
    for name in RULE_NAMES:
        params = rules.rule_params(name, config)
        fn = rules.RULES[name]["fn"]
        for code in codes:
            df = market.load_daily(code)
            if df is None or len(df) < 80:
                continue
            try:
                sig = fn(df, code, **params)
            except Exception:  # noqa: BLE001
                continue
            b = rules.bars(df)
            o, h, c = b["open"], b["high"], b["close"]
            for i in sig.to_numpy().nonzero()[0]:
                i = int(i)
                d = str(df["date"].iloc[i])
                if d < since:
                    continue
                fm = market.forward_metrics(o, h, c, i, HORIZON, STOP_PCT)
                if fm is None:
                    continue
                events.append({"rule": name, "code": code, "date": d,
                               "ret_pct": fm["ret_pct"], "hwm_pct": fm["hwm_pct"],
                               "stopped": fm["stopped"]})
    return events


def attach_vwap(events: list[dict], *, need_fetch: set[str] | None = None) -> dict:
    kept, no_snap, no_date = [], [], []
    for e in events:
        if need_fetch is not None and e["code"] in need_fetch:
            no_snap.append(e)
            continue
        r = intraday.eval_entry_vwap(e["code"], e["date"], **VWAP_PARAMS)
        if "error" in r:
            (no_snap if r["error"] == "no_snapshot" else no_date).append({**e, "why": r["error"]})
            continue
        kept.append({**e, "veto": bool(r["veto"]), "below_minutes": r.get("below_minutes"),
                     "window_minutes": r.get("window_minutes"), "break_ts": r.get("break_ts"),
                     "below_frac": r.get("stat_below_frac")})
    return {"evaluated": kept, "missing_snapshot": no_snap, "missing_date": no_date}


def summarize(rows: list[dict], label: str) -> dict:
    if not rows:
        return {"label": label, "n": 0}
    r = np.array([x["ret_pct"] for x in rows], dtype=float)
    h = np.array([x["hwm_pct"] for x in rows], dtype=float)
    stopped = np.array([bool(x["stopped"]) for x in rows])
    se = float(r.std(ddof=1) / np.sqrt(len(r))) if len(r) > 1 else float("nan")
    return {
        "label": label, "n": int(len(r)),
        "mean_ret_pct": round(float(r.mean()), 3),
        "median_ret_pct": round(float(np.median(r)), 3),
        "mean_ret_se": round(se, 3),
        "win_rate_pct": round(float((r > 0).mean() * 100.0), 1),
        "hwm_ge3_pct": round(float((h >= 3.0).mean() * 100.0), 1),
        "stop_rate_pct": round(float(stopped.mean() * 100.0), 1),
    }


def welch(a: list[float], b: list[float]) -> dict:
    """Welch t 检验（不假设等方差）。正态近似，避免引入 scipy 依赖。"""
    from math import erf, sqrt

    x, y = np.asarray(a, float), np.asarray(b, float)
    if len(x) < 2 or len(y) < 2:
        return {"t": None, "df": None, "p_two_sided_approx": None}
    vx, vy = x.var(ddof=1), y.var(ddof=1)
    se = np.sqrt(vx / len(x) + vy / len(y))
    if se == 0:
        return {"t": None, "df": None, "p_two_sided_approx": None}
    t = (x.mean() - y.mean()) / se
    df = (vx / len(x) + vy / len(y)) ** 2 / (
        (vx / len(x)) ** 2 / (len(x) - 1) + (vy / len(y)) ** 2 / (len(y) - 1))
    p = float(2 * (1 - 0.5 * (1 + erf(abs(float(t)) / sqrt(2)))))
    return {"t": round(float(t), 3), "df": round(float(df), 1),
            "p_two_sided_approx": round(p, 5)}


def run(*, market_codes: int = 1200, since: str = SINCE, use_plan: bool = True) -> dict:
    import tomllib
    config = tomllib.loads((ROOT / "config" / "parameters.toml").read_text(encoding="utf-8"))
    codes = market.available_codes(limit=market_codes)

    plan_file = MINUTE_ROOT / "_fetch_plan.json"
    if use_plan and plan_file.exists():
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
        # 计划里「需要拉取」的标的若仍无快照，则从本次检验中排除并显式计数
        need = set(plan.get("need", []))
        have = {p.stem for p in (MINUTE_ROOT / "1m").glob("*.parquet")}
        pending = need - have
    else:
        pending = set()

    events = collect_signals(config, codes, since)
    res = attach_vwap(events, need_fetch=pending)
    kept = res["evaluated"]
    veto_rows = [e for e in kept if e["veto"]]
    pass_rows = [e for e in kept if not e["veto"]]

    out = {
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "vwap_params": VWAP_PARAMS, "since": since,
        "horizon": HORIZON, "stop_pct": STOP_PCT,
        "rules": list(RULE_NAMES),
        "signals_total": len(events),
        "signals_evaluated": len(kept),
        "excluded_pending_snapshot": len(pending),
        "excluded_no_date": len(res["missing_date"]),
        "veto": summarize(veto_rows, "被否决（不玩）"),
        "pass": summarize(pass_rows, "通过（可玩）"),
        "veto_rate_pct": round(len(veto_rows) / len(kept) * 100.0, 1) if kept else None,
        "welch": welch([e["ret_pct"] for e in veto_rows], [e["ret_pct"] for e in pass_rows]),
        "by_rule": {},
    }
    for name in RULE_NAMES:
        v = [e for e in veto_rows if e["rule"] == name]
        p = [e for e in pass_rows if e["rule"] == name]
        out["by_rule"][name] = {"veto": summarize(v, f"{name} 否决"),
                                "pass": summarize(p, f"{name} 通过"),
                                "welch": welch([e["ret_pct"] for e in v],
                                               [e["ret_pct"] for e in p])}
    # 敏感度：below_frac 分档（否决"程度"是否单调影响收益）
    deciles = []
    for lo, hi in ((0.0, 0.0001), (0.0, 0.2), (0.2, 0.5), (0.5, 1.01)):
        sel = [e for e in kept if e.get("below_frac") is not None and lo <= e["below_frac"] < hi]
        if sel:
            deciles.append({**summarize(sel, f"below_frac∈[{lo},{hi})"), "range": [lo, hi]})
    out["below_frac_buckets"] = deciles
    out["verdict"] = _verdict(out)
    return out


def _verdict(out: dict) -> str:
    v, p = out["veto"], out["pass"]
    if not v.get("n") or not p.get("n"):
        return "样本不足，无法判定"
    gap = p["mean_ret_pct"] - v["mean_ret_pct"]
    pv = out["welch"].get("p_two_sided_approx")
    sig = (pv is not None and pv < 0.05)
    if gap > 0.5 and sig:
        return (f"✅ 有真实筛选价值：否决组均值 {v['mean_ret_pct']}% 显著低于通过组 "
                f"{p['mean_ret_pct']}%（差 {gap:.3f}pp, p≈{pv}）")
    if gap > 0.5:
        return (f"⚠️ 方向正确但未达显著：差 {gap:.3f}pp（p≈{pv}）——"
                f"需更长窗口累积样本")
    return (f"❌ 无筛选价值：否决组 {v['mean_ret_pct']}% vs 通过组 {p['mean_ret_pct']}%"
            f"（差 {gap:.3f}pp, p≈{pv}）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="分时均价线否决的全市场反事实检验")
    ap.add_argument("--market-codes", type=int, default=1200)
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--no-plan", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    res = run(market_codes=a.market_codes, since=a.since, use_plan=not a.no_plan)
    outp = pathlib.Path(a.out) if a.out else (ROOT / "outputs" / "iteration" / "intraday_counterfactual.json")
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"signals {res['signals_total']} evaluated {res['signals_evaluated']} "
          f"(pending-snapshot excluded {res['excluded_pending_snapshot']})")
    print("veto:", json.dumps(res["veto"], ensure_ascii=False))
    print("pass:", json.dumps(res["pass"], ensure_ascii=False))
    print("welch:", json.dumps(res["welch"], ensure_ascii=False))
    print("verdict:", res["verdict"])
    print("->", outp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
