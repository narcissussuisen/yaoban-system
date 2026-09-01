"""R5' 第一轮验证：情绪温度计全窗口 + 选手仓位锚 + 黄金坑

用法: python scripts/r5p_thermometer_validate.py
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.sentiment import emotion_thermometer, gold_pit_zone  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent


def main():
    df = pd.read_csv(BASE / 'outputs' / 'sentiment_full_2026.csv')
    df['date'] = df['date'].astype(str)
    con = sqlite3.connect(BASE / 'data' / 'yaoban.db')
    idx = pd.read_sql('SELECT date, close FROM index_daily WHERE symbol="sh000001" '
                      "AND date >= '2026-01-01' ORDER BY date", con)
    con.close()
    idx['date'] = idx['date'].astype(str)
    m = df.merge(idx, on='date', how='left')
    rows = []
    for _, r in m.iterrows():
        t = emotion_thermometer(int(r['zt']), int(r['dt']) if pd.notna(r['dt']) else None,
                               float(r['zhaban_rate']), int(r['max_h']),
                               float(r['lianban_rate']) if pd.notna(r['lianban_rate']) else None,
                               float(r['median_pct']) if pd.notna(r['median_pct']) else None)
        g = gold_pit_zone(float(r['close']) if pd.notna(r['close']) else None)
        rows.append({'date': r['date'], 'temp': t['temp'], 'stage': t['stage'],
                     'note': t['note'], 'zone': g['zone'], 'index': r['close']})
    out = pd.DataFrame(rows)
    # 阶段统计
    print('== 情绪六阶段分布（2026, 154 交易日） ==')
    print(out['stage'].value_counts().to_string())
    print()
    print('== 锚日温度 vs 选手仓位 ==')
    anchors = [('2026-04-21', 99.8), ('2026-06-18', 69.3), ('2026-07-06', 34.8),
               ('2026-07-17', 20.0), ('2026-08-13', 51.2)]
    for d, pos in anchors:
        r = out[out.date == d]
        if r.empty:
            print(f'{d}: 无数据'); continue
        r = r.iloc[0]
        ok = '✓' if (pos >= 50 and r['temp'] >= 50) or (pos <= 30 and r['temp'] <= 35) else '≈'
        print(f'{d}: temp={r["temp"]:>5} {r["stage"]:<4} 选手仓位{pos:>5}% {ok} 指数{r["index"]:.0f}/{r["zone"]}')
    print()
    print('== 冰点日清单（stage=冰点 或 dt>zt） ==')
    bing = out[out.stage == '冰点']
    print(bing[['date', 'temp', 'stage', 'index', 'zone']].to_string(index=False))
    print()
    print('== 黄金坑区间（指数<4000） ==')
    gp = out[out.zone == 'gold_pit']
    if not gp.empty:
        print(f'{gp.date.iloc[0]} ~ {gp.date.iloc[-1]}, 共 {len(gp)} 天, '
              f'指数最低 {gp["index"].min():.0f}, 区间内均温 {gp["temp"].mean():.1f}')
    print()
    print('== 每月均温 ==')
    out['mon'] = out.date.str[:7]
    print(out.groupby('mon')['temp'].mean().round(1).to_string())
    # 温度 vs 次日全市场中位数（情绪→隔日收益 方向检查, 与 GAP_intraday P1 结论互证）
    print()
    print('== 温度分桶 vs 次日中位数（10 桶） ==')
    df2 = df.copy()
    df2['next_median'] = df2['median_pct'].shift(-1)
    df2['bucket'] = pd.cut(df2['zt'], [0, 40, 60, 80, 100, 200], labels=['<40','40-60','60-80','80-100','≥100'])
    print(df2.groupby('bucket', observed=True)['next_median'].agg(['count', 'mean']).round(3).to_string())


if __name__ == '__main__':
    main()