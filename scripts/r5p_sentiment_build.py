"""R5' 第一轮：全量情绪指标构建（2026，F盘日线直算，与 sentiment_daily_2026.csv 交叉验证）

输出 outputs/sentiment_full_2026.csv 列:
  date, zt(涨停数), dt(跌停数), touch(触板数), zhaban(炸板数), zhaban_rate, max_h(最高连板),
  lianban_rate(昨日涨停今日晋级率), median_pct(全市场涨幅中位数%), up_count/down_count(涨跌家数)

证据口径:
  涨停判定 close ≥ round(prev×(1+limit_pct),2)-0.005; 跌停 close ≤ round(prev×(1-limit_pct),2)+0.005
  （R4R5 评审整改：±0.005 容差吸收 float64 表示误差，等价严格「收盘=涨停价」；北交所 30% 含 920 段；
    SH B 股 900 段计入 10% 涨停口径（样本极少，如不计入须显式声明）
  炸板 = 当日触板(high≥涨停价-0.005)且收盘未涨停
  连板率 = |昨日涨停 ∩ 今日涨停| / |昨日涨停|（v: 连板率 10% 口径）
  冰点 = zt<40 且 dt>zt（v47: 涨停34/跌停45; USAGE §1.2）

用法: python scripts/r5p_sentiment_build.py [--workers 6]
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from data.qfq_store import QFQStore, build_daily_map  # noqa: E402
from core.sell import limit_pct_of  # noqa: E402

OUT_TMPL = 'sentiment_full_{year}.csv'
OLD = pathlib.Path(__file__).resolve().parent.parent / 'outputs' / 'sentiment_daily_2026.csv'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--year', default='2026')
    args = ap.parse_args()
    t0 = time.time()
    st = QFQStore(args.year)
    symbols = [s for s in st.symbols() if not s.startswith(('399', '5', '15', '16', '899'))]  # 899=北证指数
    print(f'{len(symbols)} 只股票, 构建日线映射...')
    dmap = build_daily_map(args.year, symbols, workers=args.workers)
    print(f'日线映射完成 {time.time()-t0:.0f}s, 计算日级指标...')
    # 逐日统计
    day_stats: dict[str, dict] = {}
    for sym, rows in dmap.items():
        if not rows or len(rows) < 2:
            continue
        pct_limit = limit_pct_of(sym)
        # 连板数（前向扫描）
        lb = 0
        for i in range(len(rows)):
            d = rows[i][1]
            close = float(rows[i][5])
            high = float(rows[i][3])
            low = float(rows[i][4])
            prev_close = float(rows[i - 1][5]) if i >= 1 else None
            s = day_stats.setdefault(d, {'zt': 0, 'dt': 0, 'touch': 0, 'zhaban': 0,
                                         'up': 0, 'down': 0, 'n': 0, 'pcts': [],
                                         'max_h': 0, 'zt_set': set()})
            if prev_close:
                limit_up_px = round(prev_close * (1 + pct_limit), 2)
                limit_dn_px = round(prev_close * (1 - pct_limit), 2)
                # 容差 ±0.005（R4R5 评审整改：原 1 分容差使 zt 膨胀 1.5~2 倍）
                is_zt = close >= limit_up_px - 0.005
                is_dt = close <= limit_dn_px + 0.005
                if is_zt:
                    lb += 1
                    s['zt'] += 1
                    s['zt_set'].add(sym)
                else:
                    lb = 0
                if is_dt:
                    s['dt'] += 1
                if high >= limit_up_px - 0.005:
                    s['touch'] += 1
                    if not is_zt:
                        s['zhaban'] += 1
                pct = close / prev_close - 1
                s['pcts'].append(pct)
                if pct > 0:
                    s['up'] += 1
                elif pct < 0:
                    s['down'] += 1
                s['n'] += 1
            else:
                lb = 0
            if lb > s['max_h']:
                s['max_h'] = lb
    print(f'日级指标完成 {time.time()-t0:.0f}s, 汇总...')
    dates = sorted(day_stats.keys())
    rows_out = []
    prev_zt_set = set()
    for d in dates:
        s = day_stats[d]
        zt = s['zt']
        dt = s['dt']
        touch = s['touch']
        zhaban = s['zhaban']
        zhaban_rate = zhaban / touch * 100 if touch else 0.0
        lianban_rate = 0.0
        if prev_zt_set:
            lianban_rate = len(prev_zt_set & s['zt_set']) / len(prev_zt_set) * 100
        med = float(np.median(s['pcts'])) * 100 if s['pcts'] else None
        rows_out.append({'date': d, 'zt': zt, 'dt': dt, 'touch': touch, 'zhaban': zhaban,
                         'zhaban_rate': round(zhaban_rate, 2), 'max_h': s['max_h'],
                         'lianban_rate': round(lianban_rate, 2),
                         'median_pct': round(med, 3) if med is not None else None,
                         'up_count': s['up'], 'down_count': s['down']})
        prev_zt_set = s['zt_set']
    out = pd.DataFrame(rows_out)
    out_path = pathlib.Path(__file__).resolve().parent.parent / 'outputs' / OUT_TMPL.format(year=args.year)
    out.to_csv(out_path, index=False)
    print(f'写入 {out_path} ({len(out)} 行)')
    # 交叉验证: 与旧 sentiment_daily_2026.csv 的 zt/touch/zhaban/max_h 对比
    if OLD.exists():
        old = pd.read_csv(OLD)
        old['date'] = old['date'].astype(str)
        m = out.merge(old, on='date', suffixes=('_new', '_old'))
        for c in ['zt', 'touch', 'zhaban', 'max_h']:
            diff = (m[c + '_new'] != m[c + '_old']).sum()
            print(f'  {c}: {diff}/{len(m)} 行不一致（新口径 vs 旧口径）')
    st.close()


if __name__ == '__main__':
    main()