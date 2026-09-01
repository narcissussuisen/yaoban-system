# -*- coding: utf-8 -*-
import pandas as pd, os
base = r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\outputs'
for yr in ['2023','2024','2025','2026']:
    f = os.path.join(base, f'r6p_candidates_{yr}.csv')
    df = pd.read_csv(f)
    cols = list(df.columns)
    dcol = None
    for c in cols:
        if 'date' in c.lower() or 'sd' in c.lower() or '信号' in c:
            dcol = c; break
    n_days = df[dcol].nunique() if dcol else '?'
    print(yr, 'rows:', len(df), 'signal_days:', n_days, 'avg:', round(len(df)/n_days,1) if dcol else '?', 'cols:', cols[:8])
