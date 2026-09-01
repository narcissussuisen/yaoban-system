# -*- coding: utf-8 -*-
import pandas as pd, os
base = r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\outputs'
files = {'2023':'r6p_eq_2023_base.csv','2024':'r6p_eq_2024_base.csv','2025':'r6p_eq_2025_base.csv','2026':'r6p_eq_v2_base.csv','2026_v1':'r6p_equity_v1.csv'}
for yr, fn in files.items():
    df = pd.read_csv(os.path.join(base, fn))
    df['date'] = df['date'].astype(str)
    df['_m'] = df['date'].str[:7]
    g = df.groupby('_m').agg(f=('equity','first'), l=('equity','last'))
    g['mon_ret'] = (g['l']/g['f'] - 1)*100
    peak = df['equity'].cummax(); mdd = ((df['equity']-peak)/peak).min()*100
    mult = df['equity'].iloc[-1]/df['equity'].iloc[0]
    print('=====', yr, fn, 'mult=%.4f maxDD=%.2f%%' % (mult, mdd))
    print(g['mon_ret'].round(2).to_string())
