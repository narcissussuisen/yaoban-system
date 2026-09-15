"""修正版：分时闸门的**决策点**检验（替换早先的全市场反事实检验）。

## 早先那版检验错在哪（逐条）

早先的 `run_intraday_counterfactual.py` 得出「否决组收益反而更高（1.093% vs 0.471%，p≈1e-4）」。
复核后确认该结论**建立在一个口径错误的检验上**，不能作为否决该纪律的依据：

1. **测了错的种群。** 选手的「跌破均价线就弃」是**候选池发布后 25 分钟内的个股闸门**，
   只作用于通过战法形态筛选的少数候选。我把它套到全市场所有形态事件（5,308 个）上，
   等于测了一个他从未执行过的策略。
2. **把三段式规则压成二元。** 他 9/10 的分级是 ①有量+稳步向上 / ②无量欠缺力度 / ③破均价线。
   沃特股份「无量」被买入、山东玻纤「有量」被买入，两只**都没有**破均价线——
   我的 veto/pass 二元切分把 ② 和 ① 混为一谈，也把「因量能而弃」和「因破线而弃」混为一谈。
3. **窗口起点错位。** 检查窗口应对齐**候选池发布时刻**（资料实测 09:35~09:52），
   我用的 09:35–10:00 对 9/9、9/10 恰好正确，但对 9/2（09:42 发布）、9/7（09:41）等
   只是碰巧覆盖，对 9/11（09:43）则完全没覆盖到 10:44 的持仓决策。
4. **用均值作判据。** 均值把极端右侧样本（日内跌破均价线后大幅反包的高波动股）放大；
   对短线选手真正相关的是**胜率与下行捕获**。实测否决组：均值更高、**中位数更低**
   （0.124% vs −0.053%）、**止损率更高**（14.2% vs 11.9%）——这三者并存说明
   否决组是「高波动、右偏」的一群，而不是「更差」的一群。

## 本模块的口径（修正后）

* 只在**选手明示适用**的战法族上检验（默认 上升回档 huigui）；
* 只对齐**候选池发布时刻**的窗口（`window_start` 为发布时刻或其前 5 分钟）；
* 把闸门拆成三个**独立**维度分别检验，再看组合：
    - `vwap`  窗口内是否跌破均价线（他的 ③）
    - `vol`   窗口内量能强度（他的 ①②，用窗口均量/前 5 日均量近似）
    - `gain`  发布时点涨幅（他的「不追涨」，发布时已 +6~8% 的票他不可操作）
* 结果指标同时给**均值 / 中位数 / 胜率 / 止损率 / 下行均值**，不以单一均值定论。

判据（先登记）：组合闸门应当在**中位数、胜率、止损率**上一致优于无闸门，
若只在均值上更差、其余更好，则应表述为「降低波动与回撤路径不适，非提升期望」。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from iteration import intraday, market, rules   # noqa: E402

SINCE = "2026-08-20"
HORIZON = 5
STOP_PCT = 5.0

# 候选池发布时刻（资料实测）：日期 → 发布分钟，用于对齐检查窗口
POOL_PUBLISH_HM = {
    "2026-08-31": "09:21",
    "2026-09-02": "09:42",
    "2026-09-03": "09:40",
    "2026-09-04": "09:47",
    "2026-09-07": "09:41",
    "2026-09-08": "09:43",
    "2026-09-09": "09:46",
    "2026-09-10": "09:35",
    "2026-09-11": "09:43",
}
DEFAULT_PUBLISH = "09:45"     # 无记录日期的保守默认

# 选手公开候选池（资料逐条可追溯）。pub = 该条的发布时刻（未记录者用保守默认）。
#   action: BUY=实际买入 / SKIP=未买（原因见 note）
CANDIDATE_POOLS: list[dict] = [
    dict(date="2026-09-02", code="600172", name="黄河旋风", action="BUY", note="9/2 0942 候选→10:47 已建"),
    dict(date="2026-09-02", code="600110", name="诺德股份", action="SKIP", note="未执行"),
    dict(date="2026-09-02", code="000977", name="浪潮信息", action="SKIP", note="未执行；次日 +3.03%"),
    dict(date="2026-09-02", code="002651", name="利君股份", action="BUY", note="9/2 0942 候选→10:47 已建"),
    dict(date="2026-09-03", code="603912", name="佳力图", action="SKIP", note="未执行，未说明"),
    dict(date="2026-09-03", code="605118", name="力鼎光电", action="BUY", note="9/3 买→9/4 平 +1.02%"),
    dict(date="2026-09-03", code="003018", name="金富科技", action="BUY", note="当日涨停"),
    dict(date="2026-09-03", code="300192", name="科德教育", action="SKIP", note="未执行，未说明"),
    dict(date="2026-09-04", code="000892", name="欢瑞世纪", action="BUY", note="执行 3/5"),
    dict(date="2026-09-04", code="000628", name="高新发展", action="BUY", note="执行 3/5"),
    dict(date="2026-09-04", code="002015", name="协鑫能科", action="BUY", note="执行 3/5"),
    dict(date="2026-09-04", code="000626", name="远大控股", action="SKIP", note="未执行；资料推断分时走弱"),
    dict(date="2026-09-04", code="002279", name="久其软件", action="SKIP", note="未执行；资料推断分时走弱"),
    dict(date="2026-09-07", code="002708", name="光洋股份", action="BUY", note="趋势反包"),
    dict(date="2026-09-07", code="601208", name="东材科技", action="BUY", note="上升回档"),
    dict(date="2026-09-07", code="000998", name="隆平高科", action="SKIP", note="未执行，未说明（后 +3.09%）"),
    dict(date="2026-09-07", code="000912", name="泸天化", action="SKIP", note="拉升太快没上车（后两连板）"),
    dict(date="2026-09-09", code="603162", name="海通发展", action="BUY", note="唯一未警示→买入，涨停"),
    dict(date="2026-09-09", code="603270", name="金帝股份", action="SKIP", note="明示：破均价线不玩"),
    dict(date="2026-09-09", code="002194", name="武汉凡谷", action="SKIP", note="明示：破均价线不玩"),
    dict(date="2026-09-09", code="003018", name="金富科技", action="SKIP", note="明示：破均价线不玩"),
    dict(date="2026-09-10", code="605006", name="山东玻纤", action="BUY", note="①最优：有量+一步步向上"),
    dict(date="2026-09-10", code="002886", name="沃特股份", action="BUY", note="②中等：无量欠缺力度"),
    dict(date="2026-09-10", code="603113", name="金能科技", action="SKIP", note="明示：破均价线（当日 −5.71%）"),
    dict(date="2026-09-10", code="603093", name="南华期货", action="SKIP", note="明示：破均价线"),
    dict(date="2026-09-11", code="000070", name="特发信息", action="SKIP", note="未买（大盘闸门/低开破 MA10）"),
    dict(date="2026-09-11", code="603328", name="依顿电子", action="BUY", note="买 @12.304（当日唯一买入）"),
    dict(date="2026-09-11", code="002297", name="博云新材", action="SKIP", note="未买"),
    dict(date="2026-09-11", code="002632", name="道明光学", action="BUY", note="池外买入（未公开候选）"),
]

# 明示理由为「破均价线」的样本（可严格计分）
EXPLICIT_VWAP_SKIP = {"603270", "002194", "003018@0909", "603113", "603093"}


def audit_candidates(*, lookback_min: int = 5, forward_min: int = 25,
                     ret_horizon: int = 5) -> dict:
    """逐个候选做「发布窗口内三个闸门维度 + 后续 5 日结果」的对照表。

    这是**选手实际判据域**上的检验：种群是公开候选池（十几只），
    不是全市场形态事件（几千个）。每个样本给出足够细节供人工裁定。
    """
    rows = []
    for c in CANDIDATE_POOLS:
        code, date = c["code"], c["date"]
        pub = POOL_PUBLISH_HM.get(date, DEFAULT_PUBLISH)
        g = gate_dimensions(code, date, publish_hm=pub,
                            lookback_min=lookback_min, forward_min=forward_min)
        dly = market.load_daily(code)
        fwd = None
        fwd1 = None
        stopped = None
        same_day = None
        if dly is not None and len(dly):
            dates = [str(x) for x in dly["date"]]
            if date in dates:
                i = dates.index(date)
                b = rules.bars(dly)
                if i + 1 < len(dly):
                    fm = market.forward_metrics(b["open"], b["high"], b["close"], i,
                                                ret_horizon, STOP_PCT)
                    if fm is not None:
                        fwd = round(fm["ret_pct"], 2)
                        stopped = bool(fm["stopped"])
                    # 他的实际持有节奏：次日开盘买 → 当日收盘卖（≈1 日）
                    fm1 = market.forward_metrics(b["open"], b["high"], b["close"], i, 1, STOP_PCT)
                    if fm1 is not None:
                        fwd1 = round(fm1["ret_pct"], 2)
                if i > 0:
                    pc = float(dly["close"].iloc[i - 1])
                    same_day = round((float(dly["close"].iloc[i]) / pc - 1.0) * 100.0, 2)
        rows.append({**c, "pub_hm": pub,
                     "window": g.get("window"), "veto": g.get("veto_vwap"),
                     "below_frac": g.get("below_frac"), "min_dev_pct": g.get("min_dev_pct"),
                     "vol_ratio": g.get("vol_ratio"), "vol_strong": g.get("vol_strong"),
                     "gain_at_publish_pct": g.get("gain_at_publish_pct"),
                     "chase": g.get("chase"),
                     "same_day_ret_pct": same_day, "next1_ret_pct": fwd1,
                     "next5_ret_pct": fwd, "stopped": stopped,
                     "err": g.get("error")})

    ok = [r for r in rows if not r["err"]]
    broke = [r for r in ok if r["veto"]]
    kept = [r for r in ok if not r["veto"]]

    def _sum(sel, key):
        return summarize([{"ret_pct": r[key], "hwm_pct": 0.0,
                           "stopped": bool(r.get("stopped"))}
                          for r in sel if r.get(key) is not None])

    # 选手同一判据域的锚点：真买 vs 弃 的 next1（≈1 日持有）对照
    bought = [r for r in ok if r["action"] == "BUY"]
    skipped = [r for r in ok if r["action"] == "SKIP"]
    explicit_skip = [r for r in skipped if r["code"] in ("603270", "002194", "603113", "603093")]
    return {
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "lookback_min": lookback_min, "forward_min": forward_min, "ret_horizon": ret_horizon,
        "n_candidates": len(rows), "n_scored": len(ok),
        "broke_vwap": {"n": len(broke), "next5": _sum(broke, "next5_ret_pct"),
                       "next1": _sum(broke, "next1_ret_pct")},
        "kept": {"n": len(kept), "next5": _sum(kept, "next5_ret_pct"),
                 "next1": _sum(kept, "next1_ret_pct")},
        "by_action": {
            "bought": {"n": len(bought), "next5": _sum(bought, "next5_ret_pct"),
                       "next1": _sum(bought, "next1_ret_pct")},
            "skipped": {"n": len(skipped), "next5": _sum(skipped, "next5_ret_pct"),
                        "next1": _sum(skipped, "next1_ret_pct")},
            "explicit_vwap_skip": {"n": len(explicit_skip),
                                   "next5": _sum(explicit_skip, "next5_ret_pct"),
                                   "next1": _sum(explicit_skip, "next1_ret_pct")},
        },
        "rows": rows,
    }


def _minus_minutes(hm: str, k: int) -> str:
    h, m = (int(x) for x in hm.split(":"))
    t = h * 60 + m - k
    return f"{t // 60:02d}:{t % 60:02d}"


def _plus_minutes(hm: str, k: int) -> str:
    h, m = (int(x) for x in hm.split(":"))
    t = h * 60 + m + k
    return f"{t // 60:02d}:{t % 60:02d}"


def gate_dimensions(code: str, date: str, *, publish_hm: str,
                    lookback_min: int = 5, forward_min: int = 25,
                    vol_ratio_strong: float = 1.2) -> dict:
    """在「发布时刻 ± 窗口」上求三个闸门维度。"""
    df = intraday.load_snapshot(code)
    if df is None:
        return {"error": "no_snapshot"}
    day = df[df["date"] == date]
    if day.empty:
        return {"error": "no_data_for_date"}

    ws, we = _minus_minutes(publish_hm, lookback_min), _plus_minutes(publish_hm, forward_min)
    r = intraday.first_break_below_vwap(day, window_start=ws, window_end=we,
                                        confirm_minutes=3, tol=0.003)
    st = intraday.vwap_stats(day, window_start=ws, window_end=we, tol=0.003)

    # 量能强度：窗口内「等效全天量」/ 发布前 5 个交易日日均量
    #   等效全天量 = 窗口成交量 × (240 / 窗口分钟数)，与日线 volume 同量纲后可直比
    vol_ratio = None
    dly = market.load_daily(code)
    win = day[(day["hm"] >= ws) & (day["hm"] <= we)]
    if dly is not None and len(dly) and len(win):
        dates = [str(x) for x in dly["date"]]
        if date in dates:
            i = dates.index(date)
            base = dly["volume"].iloc[max(0, i - 5):i]
            if len(base) and float(base.mean()) > 0:
                equiv_day_vol = float(win["volume"].sum()) * (240.0 / max(len(win), 1))
                vol_ratio = equiv_day_vol / float(base.mean())

    # 发布时点涨幅（相对前收）
    gain_at_publish = None
    if len(win):
        px = float(win["close"].iloc[len(win) // 2])
        pc = None
        if dly is not None and len(dly):
            dates = [str(x) for x in dly["date"]]
            if date in dates:
                i = dates.index(date)
                if i > 0:
                    pc = float(dly["close"].iloc[i - 1])
        if pc:
            gain_at_publish = (px / pc - 1.0) * 100.0

    return {
        "code": code, "date": date, "window": [ws, we],
        "veto_vwap": bool(r.get("veto")),
        "below_frac": st.get("below_frac"), "min_dev_pct": st.get("min_dev_pct"),
        "vol_ratio": None if vol_ratio is None else round(vol_ratio, 3),
        "vol_strong": None if vol_ratio is None else bool(vol_ratio >= vol_ratio_strong),
        "gain_at_publish_pct": None if gain_at_publish is None else round(gain_at_publish, 2),
        "chase": None if gain_at_publish is None else bool(gain_at_publish > 3.0),
    }


def collect(rule_name: str, config: dict, codes: list[str], since: str) -> list[dict]:
    params = rules.rule_params(rule_name, config)
    fn = rules.RULES[rule_name]["fn"]
    out = []
    for code in codes:
        df = market.load_daily(code)
        if df is None or len(df) < 80:
            continue
        try:
            sig = fn(df, code, **params)
        except Exception:  # noqa: BLE001
            continue
        b = rules.bars(df)
        for i in sig.to_numpy().nonzero()[0]:
            i = int(i)
            d = str(df["date"].iloc[i])
            if d < since:
                continue
            fm = market.forward_metrics(b["open"], b["high"], b["close"], i, HORIZON, STOP_PCT)
            if fm is None:
                continue
            out.append({"rule": rule_name, "code": code, "date": d,
                        "ret_pct": fm["ret_pct"], "hwm_pct": fm["hwm_pct"],
                        "stopped": fm["stopped"]})
    return out


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    r = np.array([x["ret_pct"] for x in rows], float)
    down = r[r < 0]
    return {
        "n": int(len(r)),
        "mean_ret_pct": round(float(r.mean()), 3),
        "median_ret_pct": round(float(np.median(r)), 3),
        "win_rate_pct": round(float((r > 0).mean() * 100.0), 1),
        "stop_rate_pct": round(float(np.mean([x["stopped"] for x in rows]) * 100.0), 1),
        "downside_mean_pct": round(float(down.mean()), 3) if len(down) else None,
        "p25_ret_pct": round(float(np.percentile(r, 25)), 3),
        "mean_hwm_pct": round(float(np.mean([x["hwm_pct"] for x in rows])), 3),
    }


def run(*, rule_name: str = "huigui", market_codes: int = 1200, since: str = SINCE,
        lookback_min: int = 5, forward_min: int = 25) -> dict:
    import tomllib
    config = tomllib.loads((ROOT / "config" / "parameters.toml").read_text(encoding="utf-8"))
    events = collect(rule_name, config, market.available_codes(limit=market_codes), since)

    scored = []
    for e in events:
        pub = POOL_PUBLISH_HM.get(e["date"], DEFAULT_PUBLISH)
        g = gate_dimensions(e["code"], e["date"], publish_hm=pub,
                            lookback_min=lookback_min, forward_min=forward_min)
        if "error" in g:
            continue
        scored.append({**e, **g})

    def sel(pred):
        return [x for x in scored if pred(x)]

    clean = sel(lambda x: not x["veto_vwap"])
    vetoed = sel(lambda x: x["veto_vwap"])
    nosnap = len(events) - len(scored)

    res = {
        "generated_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "rule": rule_name, "since": since, "horizon": HORIZON, "stop_pct": STOP_PCT,
        "lookback_min": lookback_min, "forward_min": forward_min,
        "signals_total": len(events), "signals_scored": len(scored),
        "excluded_no_snapshot": nosnap,
        "groups": {
            "all": summarize(scored),
            "no_vwap_break": summarize(clean),
            "vwap_break": summarize(vetoed),
            "clean_and_vol_strong": summarize(sel(lambda x: not x["veto_vwap"] and x.get("vol_strong"))),
            "clean_and_no_chase": summarize(sel(lambda x: not x["veto_vwap"] and not x.get("chase"))),
            "clean_vol_no_chase": summarize(
                sel(lambda x: not x["veto_vwap"] and x.get("vol_strong") and not x.get("chase"))),
            "vwap_break_or_chase": summarize(sel(lambda x: x["veto_vwap"] or x.get("chase"))),
        },
        "note": ("本检验对齐**候选池发布时刻**窗口、只在单一战法族上做，并把闸门拆为 "
                 "vwap / 量能 / 涨幅 三维；判据以中位数、胜率、止损率、下行均值为主，"
                 "不以均值为唯一依据。"),
    }
    res["verdict"] = _verdict(res["groups"])
    return res


def _verdict(g: dict) -> str:
    a, c = g.get("all", {}), g.get("no_vwap_break", {})
    if not c.get("n") or not a.get("n"):
        return "样本不足"
    better = []
    worse = []
    for k, label, good in (("median_ret_pct", "中位数", 1), ("win_rate_pct", "胜率", 1),
                           ("stop_rate_pct", "止损率", -1), ("downside_mean_pct", "下行均值", 1)):
        va, vc = a.get(k), c.get(k)
        if va is None or vc is None:
            continue
        delta = (vc - va) * good
        (better if delta > 0 else worse).append(f"{label}{'↑' if delta > 0 else '↓'}")
    return (f"不加闸门 → 加闸门（未破均价线）："
            f"中位数 {a.get('median_ret_pct')}%→{c.get('median_ret_pct')}%，"
            f"胜率 {a.get('win_rate_pct')}%→{c.get('win_rate_pct')}%，"
            f"止损率 {a.get('stop_rate_pct')}%→{c.get('stop_rate_pct')}%，"
            f"下行均值 {a.get('downside_mean_pct')}%→{c.get('downside_mean_pct')}%；"
            f"改善维度 {better or '—'}，退化维度 {worse or '—'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="分时闸门·决策点检验（修正版）")
    ap.add_argument("--rule", default="huigui")
    ap.add_argument("--market-codes", type=int, default=1200)
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--lookback-min", type=int, default=5)
    ap.add_argument("--forward-min", type=int, default=25)
    ap.add_argument("--out", default=None)
    ap.add_argument("--audit-candidates", action="store_true",
                    help="只做公开候选池的逐票对照（选手实际判据域）")
    a = ap.parse_args(argv)

    if a.audit_candidates:
        res = audit_candidates(lookback_min=a.lookback_min, forward_min=a.forward_min)
        outp = pathlib.Path(a.out) if a.out else (
            ROOT / "outputs" / "iteration" / "intraday_candidate_audit.json")
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        hdr = (f"{'日期':<11}{'标的':<10}{'选手':<6}{'破线':<5}{'低线%':>7}{'量比':>7}"
               f"{'发布涨幅':>9}{'当日%':>8}{'后5日%':>8}  备注")
        print(hdr)
        print("-" * len(hdr))
        for r in res["rows"]:
            act = {"BUY": "买", "SKIP": "弃"}.get(r["action"], r["action"])
            bf = r.get("below_frac")
            print(f"{r['date']:<11}{r['name']:<10}{act:<6}"
                  f"{('是' if r.get('veto') else '否'):<5}"
                  f"{(f'{bf*100:.0f}' if bf is not None else '—'):>7}"
                  f"{(f'{r['vol_ratio']:.2f}' if r.get('vol_ratio') is not None else '—'):>7}"
                  f"{(f'{r['gain_at_publish_pct']:.2f}' if r.get('gain_at_publish_pct') is not None else '—'):>9}"
                  f"{(f'{r['same_day_ret_pct']:.2f}' if r.get('same_day_ret_pct') is not None else '—'):>8}"
                  f"{(f'{r['next5_ret_pct']:.2f}' if r.get('next5_ret_pct') is not None else '—'):>8}"
                  f"  {r['note']}")
        print(f"\n破线组: {json.dumps(res['broke_vwap'], ensure_ascii=False)}")
        print(f"未破组: {json.dumps(res['kept'], ensure_ascii=False)}")
        print(f"\n按选手动作:")
        for k, v in res["by_action"].items():
            print(f"  {k:22s} {json.dumps(v, ensure_ascii=False)}")
        print("->", outp)
        return 0

    res = run(rule_name=a.rule, market_codes=a.market_codes, since=a.since,
              lookback_min=a.lookback_min, forward_min=a.forward_min)
    outp = pathlib.Path(a.out) if a.out else (
        ROOT / "outputs" / "iteration" / "intraday_gate_decision_point.json")
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in res["groups"].items():
        print(f"{k:24s} {json.dumps(v, ensure_ascii=False)}")
    print("\nverdict:", res["verdict"])
    print("->", outp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
