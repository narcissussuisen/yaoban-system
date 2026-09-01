"""R5' P1×1 整改：冰点 3-5 日窗口复核（校正后口径）+ 锚日数值核对

问题: 我方「冰点+黄金坑=修复预期→加仓」与系统 P1 两年证据（冰点=下跌中继, 次日-1.54%/-0.95%）冲突
复核: 校正口径(±0.005容差+BJ 30%)下的冰点日 → 次1/3/5日指数与全市场表现
"""
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent


def main():
    df = pd.read_csv(BASE / 'outputs' / 'sentiment_full_2026.csv')
    df['date'] = df['date'].astype(str)
    print('== 锚日数值核对（校正口径） ==')
    for d in ['2026-04-21', '2026-06-18', '2026-07-06', '2026-07-17', '2026-08-13']:
        r = df[df.date == d].iloc[0]
        print(f'  {d}: zt={r["zt"]} dt={r["dt"]} touch={r["touch"]} zhaban_rate={r["zhaban_rate"]} '
              f'max_h={r["max_h"]} lianban={r["lianban_rate"]} med={r["median_pct"]}')
    print()
    # 冰点日（校正口径: zt<40 且 dt>zt）
    bing = df[(df.zt < 40) & (df.dt > df.zt)]
    print(f'== 冰点日 {len(bing)} 个: {bing.date.tolist()} ==')
    con = sqlite3.connect(BASE / 'data' / 'yaoban.db')
    idx = pd.read_sql("SELECT date, close FROM index_daily WHERE symbol='sh000001' "
                      "AND date >= '2026-01-01' ORDER BY date", con)
    con.close()
    idx['date'] = idx['date'].astype(str)
    dates = idx['date'].tolist()
    print()
    print('冰点日后续指数收益（校正口径）:')
    print(f'{"冰点日":<12}{"指数":>8}{"次1日%":>8}{"次3日%":>8}{"次5日%":>8}{"次5日收盘":>9}')
    rows = []
    for d in bing.date.tolist():
        if d not in dates:
            continue
        i = dates.index(d)
        close0 = float(idx.close.iloc[i])
        vals = []
        for h in [1, 3, 5]:
            j = i + h
            vals.append((float(idx.close.iloc[j]) / close0 - 1) * 100 if j < len(idx) else None)
        c5 = float(idx.close.iloc[min(i + 5, len(idx) - 1)]) if i + 5 < len(idx) else None
        v0 = f'{vals[0]:+.2f}' if vals[0] is not None else 'N/A'
        v1 = f'{vals[1]:+.2f}' if vals[1] is not None else 'N/A'
        v2 = f'{vals[2]:+.2f}' if vals[2] is not None else 'N/A'
        c5s = f'{c5:.0f}' if c5 is not None else 'N/A'
        print(f'{d:<12}{close0:>8.0f}{v0:>8}{v1:>8}{v2:>8}{c5s:>9}')
        rows.append((d, vals[0], vals[1], vals[2]))
    vr = pd.DataFrame(rows, columns=['d', 'd1', 'd3', 'd5'])
    print()
    print(f'冰点次日指数均值 {vr.d1.mean():+.2f}%（P1 口径: -1.54~-0.95%）')
    print(f'冰点次3日指数均值 {vr.d3.mean():+.2f}%, 次5日 {vr.d5.dropna().mean():+.2f}%')
    print(f'次5日为正的占比: {(vr.d5.dropna() > 0).mean()*100:.0f}%（N/A 剔除）')
    # 冰点后 zt 回升情况
    print()
    print('冰点后涨停家数回升:')
    for d in bing.date.tolist():
        sub = df[df.date > d].head(5)
        if not sub.empty:
            print(f'  {d}: zt={sub.zt.tolist()}')


if __name__ == '__main__':
    main()