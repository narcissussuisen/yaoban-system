# -*- coding: utf-8 -*-
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\src')))
sys.path.insert(0, str(pathlib.Path(r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system')))
from data.qfq_store import QFQStore, build_daily_map
st = QFQStore('2026', cache_minute=64)
syms = [s for s in st.symbols() if not s.startswith(('399','5','15','16','899'))]
dmap = build_daily_map('2026', syms, workers=6)
for sym in ['301165','603236','000856','300833']:
    rows = dmap.get(sym)
    if not rows: 
        print(sym, 'NO ROWS'); continue
    dates = [r[1] for r in rows]
    # print last 8 rows before and at 08-21
    sel = [(r[1], r[4], r[5]) for r in rows if '2026-08-1' in r[1] or '2026-08-2' in r[1]]
    print(sym, 'last date in store:', dates[-1], '| total rows:', len(dates))
    for d, lo, c in sel:
        print('  ', d, 'low=', lo, 'close=', c)
st.close()
