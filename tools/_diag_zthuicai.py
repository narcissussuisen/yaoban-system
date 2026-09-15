# -*- coding: utf-8 -*-
"""诊断：涨停回踩低吸——对比「缩量的比较基准」两种口径。

背景：现行实现把「回调期均量 / 涨停柱当日量 ≤ 0.7」当作缩量判据。
但这个比值天然偏小（涨停柱本身是放量柱），实测覆盖不到真实交易样本。
候选替代口径：与**涨停前的常态量**比较（近 N 日均量），这才符合「缩量回踩」的字面含义。
"""
import sys
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from iteration import market, rules  # noqa: E402

CASES = [
    ("603615", "茶花股份", "2026-08-28", "涨停回踩低吸（画像 §六 已解答）"),
    ("605006", "山东玻纤", "2026-09-10", "9/10 买入①最优档，信号日 9/9 涨停回踩"),
    ("003018", "金富科技", "2026-09-03", "9/3 上升回档精选，当日涨停"),
    ("002909", "集泰股份", "2026-09-08", "9/8 试错龙头抱团，液冷"),
    ("603162", "海通发展", "2026-09-09", "趋势反包战法"),
    ("603618", "杭电股份", "2026-09-08", "上升回档战法"),
]

for code, name, entry, note in CASES:
    df = market.load_daily(code)
    if df is None:
        print(f"{code} {name}: 无数据")
        continue
    b = rules.bars(df)
    c, h, l, v = b["close"], b["high"], b["low"], b["volume"]
    dates = [str(x) for x in df["date"]]
    ei = dates.index(entry) if entry in dates else None
    zt = rules.limit_up_mask(b, code)
    zs = [i for i in range(len(c)) if zt[i] and 0 <= (ei - i if ei else -1) <= 8]
    print(f"===== {code} {name} 建仓日 {entry}（{note}）")
    if not zs:
        print("   建仓前 8 日内无涨停柱 → 不属本战法")
        continue
    for z in zs:
        vma5_pre = float(v[max(0, z - 5):z].mean())
        vma5 = float(v[max(0, z - 4):z + 1].mean())
        k0, k1 = z + 1, ei
        if k1 is None or k1 <= k0:
            print(f"   涨停柱 {dates[z]}: 无回踩期")
            continue
        seg = v[k0:k1 + 1]
        peak_ratio = float(seg.mean()) / float(v[z]) if v[z] > 0 else float("nan")
        pre_ratio = float(seg.mean()) / vma5_pre if vma5_pre > 0 else float("nan")
        lownotbroken = float(l[k0:k1 + 1].min()) >= float(l[z])
        above_ma5 = float(c[ei]) > float(rules.roll_mean(c, 5)[ei])
        print(f"   涨停柱 {dates[z]} 距建仓 {ei - z} 日 | 涨停柱量/MA5={v[z]/vma5:.2f}")
        print(f"      回踩期均量/涨停柱量 = {peak_ratio:.2f}   ← 现行口径（要求 ≤0.7）")
        print(f"      回踩期均量/涨停前5日均量 = {pre_ratio:.2f}   ← 候选口径")
        print(f"      未破涨停柱低点={lownotbroken}  建仓日站上MA5={above_ma5}")
        print(f"      回踩期均量={seg.mean():.0f}  涨停柱量={v[z]:.0f}  涨停前5日均量={vma5_pre:.0f}")
