# -*- coding: utf-8 -*-
import pandas as pd, os
base = r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\outputs'
files = {
 '2023': base + r'\r6p_eq_2023_base.csv',
 '2024': base + r'\r6p_eq_2024_base.csv',
 '2025': base + r'\r6p_eq_2025_base.csv',
 '2026': base + r'\r6p_eq_v2_base.csv',
}
for yr, f in files.items():
    df = pd.read_csv(f)
    print('=====', yr, os.path.basename(f), 'rows:', len(df), 'cols:', list(df.columns))
    print('HEAD:'); print(df.head(3).to_string())
    print('TAIL:'); print(df.tail(3).to_string())
    eqcol = None
    for c in df.columns:
        if any(k in c.lower() for k in ('eq','nav','净值','资产','equity')):
            eqcol = c; break
    print('eqcol:', eqcol)
    if eqcol is None:
        print('NO EQ COL'); continue
    eq = df[eqcol].astype(float)
    start_val = eq.iloc[0]; end_val = eq.iloc[-1]
    print('start=%.2f end=%.2f mult=%.4f' % (start_val, end_val, end_val/start_val))
    peak = eq.cummax(); dd = (eq/peak - 1).min()*100
    print('maxDD=%.2f%%' % dd)
    dcol = df.columns[0]
    df[dcol] = df[dcol].astype(str)
    df['_m'] = df[dcol].str[:7]
    g = df.groupby('_m').first()[[eqcol]].rename(columns={eqcol:'first'})
    g['pct'] = (g['first']/g['first'].shift(1)-1)*100
    print(g.to_string())
