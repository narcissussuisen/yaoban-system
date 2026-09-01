"""P0-C 补充：阈值 50 扫描 + 剔除天数统计"""
import pandas as pd, pathlib, subprocess, sys

OUT = pathlib.Path("outputs")
for y in (2025, 2026):
    sent = pd.read_csv(OUT / f"sentiment_daily_{y}.csv")
    for thr in (45, 50, 55):
        nd = (sent["zhaban_rate"] > thr).sum()
        print(f"{y} 炸板率>{thr}: {nd} 天（{nd/len(sent)*100:.0f}%） 涉及交易日的信号数需查")
