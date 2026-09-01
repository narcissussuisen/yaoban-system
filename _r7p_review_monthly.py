# -*- coding: utf-8 -*-
import pandas as pd, os
base = r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\outputs'
files = {'2023':'r6p_eq_2023_base.csv','2024':'r6p_eq_2024_base.csv','2025':'r6p_eq_2025_base.csv','2026':'r6p_eq_v2_base.csv'}
for yr, fn in files.items():
    df = pd.read_csv(os.path.join(base, fn))
    df['date'] = df['date'].astype(str)
    df['_m'] = df['date'].str[:7]
    last = df.groupby('_m').last()['equity'].astype(float)
    ret = (last/last.shift(1) - 1)*100
    print('=====', yr)
    print(ret.round(2).to_string())
