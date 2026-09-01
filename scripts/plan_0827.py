"""8/27 盘前计划（重建口径：输入仅用 ≤8/26 收盘数据，无前视）

流程: 全市场日线(F盘8/21前 + TDX回填8/21-8/26) → huigui_v5 信号(信号日∈[8/21,8/26]) →
  8/26 情绪(zt/dt/炸板/温度+冰点档) → 8/26 板块热度 → 软评分 → top4 备选 → 计划写入 ledger

用法: python scripts/plan_0827.py
"""
from __future__ import annotations

import json
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
    from data.qfq_store import build_daily_map
    qfq26 = QFQStore('2026')
    syms = [s for s in qfq26.symbols() if not s.startswith(('399', '899'))]
    print('并行预载日线(2025+2026)...', flush=True)
    d25 = build_daily_map('2025', syms, workers=6)
    d26 = build_daily_map('2026', syms, workers=6)
    dmap = {}
    for sym in syms:
        rows = (d25.get(sym) or []) + (d26.get(sym) or [])
        fp = TDX / f'{sym}.parquet'
        if fp.exists():
            try:
                extra = pd.read_parquet(fp)
                extra_rows = list(extra[["symbol","date","open","high","low","close","volume","amount"]].itertuples(index=False, name=None))
                rows = rows + extra_rows
            except Exception:
                pass
        if rows:
            df = pd.DataFrame(rows, columns=["symbol","date","open","high","low","close","volume","amount"])
            df["date"] = df["date"].astype(str)
            df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
            dmap[sym] = df
    qfq26.close()
    return dmap


def main():
    dmap = load_all_daily()
    print(f'日线载入 {len(dmap)} 只', flush=True)
    # ---- 8/26 情绪（全市场日线算 zt/dt/炸板/连板率/中位数）
    d26 = '2026-08-26'
    zt = dt_cnt = touch = zhaban = 0
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
    print(f'8/26 情绪: zt={zt} dt={dt_cnt} 炸板率={zr:.1f}% 高度={max_h} 连板率={lianban_rate:.1f}% 温度={t["temp"]} {t["stage"]} 冰点={bing}', flush=True)
    # ---- 信号扫描（信号日 ∈ [8/21, 8/26]，8/27 可买 = k≥1）
    win_days = ['2026-08-21', '2026-08-24', '2026-08-25', '2026-08-26']
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
            brk5 = i >= 5 and float(r["close"]) >= max(float(x) for x in df["close"].iloc[i-5:i])
            l2 = industry_of(sym, sd) or ""
            cands.append({"symbol": sym, "sig_date": sd, "l2": l2, "yang": yang, "brk5": brk5, "heat": sector_zt.get(l2, 0)})
    print(f'候选池 {len(cands)} 只', flush=True)
    # ---- 软评分排序 top4
    cdf = pd.DataFrame(cands).drop_duplicates(subset=["symbol"])
    cdf = cdf.sort_values(["heat", "brk5", "yang"], ascending=False)
    top = cdf.head(4)
    picks = []
    for _, r in top.iterrows():
        sym = r["symbol"]
        df = dmap[sym]
        dates = df["date"].tolist()
        i = dates.index(d26) if d26 in dates else len(dates) - 1
        pc = float(df.iloc[i]["close"])  # 8/26 收盘 = 8/27 昨收
        lim = round(pc * (1 + limit_pct_of(sym)), 2)
        picks.append({"sym": sym, "sig_date": r["sig_date"], "l2": r["l2"], "heat": r["heat"], "brk5": r["brk5"], "yang": r["yang"], "prev_close": round(pc, 3), "limit_px": round(lim, 3), "cond": "回踩均价线收复≤+3% 或 下杀≥2%回拉 或 低开≥3%高走"})
    plan = {
        "date": "2026-08-27",
        "mode": "重建口径(输入≤8/26收盘)",
        "emotion": {"zt": zt, "dt": dt_cnt, "zhaban_rate": round(zr, 1), "max_h": max_h, "lianban_rate": round(lianban_rate, 1), "temp": t["temp"], "stage": t["stage"]},
        "position_cap": "20%(冰点避险)" if bing else "90%",
        "mainline": sorted(sector_zt.items(), key=lambda x: -x[1])[:5],
        "picks": picks,
        "sell_list": ["破均价线减半/再破清", "炸板卖", "次高点卖", "乖离≥8%减半", "KDJ死叉清", "破MA5减半/破MA10 3日清", "5日时间止损", "-5%止损"]
    }
    import sys as _s; _s.path.insert(0, str(BASE / "portfolio"))
    from ledger import load, save, record_plan
    st = load()
    record_plan(st, "2026-08-27", plan)
    save(st)
    out = BASE / 'outputs' / 'plans'
    out.mkdir(parents=True, exist_ok=True)
    (out / "2026-08-27_plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(plan, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()