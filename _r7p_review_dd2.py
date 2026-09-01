# -*- coding: utf-8 -*-
import pandas as pd, os
base = r'C:\Users\YZP\WorkBuddy\Claw\方法论与研究文档\yaoban-system\outputs'
names = ['r6p_eq_v2_base.csv','r6p_eq_v2_C2_ride.csv','r6p_eq_v2_C4_conc.csv',
         'r6p_eq_v2_C5_heat.csv','r6p_eq_v2_C6_k1.csv','r6p_eq_v2_C8_gate.csv','r6p_eq_v2_C9_all.csv']
for fn in names:
    f = os.path.join(base, fn)
    if not os.path.exists(f):
        print(fn, 'MISSING'); continue
    df = pd.read_csv(f)
    eq = df['equity'].astype(float)
    mult = eq.iloc[-1]/eq.iloc[0]
    peak = eq.cummax(); mdd = ((eq-peak)/peak).min()*100
    # window-restricted maxDD (4/1 onward)
    df2 = df[df['date'].astype(str) >= '2026-04-01']
    eq2 = df2['equity'].astype(float)
    peak2 = eq2.cummax(); mdd2 = ((eq2-peak2)/peak2).min()*100
    print(fn, 'mult=%.4f maxDD_full=%.2f%% maxDD_4to8=%.2f%%' % (mult, mdd, mdd2))
