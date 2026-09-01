# -*- coding: utf-8 -*-
import pandas as pd, glob, os
base = r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\outputs'
for f in sorted(glob.glob(os.path.join(base, 'r6p_eq_v2_*.csv'))):
    df = pd.read_csv(f)
    eq = df['equity'].astype(float)
    mult = eq.iloc[-1]/eq.iloc[0]
    peak = eq.cummax(); mdd = ((eq-peak)/peak).min()*100
    print(os.path.basename(f), 'mult=%.4f maxDD=%.2f%%' % (mult, mdd))
