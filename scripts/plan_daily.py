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

# ==== 选股窗口口径（单点常量，2026-09-15 立）==================================
# 背景（用户 2026-09-15 13:49 报「备选股池以及买入标的无一命中」的根因之一）：
#   Layer C（commit ae73dff）已裁定 **战法池 lookback = 6**（8 天选手候选池矩阵召回 31/32 → 32/32），
#   并在注释里明确写「池的召回窗与 picks 窗**不是同一口径**，勿再"对齐"回去」。
#   commit f496be6（9/15 09:03）把 lookback **静默退回 4**，理由注释为「lookback=4 与下面 win_days 同口径」——
#   而 win_days 当时是硬编码 4 ⇒ **"对齐"在代码上从未成立**，却让计划层比池层少看 2 个交易日。
#
# 实测代价（2026-09-15 池产物，metadata 记 lookback=6 实建）：
#   池内 huigui 1133 只，按 sig_date 分布 9/14:92 9/11:96 9/10:184 9/9:165 9/8:200 9/7:394；
#   4 日窗 [9/9..9/14] 只看得见 **537 只**，6 日窗 [9/7..9/14] **1131 只（+110.6%）**；
#   **596 只池内候选被窗口结构性排除**，其中含选手当日**唯一被买入**的
#   沃特股份(sz002886, sig=2026-09-07, bars_since_sig=5) —— 即"无一命中"的直接来源。
#
# 纪律（防再次静默回退）：**池窗（召回闸门）与 picks 窗（备选域）保持解耦，但 picks 窗不得窄于池窗。**
#   池是 scan 侧 fail-closed 的硬依赖（缺产物 ⇒ rc=8 ⇒ 全天禁新仓）；备选域比闸门更窄是无理由的信息损失。
PATTERN_LOOKBACK = 6        # 战法池召回窗（交易日）
PICKS_SIGNAL_WINDOW = 6     # 日计划 picks 信号窗（交易日）；必须 >= PATTERN_LOOKBACK

