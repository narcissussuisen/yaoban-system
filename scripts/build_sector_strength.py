# -*- coding: utf-8 -*-
"""R2.2 第四块 · 板块强度计算（把「强主线判定三标准」跑成数值）。

## 输入 / 输出
- 输入：`data/sector_themes.json`（板块→成分股 + M1/M2/M3 判据定义）、`F:/.../daily_rebuilt/*.parquet`（800 根日线）
- 输出：`outputs/sector_strength_<date>.json`

## 三条判据（对应 `data/sector_themes.json::strong_mainline_criteria`）
- **M1 连续放量突破关键均线**（v65@03:34）：板块**等权指数** `close>MA20` 且 `vol>vol_ma5*1.2`，**连续 ≥2 日**
- **M2 趋势股率先创阶段新高并带动跟风**：板块内「趋势股」（`close>MA20` 且 MA20 向上）中
  出现 ≥1 只在近 60 日创**阶段新高**
- **M3 与指数共振**：近 20 日 —— ① **领涨**：市场上涨日里板块超额>0 的比例 ≥0.6；
  ② **抗跌**：市场下跌日里板块跑赢（跌得更少）的比例 ≥0.6

## 板块强度三层（裁量点 D3 / `GEN-MAIN-03`）
- **高标**：板块内当日**最高连板**数
- **20cm**：板块内**创业板 30x / 科创板 68x** 的涨停数（赚钱效应来源）
- **容量票**：板块内**成交额 Top5** 成员的平均涨跌幅（大资金承接）

## ⚠️ 阈值一律标 calibrated=false
M1/M2/M3 的参数（MA20/vol_ma5/1.2/连续2日/60日/0.6）是**占位值** —— 人格 SOP 只给了**定性表述**，
没给数值。本脚本把 `calibrated` 原样带进产物，**不假装已标定**。标定归 R1.3 容差带。

## 性能
一次遍历 5912 个 parquet（只取每只最后 ~65 行做窗口），再按 162 个板块聚合 —— 
实测约 5~7 分钟（与 `build_benchmark_arms.py` 同量级：它读 5646 只耗时 354s）。

用法: python scripts/build_sector_strength.py [--date 2026-09-11] [--workers 6] [--top 20]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

DAILY = pathlib.Path(r"F:\WorkBuddyItem\a股level2\daily_rebuilt")
THEMES = BASE / "data" / "sector_themes.json"
OUTDIR = BASE / "outputs"

WINDOW = 65          # 每只保留最后 65 根（覆盖 MA20 + 60 日新高）
LOOKBACK = 60
M3_WINDOW = 20


def _one(fp: str):
    """单只：返回 (sym, dates, closes, vols, amounts, 指标字典)。只保留最后 WINDOW 根。"""
    import pandas as pd
    import numpy as np
    try:
        df = pd.read_parquet(fp)
    except Exception:
        return None
    if df is None or len(df) < 25:
        return None
    df = df.tail(WINDOW)
    sym = str(df["symbol"].iloc[-1])
    d = df["date"].astype(str).tolist()
    c = df["close"].astype(float).to_numpy()
    h = df["high"].astype(float).to_numpy()
    v = df["volume"].astype(float).to_numpy()
    a = (df["amount"].astype(float).to_numpy() if "amount" in df.columns
         else np.zeros(len(c)))
    if len(c) < 25:
        return None
    ma20 = c[-20:].mean()
    ma20_prev = c[-21:-1].mean() if len(c) >= 21 else ma20
    trend = bool(c[-1] > ma20 and ma20 > ma20_prev)
    newhigh = bool(h[-1] >= (h[-LOOKBACK:].max() if len(h) >= 5 else h[-1]))
    return dict(sym=sym, dates=d, closes=c.tolist(), vols=v.tolist(), amounts=a.tolist(),
                trend=trend, newhigh=newhigh,
                ret_last=(c[-1] / c[-2] - 1) if len(c) >= 2 else 0.0,
                amt_last=float(a[-1]))


def _eq_index(series_list, dates):
    """等权指数：逐日对「相邻两日都有行情」的成员取平均收益，累乘；并产出等权成交量。"""
    import numpy as np
    n = len(dates)
    rets = [[] for _ in range(n)]
    vols = [[] for _ in range(n)]
    up = [0] * n
    down = [0] * n
    for s in series_list:
        idx = {d: i for i, d in enumerate(s["dates"])}
        c, v = s["closes"], s["vols"]
        for i in range(1, len(s["dates"])):
            j = n - len(s["dates"]) + i
            if j <= 0 or j >= n:
                continue
            if s["dates"][i] != dates[j]:
                continue
            r = c[i] / c[i - 1] - 1 if c[i - 1] else 0.0
            if abs(r) > 0.11:            # 剔除除权/异常（与 build_benchmark_arms 同口径）
                continue
            rets[j].append(r)
            vols[j].append(v[i])
            if r > 0:
                up[j] += 1
            elif r < 0:
                down[j] += 1
    nav, cur = [], 1.0
    for j in range(n):
        if rets[j]:
            cur *= (1 + sum(rets[j]) / len(rets[j]))
        nav.append(cur)
    vser = [(sum(vols[j]) / len(vols[j])) if vols[j] else 0.0 for j in range(n)]
    return nav, vser, up, down


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--top", type=int, default=20)
    args = ap.parse_args()
    t0 = time.time()

    themes = json.loads(THEMES.read_text(encoding="utf-8"))
    sectors = themes["sector_members"]
    crit = {c["id"]: c for c in themes["strong_mainline_criteria"]}
    want = sorted({s for v in sectors.values() for s in v["members"]})
    print(f"板块 {len(sectors)} 个 / 成员去重 {len(want)} 只 → 读 daily_rebuilt ...", flush=True)

    data = {}
    files = [(DAILY / f"{s}.parquet") for s in want]
    files = [f for f in files if f.exists()]
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_one, str(f)): f for f in files}
        for k, fu in enumerate(as_completed(futs), 1):
            r = fu.result()
            if r:
                data[r["sym"]] = r
            if k % 1000 == 0:
                print(f"  ...{k}/{len(files)}  取到 {len(data)}", flush=True)
    print(f"取到 {len(data)} 只 ({time.time()-t0:.0f}s)", flush=True)
    if not data:
        print("FAIL: 无数据（daily_rebuilt 不可达？）")
        return 1

    # 全局日期轴 = 最新一只的 dates（daily_rebuilt 同步更新，取众数更稳）
    dc = collections.Counter(tuple(d["dates"]) for d in data.values())
    dates = list(dc.most_common(1)[0][0])
    day = args.date or dates[-1]
    print(f"日期轴 {dates[0]} .. {dates[-1]}（{len(dates)} 根）；目标日 {day}", flush=True)

    # 全市场等权（作为 M3 的「指数」）
    mkt_nav, mkt_vol, mkt_up, mkt_down = _eq_index(list(data.values()), dates)
    mkt_ret = [0.0] + [mkt_nav[i] / mkt_nav[i - 1] - 1 for i in range(1, len(mkt_nav))]

    # 昨日涨停集合（连板判定）
    # ⚠️ 必须按**日期**定位，不能用尾部偏移：K 线根数不足 WINDOW 的标的（次新股/长期停牌）
    #    用偏移会整体错位、涨停永远判不出。故为每只建 date→index 映射。
    from core.sell import limit_pct_of
    _DMAP: dict = {}

    def _dmap(sym):
        m = _DMAP.get(sym)
        if m is None:
            s = data[sym]
            m = {d: i for i, d in enumerate(s["dates"])}
            _DMAP[sym] = m
        return m

    def zt_idx(sym, i):
        """该标的第 i 根是否收盘涨停。"""
        s = data.get(sym)
        if not s or i < 1:
            return False
        pl = limit_pct_of(sym)
        lim = round(s["closes"][i - 1] * (1 + pl), 2)
        return s["closes"][i] >= lim - 0.005

    def zt_on(sym, j):
        """该标的在全局日期 dates[j] 是否涨停。"""
        s = data.get(sym)
        if not s:
            return False
        i = _dmap(sym).get(dates[j])
        return zt_idx(sym, i) if i is not None else False

    def lianban(sym):
        """自最后一个交易日往回数连续涨停根数。"""
        s = data.get(sym)
        if not s:
            return 0
        i = len(s["dates"]) - 1
        k = 0
        while i >= 1 and zt_idx(sym, i):
            k += 1
            i -= 1
        return k

    rows = []
    li = len(dates) - 1
    for l2, info in sectors.items():
        syms = [s for s in info["members"] if s in data]
        if len(syms) < 5:
            continue
        series = [data[s] for s in syms]
        nav, vol, up, down = _eq_index(series, dates)
        ma20 = sum(nav[li - 19:li + 1]) / 20
        ma20p = sum(nav[li - 20:li]) / 20
        vma5 = sum(vol[li - 4:li + 1]) / 5
        # M1：连续 ≥2 日 突破
        def m1_at(j):
            a = sum(nav[j - 19:j + 1]) / 20
            vv = sum(vol[j - 4:j + 1]) / 5
            return nav[j] > a and vv > 0 and (vol[j] > vv * 1.2)
        m1 = bool(m1_at(li) and m1_at(li - 1)) if li >= 21 else False
        # M2：趋势股创阶段新高
        m2_cnt = sum(1 for s in series if s["trend"] and s["newhigh"])
        # M3：近 20 日共振
        up_ok = dn_ok = up_n = dn_n = 0
        for j in range(max(1, li - M3_WINDOW + 1), li + 1):
            sr = nav[j] / nav[j - 1] - 1
            mr = mkt_ret[j]
            if mr > 0:
                up_n += 1
                up_ok += 1 if sr > mr else 0
            elif mr < 0:
                dn_n += 1
                dn_ok += 1 if sr > mr else 0
        lead = (up_ok / up_n) if up_n else None
        resist = (dn_ok / dn_n) if dn_n else None
        m3 = bool(lead is not None and resist is not None and lead >= 0.6 and resist >= 0.6)
        # 强度三层（D3）
        zt = [s for s in syms if zt_on(s, li)]
        max_h = max((lianban(s) for s in zt), default=0)
        cm20 = sum(1 for s in zt if s[:2] in ("30", "68"))
        cap5 = sorted(series, key=lambda x: -x["amt_last"])[:5]
        cap_ret = sum(c["ret_last"] for c in cap5) / len(cap5) if cap5 else 0.0
        rows.append(dict(
            l2=l2, name=info["name"], n=len(syms),
            m1=dict(pass_=m1, close=round(nav[li], 4), ma20=round(ma20, 4),
                    vol_ratio=round((vol[li] / vma5) if vma5 else 0, 3)),
            m2=dict(pass_=m2_cnt >= 1, count=m2_cnt),
            m3=dict(pass_=m3, lead=round(lead, 3) if lead is not None else None,
                    resist=round(resist, 3) if resist is not None else None,
                    up_days=up_n, down_days=dn_n),
            d3=dict(zt=len(zt), max_h=max_h, cm20=cm20, cap5_ret=round(cap_ret, 4),
                    zt_syms=sorted(zt)[:20]),
            score=sum([m1, m2_cnt >= 1, m3]),
            ret_20d=round((nav[li] / nav[max(0, li - 20)] - 1) if li > 20 else 0.0, 4),
            ret_5d=round((nav[li] / nav[max(0, li - 5)] - 1) if li > 5 else 0.0, 4),
        ))

    rows.sort(key=lambda r: (-r["score"], -r["ret_20d"]))
    doc = {
        "_meta": {
            "schema_version": "1",
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "date": day, "date_axis": [dates[0], dates[-1]], "bars": len(dates),
            "sectors_evaluated": len(rows),
            "members_used": len(data),
            "criteria_source": "data/sector_themes.json::strong_mainline_criteria（§1.3 / v65@03:34）",
            "criteria_params": {k: crit[k]["params_placeholder"] for k in crit},
            "calibrated": False,
            "calibrated_note": "⚠️ M1/M2/M3 的阈值均为**占位值** —— 人格 SOP 只给定性表述、未给数值。"
                               "本产物原样带 calibrated=false，**不假装已标定**。标定归 R1.3 容差带。",
            "d3_note": "强度三层（高标/20cm/容量票）对应裁量点 D3 与 GEN-MAIN-03；容量票=成交额 Top5 平均涨跌幅",
            "generator": "scripts/build_sector_strength.py",
            "elapsed_sec": round(time.time() - t0, 1),
            "source_note": f"日线来自 {DAILY}（不做修改）",
        },
        "criteria": themes["strong_mainline_criteria"],
        "market": dict(ret_last=round(mkt_ret[-1], 4),
                       ret_20d=round((mkt_nav[li] / mkt_nav[max(0, li - 20)] - 1) if li > 20 else 0.0, 4)),
        "sectors": rows,
    }
    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"sector_strength_{day}.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    back = json.loads(out.read_text(encoding="utf-8"))
    assert back["_meta"]["sectors_evaluated"] == len(back["sectors"])

    print(f"\n{'板块':<18}{'n':>5}{'M1':>4}{'M2':>4}{'M3':>4}{'分':>4}{'涨停':>5}{'高标':>5}{'20cm':>5}{'容量5':>9}{'20日':>8}")
    for r in rows[: args.top]:
        print(f"{(r['name'] or r['l2'])[:16]:<18}{r['n']:>5}{'✓' if r['m1']['pass_'] else '·':>4}"
              f"{'✓' if r['m2']['pass_'] else '·':>4}{'✓' if r['m3']['pass_'] else '·':>4}"
              f"{r['score']:>4}{r['d3']['zt']:>5}{r['d3']['max_h']:>5}{r['d3']['cm20']:>5}"
              f"{r['d3']['cap5_ret']*100:>8.2f}%{r['ret_20d']*100:>7.1f}%")
    full = [r for r in rows if r["score"] == 3]
    print(f"\n三判据全过（强主线）= {len(full)} 个：{[r['name'] or r['l2'] for r in full][:12]}")
    print(f"selfcheck OK → {out} ({out.stat().st_size}B)  elapsed={time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
