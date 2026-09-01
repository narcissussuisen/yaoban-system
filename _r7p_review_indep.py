# -*- coding: utf-8 -*-
"""R7' 终局评审: 独立复算 daily_pipeline_v5 2026-08-21 全部核心数字（不采信管线自身打印）"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\src')))
sys.path.insert(0, str(pathlib.Path(r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system')))
import numpy as np
import pandas as pd
from data.qfq_store import QFQStore, build_daily_map
from core import strategies as S
from core.combo_sell import industry_of, concept_of, preload_daily
from core.sell import limit_pct_of
from core.sentiment import emotion_thermometer

st = QFQStore('2026', cache_minute=64)
syms = [s for s in st.symbols() if not s.startswith(('399','5','15','16','899'))]
print('预载...', flush=True)
dmap = build_daily_map('2026', syms, workers=6)
preload_daily(dmap)
d = '2026-08-21'

# --- 独立温度计组件 ---
zt = dt_cnt = touch = zhaban = 0
pcts = []
max_h = 0
sector_zt = {}
for sym, rows in dmap.items():
    if len(rows) < 2: continue
    dates = [r[1] for r in rows]
    if d not in dates: continue
    i = dates.index(d)
    if i == 0: continue
    pc = float(rows[i-1][5]); c = float(rows[i][5]); hi = float(rows[i][3])
    pct_lim = limit_pct_of(sym)
    lim = round(pc*(1+pct_lim), 2); dlim = round(pc*(1-pct_lim), 2)
    is_zt = c >= lim - 0.005
    if is_zt: zt += 1
    if c <= dlim + 0.005: dt_cnt += 1
    if hi >= lim - 0.005:
        touch += 1
        if not is_zt: zhaban += 1
    pcts.append(c/pc - 1)
    l2 = industry_of(sym, d) or 'NA'
    sector_zt[l2] = sector_zt.get(l2, 0) + (1 if is_zt else 0)
    lb = 0; j = i
    while j >= 1 and float(rows[j][5]) >= round(float(rows[j-1][5])*(1+pct_lim),2) - 0.005:
        lb += 1; j -= 1
    max_h = max(max_h, lb)
med = float(np.median(pcts))*100
zhaban_rate = zhaban/touch*100 if touch else 0.0
t = emotion_thermometer(zt, dt_cnt, zhaban_rate, max_h, median_pct=med)
print('独立: zt=%d dt=%d touch=%d zhaban_rate=%.1f%% max_h=%d med=%.2f%% temp=%s stage=%s' %
      (zt, dt_cnt, touch, zhaban_rate, max_h, med, t['temp'], t['stage']))
print('独立板块涨停前5:', sorted(sector_zt.items(), key=lambda x:-x[1])[:5])

# --- 独立候选池 ---
from datetime import date as _d, timedelta as _td
window = []
dd = _d.fromisoformat(d)
while len(window) < 6:
    if dd.weekday() < 5: window.append(dd.isoformat())
    dd -= _td(days=1)
cands = []
for sym, rows in dmap.items():
    if len(rows) < 70: continue
    df = pd.DataFrame(rows, columns=['symbol','date','open','high','low','close','volume','amount'])
    df['date'] = df['date'].astype(str)
    sig = S.detect_huigui_v5(df)
    dlist = df['date'].tolist()
    for sd in window:
        if sd not in dlist: continue
        if not bool(sig[df['date']==sd].any()): continue
        i = dlist.index(sd)
        r = df.iloc[i]
        yang = float(r['close']) > float(r['open'])
        brk5 = False
        if i >= 5:
            brk5 = float(r['close']) >= max(float(x) for x in df['close'].iloc[i-5:i])
        l2 = industry_of(sym, sd) or ''
        cands.append({'symbol':sym,'sig_date':sd,'l2':l2,'yang':yang,'brk5':brk5,
                      'heat': sector_zt.get(l2,0) if sd==d else 0,
                      'concept': concept_of(sym, sd) or ''})
cand_df = pd.DataFrame(cands).drop_duplicates(subset=['symbol'])
cand_df = cand_df.sort_values(['heat','brk5','yang'], ascending=False)
print('独立候选池:', len(cand_df), '只')
print('独立 top-4:')
for _, r in cand_df.head(4).iterrows():
    print('  ', r['symbol'], r['sig_date'], r['l2'], 'heat=', r['heat'], 'brk5=', r['brk5'], 'yang=', r['yang'], 'concept=', r['concept'])
st.close()