# picks 排序键（行为参数，抽成常量便于 A/B 与裁定）
# 原实现只按 ["heat","brk5","yang"] 排序 ⇒ 同 L2 内大量并列 + 不稳定排序 = 随机裁掉。
# heat 仍是第一主键（保留现有"板块热度优先"口径，避免本轮夹带未裁定的行为改动）；
# 新增 amt（成交额，量能强度）与 bars_since_sig（新鲜度）作显式 tiebreak，末位 symbol 保证完全确定。
PICKS_SORT_KEYS = ["heat", "brk5", "yang", "amt", "bars_since_sig", "symbol"]
PICKS_SORT_ASC = [False, False, False, False, True, True]
# ============================================================================



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
      （该脚本已于 2026-09-15 归档到 `scripts/_legacy/build_pattern_pool.py`，勿再单独跑）
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
    # ⭐ 2026-09-15：lookback 恢复为 6（撤销 f496be6 的静默回退，回到 ae73dff 的裁定）。
    #   注意这与下方 win_days（日计划 picks 的信号窗）**不是同一口径** ——
    #   池的召回窗与 picks 窗解耦，勿再"对齐"回去；但 picks 窗不得窄于池窗（见 PICKS_SIGNAL_WINDOW）。
    if PICKS_SIGNAL_WINDOW < PATTERN_LOOKBACK:
        print(f'[WARN] picks 信号窗({PICKS_SIGNAL_WINDOW}) < 池召回窗({PATTERN_LOOKBACK}) ⇒ '
              f'池内近 {PATTERN_LOOKBACK - PICKS_SIGNAL_WINDOW} 个交易日的信号在计划层不可见',
              file=sys.stderr, flush=True)
    try:
        _pat_fp, _pat_stats = build_pattern_artifact(dmap, d, pday, lookback=PATTERN_LOOKBACK)
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
        _pct = c / pc - 1
        pcts.append(_pct)
        if _pct < -0.07:            # R2.5: 严格 < -0.07（与 GEN-GATE-17 表达式一致）
            dt7_cnt += 1
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
    t = emotion_thermometer(zt, dt_cnt, zr, max_h, lianban_rate=lianban_rate, median_pct=med,
                            deep_drop_count=dt7_cnt)
    bing = (dt_cnt > zt and zt < 40)
    print(f'{pday} 情绪: zt={zt} dt={dt_cnt} 跌幅>7%={dt7_cnt} 炸板率={zr:.1f}% 高度={max_h} 连板率={lianban_rate:.1f}% 温度={t["temp"]} {t["stage"]} 冰点={bing}', flush=True)
    # ---- 信号扫描（信号日 ∈ 最近 PICKS_SIGNAL_WINDOW 个工作日，T 可买）
    import datetime as _dt2
    _w = []
    _dd2 = _dt.date.fromisoformat(pday)
    while len(_w) < PICKS_SIGNAL_WINDOW:
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
            cands.append({"symbol": sym, "sig_date": sd, "l2": l2, "yang": yang, "brk5": brk5,
                          "heat": sector_zt.get(l2, 0), "amt": amt,
                          "bars_since_sig": len(dlist) - 1 - i})
    print(f'候选池 {len(cands)} 只', flush=True)
    # ---- 软评分排序 top4（操盘纪律：科创板688/689、北交所4/8/92 不可买——权限限制；ST 不打——选手模式无 ST 证据）
    cdf = pd.DataFrame(cands).drop_duplicates(subset=["symbol"])
    cdf = cdf[~cdf["symbol"].str.startswith(("688", "689", "4", "8", "92"))]
    _names = {}
    try:
        _doc = json.loads((BASE / 'data' / 'stock_names_stocks.json').read_text(encoding='utf-8'))
        _names = _doc.get('names', _doc)      # 兼容两段式（_meta + names）与旧扁平结构
    except Exception:
        pass
    # ST 过滤（R2.8 2026-09-12）：
    #   ① 切到**只含个股**的新表 —— 旧表 35086 条里 83% 是债/基金/指数，且 `000004` 被写成「工业指数」
    #      不含 'ST' → **ST 漏筛**（真正的 000004 = *ST国华 本该被排除）。
    #   ② 名称缺失时**保守排除**：无法确认非 ST 就不买（与「选手模式无 ST 证据」纪律一致）。
    _named = cdf[cdf["symbol"].map(lambda s: bool(_names.get(s, '')))]
    if len(_named) != len(cdf):
        print(f'[names] 因缺名保守排除 {len(cdf) - len(_named)} 只', flush=True)
    cdf = _named[~_named["symbol"].map(
        lambda s: 'ST' in _names.get(s, '') or str(_names.get(s, '')).startswith('*'))]
    # ⭐ 排序键（2026-09-15 修，原为 cdf.sort_values(["heat","brk5","yang"], ascending=False)）
    #   缺陷：三个键值域极窄（heat 为小整数、brk5/yang 为布尔）⇒ 同 L2 内大量**完全并列**；
    #   pandas 默认 quicksort **不稳定**，`groupby("l2").head(2)` 在并列上等价于**任取 2 只**。
    #   实测 2026-09-15：崇达技术/奥士康（l2=270200, heat=6, brk5=T, yang=T）与选手候选
    #   协和电子/艾华集团（**同为 l2=270200, heat=6, brk5=T, yang=T**）四者完全并列
    #   ⇒ 协和/艾华被随机裁掉；而 `amt`（信号日成交额）已算出却**未参与排序**。
    #   修法：① 显式 tiebreak，纳入已算出的 amt（量能强度）与信号新鲜度；
    #         ② kind="mergesort" 保证稳定 ⇒ 结果可复现（原实现同一输入可产出不同 top4）。
    #   为何是这两个 tiebreak：都对齐选手口径 —— 量柱六形态把"有量"当最优档
    #   （9/10「分时上有量，一步一步向上走」= ①最优档），且他实际执行多落在信号后 0-3 日。
    #   ⚠️ 顺序本身是**行为参数**，故抽成常量：便于 A/B 与裁定，勿在调用点硬编码。
    cdf = cdf.sort_values(PICKS_SORT_KEYS, ascending=PICKS_SORT_ASC, kind="mergesort")
    # 跨方向分散：每主线(L2)最多 2 只，依次取到 4 只——避免备选押单一方向
    top = cdf.groupby("l2", group_keys=False).head(2).head(4)
    picks = []
    for _, r in top.iterrows():
        sym = r["symbol"]
        picks.append({"sym": sym, "name": _names.get(sym, ''), "sig_date": r["sig_date"], "l2": r["l2"], "heat": int(r["heat"]), "brk5": bool(r["brk5"]), "yang": bool(r["yang"])})
    # 候选域可观测性（2026-09-15 新增）：把"窗口/过滤损失"落到产物里，
    # 否则「池 1246 只 → picks 4 只」之间的损耗完全不可见（本次根因正是这样藏了两天）。
    _stats = {
        "n_cands_raw": int(len(cands)),
        "n_cands_eligible": int(len(cdf)),
        "picks_window_days": PICKS_SIGNAL_WINDOW,
        "pattern_lookback": PATTERN_LOOKBACK,
        "win_days": list(win_days),
    }
    print(f'候选域: raw={_stats["n_cands_raw"]} eligible={_stats["n_cands_eligible"]} '
          f'→ picks={len(picks)} (picks窗={PICKS_SIGNAL_WINDOW}日, 池窗={PATTERN_LOOKBACK}日)', flush=True)
    plan = {
        "date": d,
        "mode": f"候选观察(输入≤{pday}收盘)",
        "emotion": {"zt": zt, "dt": dt_cnt, "dt7": dt7_cnt, "zhaban_rate": round(zr, 1), "max_h": max_h, "lianban_rate": round(lianban_rate, 1), "temp": t["temp"], "stage": t["stage"], "note": t.get("note", "")},
        "mainline": sorted(sector_zt.items(), key=lambda x: -x[1])[:5],
        "global": _global_snapshot(),
        "picks": picks,
        "stats": _stats,
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