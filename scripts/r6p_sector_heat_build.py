"""R6' 第二轮数据：板块热度表（逐日 × 申万L2：涨停数 + 成交额占比）

用于备选软评分（选手板块主线聚焦：钱往哪流+涨停扩散）
输出 outputs/r6p_sector_heat_2026.csv: date, l2, zt_n, amt_share(%), zt_share(%)

用法: python scripts/r6p_sector_heat_build.py --workers 6
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.qfq_store import QFQStore, build_daily_map  # noqa: E402
from core.sell import limit_pct_of  # noqa: E402
from core.combo_sell import industry_of  # noqa: E402

OUT_TMPL = 'r6p_sector_heat_{year}.csv'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--year', default='2026')
    args = ap.parse_args()
    year = args.year
    t0 = time.time()
    st = QFQStore(year)
    symbols = [s for s in st.symbols() if not s.startswith(('399', '5', '15', '16', '899'))]
    dmap = build_daily_map(year, symbols, workers=args.workers)
    print(f'日线映射 {time.time()-t0:.0f}s, 逐日聚合板块...')
    agg: dict = {}
    for sym, rows in dmap.items():
        if len(rows) < 2:
            continue
        pct = limit_pct_of(sym)
        for i in range(1, len(rows)):
            d = rows[i][1]
            if d < f'{year}-04-01' or d > f'{year}-08-21':
                continue
            l2 = industry_of(sym, d) or 'NA'
            s = agg.setdefault((d, l2), {'zt': 0, 'amt': 0.0})
            close = float(rows[i][5])
            prev = float(rows[i - 1][5])
            lim = round(prev * (1 + pct), 2)
            if close >= lim - 0.005:
                s['zt'] += 1
            s['amt'] += float(rows[i][7])
    rows_out = []
    day_tot = {}
    for (d, l2), s in agg.items():
        day_tot[d] = day_tot.get(d, 0.0) + s['amt']
    for (d, l2), s in agg.items():
        rows_out.append({'date': d, 'l2': l2, 'zt_n': s['zt'],
                         'amt_share': round(s['amt'] / day_tot[d] * 100, 3) if day_tot[d] else 0.0})
    out = pd.DataFrame(rows_out).sort_values(['date', 'l2'])
    out_path = pathlib.Path(__file__).resolve().parent.parent / 'outputs' / OUT_TMPL.format(year=year)
    out.to_csv(out_path, index=False)
    print(f'写入 {out_path}: {len(out)} 行, {out["date"].nunique()} 天, '
          f'{out["l2"].nunique()} 个板块')
    print(f'总耗时 {time.time()-t0:.0f}s')
    st.close()


if __name__ == '__main__':
    main()