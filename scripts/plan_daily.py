"""每日盘前计划（重建口径：输入仅用 ≤T-1 收盘数据，无前视）

流程: 全市场日线(daily_src: 60m重建权威+F盘历史) → huigui_v5 信号(信号日∈最近4交易日) →
  T-1 情绪(zt/dt/炸板/温度+冰点档) → T-1 板块热度 → 软评分 → top4 备选(每主线≤2只跨方向分散) → 计划写入 ledger

用法: python scripts/plan_daily.py --date 2026-08-31
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from data.qfq_store import QFQStore  # noqa: E402
from core import strategies as S  # noqa: E402
from core.combo_sell import industry_of  # noqa: E402
from core.sentiment import emotion_thermometer  # noqa: E402
from core.sell import limit_pct_of  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
TDX = pathlib.Path(r'F:/WorkBuddyItem/a股level2/daily')


def load_all_daily():
    """日线来源: F盘历史(2025+2026至8/21) + 60m重建(8/22+, 已验证可靠) —— 统一经 daily_src"""
    from data.qfq_store import build_daily_map
    from core.daily_src import load_daily as _ld
    qfq26 = QFQStore('2026')
    syms = [s for s in qfq26.symbols() if not s.startswith(('399', '899'))]
    print('并行预载 F盘历史日线...', flush=True)
    d25 = build_daily_map('2025', syms, workers=6)
    d26 = build_daily_map('2026', syms, workers=6)
    dmap = {}
    for sym in syms:
        df = _ld(sym)
        if df is None or not len(df):
            continue
        for col in ('volume', 'amount'):
            if col not in df.columns:
                df[col] = 0.0
        rows = list(df[["symbol","date","open","high","low","close","volume","amount"]].itertuples(index=False, name=None))
        rows_hist = (d25.get(sym) or []) + (d26.get(sym) or [])
        merged = {}
        for r in rows_hist:
            merged[str(r[1])] = r
        for r in rows:
            merged[str(r[1])] = r
        if merged:
            dfm = pd.DataFrame(list(merged.values()), columns=["symbol","date","open","high","low","close","volume","amount"])
            dfm["date"] = dfm["date"].astype(str)
            dfm = dfm.sort_values("date").reset_index(drop=True)
            dmap[sym] = dfm
    qfq26.close()
    return dmap


def latest_source_day(source_days, target: str) -> str:
    eligible = [str(day) for day in source_days if str(day) < target]
    if not eligible:
        raise RuntimeError(f"{target} 之前无可用真实日线")
    return max(eligible)


def build_pattern_artifact(dmap, day: str, asof: str, lookback: int = 4,
                           out_dir=None, patterns=None, exclude_fanbao_only: bool = True):
    """用**同一次** `load_all_daily()` 的 dmap 生成战法池产物（与日计划共用一次全市场遍历）。

    为什么并入本脚本（2026-09-14 用户裁定）：
      原先战法池由 `scripts/build_pattern_pool.py` 单独跑 —— 它自己再 `load_all_daily()`
      一次（实测日线载入 ~900s + 形态计算 ~1200s ≈ 35 分钟），而 `plan_daily.py` 盘后
      已经为日计划付过同一笔全市场遍历。两者**读的完全是同一份数据**（`core.daily_src`
      + `build_daily_map`），分开跑纯属把 IO 付两遍。
      ⇒ 收敛成：一次载入 → 两份产物（`outputs/plans/<day>_plan.json` +
         `outputs/patterns/<day>_pattern_pool.json`），schema 由
         `core.pattern_pool.write_pattern_pool` 单点保证（scan 侧读取契约不变）。

    口径（**防前视硬约束**）：`asof` 必须**严格早于** `day` —— 本函数自己再拦一次，
    不依赖调用方（`build_pattern_pool.py` 也有同样的拒绝逻辑，两处互为兜底）。

    返回 (path, stats)。
    """
    from core.pattern_pool import (build_pattern_pool, write_pattern_pool,
                                   load_stock_names, DEFAULT_PATTERNS)
    if not asof or str(asof) >= str(day):
        raise RuntimeError(f'战法池口径违规: asof({asof}) 必须严格早于 day({day})（防前视）')
    pats = tuple(patterns or DEFAULT_PATTERNS)
    names = load_stock_names()
    pool, stats = build_pattern_pool(dmap, asof=str(asof), lookback=lookback, patterns=pats,
                                     names=names,
                                     exclude_fanbao_only=exclude_fanbao_only)
    name_map = {r['sym']: names.get(r['sym'], '') for r in pool}
    fp = write_pattern_pool(day, str(asof), lookback, pats, pool, stats, name_map,
                            out_dir=out_dir)
    print(f'战法池 {len(pool)} 只 (asof={asof} lookback={lookback} day={day}) → {fp}', flush=True)
    print(f'  分战法: {stats["by_pattern"]} | ST剔除 {stats["n_excluded_st"]} '
          f'仅反包剔除 {stats["n_excluded_fanbao_only"]} 无名称 {stats["n_no_name"]}', flush=True)
    return fp, stats


def _global_snapshot():
    fp = pathlib.Path(__file__).resolve().parent.parent / 'outputs' / 'global_snapshot.json'
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def main():
    import argparse as _ap
    import datetime as _dt
    _parser = _ap.ArgumentParser()
    _parser.add_argument('--date', default='2026-08-28')
    _args = _parser.parse_args()
    d = _args.date
    _dd0 = _dt.date.fromisoformat(d)
    if _dd0.weekday() >= 5:
        print(f'{d} 非交易日(周末)，跳过'); return
    _dd = _dd0 - _dt.timedelta(days=1)
    while _dd.weekday() >= 5:
        _dd -= _dt.timedelta(days=1)
    pday = _dd.isoformat()
    dmap = load_all_daily()
    print(f'日线载入 {len(dmap)} 只', flush=True)
    source_days = [str(df["date"].max()) for df in dmap.values() if len(df)]
    pday = latest_source_day(source_days, d)
    # ---- 战法池（形态筛选层）：与日计划共用**同一次** load_all_daily 遍历 ----
    # 放在日计划计算之前：战法池是 scan 侧 fail-closed（缺产物 ⇒ rc=8 ⇒ **全天禁新仓**）
    # 的硬依赖，先落盘可让「后续日计划步骤失败」不影响次日战法池的可用性。
    # asof=pday ⇒ 严格早于 d 的真实交易日（T-1）。
    # ⭐ 2026-09-15：lookback 4 → 6（用户裁定「今天实盘前上线」）—— 8 天选手候选池矩阵
    #   实测：南华期货(9/10) 的 zt_huicai 信号在 asof 前 5-6 个交易日，窗口=4 抓不到；
    #   扩到 6 后 8 天召回 30/32→31/32（叠 zt_watch 后 32/32）。⚠️ 注意这与下方 win_days
    #   （日计划 picks 的信号窗）**不是同一口径**——池的召回窗与 picks 窗解耦，勿再"对齐"回去。
    try:
        _pat_fp, _pat_stats = build_pattern_artifact(dmap, d, pday, lookback=6)
    except Exception as _pat_exc:
        # ⚠️ 战法池失败**不得**打死日计划（日计划还挂在取数前序多步上，且盘前 gate 依赖它）；
        #    但绝不允许静默 —— 打 stderr 让 post-close 的 log_review 能捞到。
        print(f'[WARN] 战法池生成失败（日计划继续）: {type(_pat_exc).__name__}: {_pat_exc}',
              file=sys.stderr, flush=True)
    # ---- 前一交易日情绪（全市场日线算 zt/dt/炸板/连板率/中位数）
    d26 = pday
    zt = dt_cnt = touch = zhaban = 0
    dt7_cnt = 0          # R2.5: 跌幅>7% 家数（GEN-GATE-17 恐慌度量的跌侧）
    pcts = []; max_h = 0; prev_zt_set = set(); cur_zt_set = set()
    sector_zt = {}
    lianban_prev = 0
    for sym, df in dmap.items():
        dates = df["date"].tolist()
        if d26 not in dates:
            continue
        i = dates.index(d26)
        if i == 0:
            continue
        pc = float(df.iloc[i-1]["close"]); c = float(df.iloc[i]["close"]); hi = float(df.iloc[i]["high"])
        pl = limit_pct_of(sym)
        lim = round(pc * (1 + pl), 2); dlim = round(pc * (1 - pl), 2)
        is_zt = c >= lim - 0.005
        if is_zt:
            zt += 1; cur_zt_set.add(sym)
        if c <= dlim + 0.005:
            dt_cnt += 1
        if hi >= lim - 0.005:
            touch += 1
            if not is_zt:
                zhaban += 1
        pcts.append(c / pc - 1)
        lb = 0; j = i
        while j >= 1 and float(df.iloc[j]["close"]) >= round(float(df.iloc[j-1]["close"]) * (1 + pl), 2) - 0.005:
            lb += 1; j -= 1
        max_h = max(max_h, lb)
        l2 = industry_of(sym, d26) or "NA"
        sector_zt[l2] = sector_zt.get(l2, 0) + (1 if is_zt else 0)
        # 昨日涨停集合（连板率）
        if i >= 1:
            pc2 = float(df.iloc[i-2]["close"]) if i >= 2 else None
            c2 = float(df.iloc[i-1]["close"])
            if pc2 and c2 >= round(pc2 * (1 + pl), 2) - 0.005:
                prev_zt_set.add(sym)
    lianban_rate = len(prev_zt_set & cur_zt_set) / len(prev_zt_set) * 100 if prev_zt_set else 0.0
    med = float(np.median(pcts)) * 100 if pcts else None
    zr = zhaban / touch * 100 if touch else 0.0
    t = emotion_thermometer(zt, dt_cnt, zr, max_h, lianban_rate=lianban_rate, median_pct=med)
    bing = (dt_cnt > zt and zt < 40)
    print(f'{pday} 情绪: zt={zt} dt={dt_cnt} 炸板率={zr:.1f}% 高度={max_h} 连板率={lianban_rate:.1f}% 温度={t["temp"]} {t["stage"]} 冰点={bing}', flush=True)
    # ---- 信号扫描（信号日 ∈ [8/21, 8/26]，8/27 可买 = k≥1）
    import datetime as _dt2
    _w = []
    _dd2 = _dt.date.fromisoformat(pday)
    while len(_w) < 4:
        if _dd2.weekday() < 5:
            _w.append(_dd2.isoformat())
        _dd2 -= _dt.timedelta(days=1)
    win_days = _w
    cands = []
    for sym, df in dmap.items():
        if len(df) < 70:
            continue
        sig = S.detect_huigui_v5(df)
        dlist = df["date"].tolist()
        for sd in win_days:
            if sd not in dlist:
                continue
            i = dlist.index(sd)
            if not bool(sig.iloc[i]):
                continue
            r = df.iloc[i]
            yang = float(r["close"]) > float(r["open"])
            amt = float(r.get("amount", 0) or 0)  # 元（信号日成交额）
            brk5 = i >= 5 and float(r["close"]) >= max(float(x) for x in df["close"].iloc[i-5:i])
            l2 = industry_of(sym, sd) or ""
            if amt < 1e8:
                continue  # 流动性门槛: 信号日成交额 <1亿 不入选（妖票活跃底线）
            cands.append({"symbol": sym, "sig_date": sd, "l2": l2, "yang": yang, "brk5": brk5, "heat": sector_zt.get(l2, 0), "amt": amt})
    print(f'候选池 {len(cands)} 只', flush=True)
    # ---- 软评分排序 top4（操盘纪律：科创板688/689、北交所4/8/92 不可买——权限限制；ST 不打——选手模式无 ST 证据）
    cdf = pd.DataFrame(cands).drop_duplicates(subset=["symbol"])
    cdf = cdf[~cdf["symbol"].str.startswith(("688", "689", "4", "8", "92"))]
    _names = {}
    try:
        _names = json.loads((BASE / 'data' / 'stock_names_full.json').read_text(encoding='utf-8'))
        cdf = cdf[~cdf["symbol"].map(lambda s: 'ST' in _names.get(s, '') or str(_names.get(s, '')).startswith('*'))]
    except Exception:
        pass
    cdf = cdf.sort_values(["heat", "brk5", "yang"], ascending=False)
    # 跨方向分散：每主线(L2)最多 2 只，依次取到 4 只——避免备选押单一方向
    top = cdf.groupby("l2", group_keys=False).head(2).head(4)
    picks = []
    for _, r in top.iterrows():
        sym = r["symbol"]
        picks.append({"sym": sym, "name": _names.get(sym, ''), "sig_date": r["sig_date"], "l2": r["l2"], "heat": int(r["heat"]), "brk5": bool(r["brk5"]), "yang": bool(r["yang"])})
    plan = {
        "date": d,
        "mode": f"候选观察(输入≤{pday}收盘)",
        "emotion": {"zt": zt, "dt": dt_cnt, "zhaban_rate": round(zr, 1), "max_h": max_h, "lianban_rate": round(lianban_rate, 1), "temp": t["temp"], "stage": t["stage"]},
        "mainline": sorted(sector_zt.items(), key=lambda x: -x[1])[:5],
        "global": _global_snapshot(),
        "picks": picks
    }
    import sys as _s; _s.path.insert(0, str(BASE / "portfolio"))
    from ledger import transact, record_plan
    def _mutate(st):
        record_plan(st, d, plan)
        return d
    transact(_mutate)
    out = BASE / 'outputs' / 'plans'
    out.mkdir(parents=True, exist_ok=True)
    plan_path = out / (d + "_plan.json")
    tmp_path = plan_path.with_suffix(plan_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, plan_path)
    print(json.dumps(plan, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()