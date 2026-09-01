# -*- coding: utf-8 -*-
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\src')))
sys.path.insert(0, str(pathlib.Path(r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system')))
import pandas as pd
from data.qfq_store import QFQStore, build_daily_map
from core import strategies as S
from core.combo_sell import industry_of, concept_of, preload_daily
from datetime import date as _d, timedelta as _td
st = QFQStore('2026', cache_minute=64)
syms = [s for s in st.symbols() if not s.startswith(('399','5','15','16','899'))]
dmap = build_daily_map('2026', syms, workers=6)
preload_daily(dmap)
d = '2026-08-21'
window = []
dd = _d.fromisoformat(d)
while len(window) < 6:
    if dd.weekday() < 5: window.append(dd.isoformat())
    dd -= _td(days=1)
cands = []
sym_sds = {}
for sym, rows in dmap.items():
    if len(rows) < 70: continue
    df = pd.DataFrame(rows, columns=['symbol','date','open','high','low','close','volume','amount'])
    df['date'] = df['date'].astype(str)
    sig = S.detect_huigui_v5(df)
    dlist = df['date'].tolist()
    for sd in window:
        if sd not in dlist: continue
        if not bool(sig[df['date']==sd].any()): continue
        cands.append(sym)
        sym_sds.setdefault(sym, []).append(sd)
print('RAW(len(cands)):', len(cands))
print('UNIQUE symbols:', len(set(cands)))
multi = {s: v for s, v in sym_sds.items() if len(v) > 1}
print('symbols with >1 signal day in window:', len(multi))
from collections import Counter
print('signal-day count distribution:', Counter(len(v) for v in sym_sds.values()))
print('sample multi:', list(multi.items())[:5])
st.close()
