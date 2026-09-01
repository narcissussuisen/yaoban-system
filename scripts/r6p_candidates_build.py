"""R6' 数据底座：全市场候选扫描（R1' 信号日 × 日线级软评分 × 板块归属 × 温度上下文）

输出 outputs/r6p_candidates_2026.csv 列:
  date(信号日), symbol, close, yang(信号日收阳), brk5(信号日突破前5日最高收盘),
  buy_yang(T+1收阳), buy_vol_up(T+1放量), l2(申万L2), concept(题材或空)

口径: detect_huigui_v5 全窗口逐symbol一遍 → 信号日集合; 4/1~8/21 窗口
用法: python scripts/r6p_candidates_build.py --workers 6
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
from core import strategies as S  # noqa: E402
from core.combo_sell import industry_of, concept_of  # noqa: E402
from core.sell import limit_pct_of  # noqa: E402

OUT_TMPL = 'r6p_candidates_{year}.csv'


def zt_huicai_signal(df, limit_pct_of):
    """涨停回踩补充信号(选手8/27实证: 湖南白银/黄河旋风=涨停后回踩买入):
    信号日 = 涨停(C日)之后第一个未涨停交易日(回踩/横盘日); 买入窗口 k=1..5 (E4 盘中确认)"""
    out = pd.Series(False, index=df.index)
    n = len(df)
    sym = str(df['symbol'].iloc[0]) if 'symbol' in df.columns else ''
    if n < 15: return out
    c = df['close'].astype(float)
    for i in range(2, n-1):
        pl = limit_pct_of(sym)
        # i-2 涨停且 i-1 未涨停 -> 信号日 i-1
        lim1 = round(float(c.iloc[i-2]) * (1 + pl), 2)
        if float(c.iloc[i-1]) >= lim1 - 0.005:
            continue  # i-1 仍是涨停(连板)
        lim2 = round(float(c.iloc[i-3]) * (1 + pl), 2) if i >= 3 else 0
        zt_prev = (i-2 >= 0) and float(c.iloc[i-2]) >= lim2 - 0.005 if i >= 3 else False
        if i == 2:
            zt_prev = False
        # i-1 是涨停后的第一个非涨停日(由其前一游是否为涨停判定)
        if zt_prev:
            out.iloc[i-1] = True
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--year', default='2026')
    args = ap.parse_args()
    year = args.year
    wy = str(int(year) - 1)
    t0 = time.time()
    st = QFQStore(year)
    symbols = [s for s in st.symbols() if not s.startswith(('399', '5', '15', '16', '899'))]
    print(f'{len(symbols)} 只, 构建日线映射({wy}预热+{year})...')
    dmap25 = build_daily_map(wy, symbols, workers=args.workers)
    dmap26 = build_daily_map(year, symbols, workers=args.workers)
    print(f'日线映射 {time.time()-t0:.0f}s, 逐只检测...')
    rows_out = []
    for sym in dmap26:
        rows = dmap25.get(sym, []) + dmap26.get(sym, [])
        if len(rows) < 65:
            continue
        df = pd.DataFrame(rows, columns=['symbol', 'date', 'open', 'high', 'low',
                                          'close', 'volume', 'amount'])
        df['date'] = df['date'].astype(str)
        # P0-5修复(2026-09-01): 上限改为数据驱动(旧硬编码2026-08-28导致候选表永远滞后; daily/增量方案已打通)
        _maxd = df['date'].max()
        df = df[df['date'] <= _maxd].reset_index(drop=True)
        sig = S.detect_huigui_v5(df) | zt_huicai_signal(df, limit_pct_of)
        dates = df['date'].tolist()
        for i in range(len(df)):
            if not bool(sig.iloc[i]):
                continue
            d = dates[i]
            if d < f'{year}-04-01' or d > _maxd:
                continue
            r = df.iloc[i]
            yang = float(r['close']) > float(r['open'])
            brk5 = False
            if i >= 5:
                brk5 = float(r['close']) >= max(float(x) for x in df['close'].iloc[i - 5:i])
            buy_yang = buy_vol = None
            if i + 1 < len(df):
                r2 = df.iloc[i + 1]
                buy_yang = bool(float(r2['close']) > float(r2['open']))
                buy_vol = bool(float(r2['volume']) > float(r['volume']))
            rows_out.append({'date': d, 'symbol': sym,
                             'close': round(float(r['close']), 3),
                             'yang': yang, 'brk5': brk5,
                             'buy_yang': buy_yang, 'buy_vol_up': buy_vol,
                             'l2': industry_of(sym, d),
                             'concept': concept_of(sym, d) or ''})
    out = pd.DataFrame(rows_out)
    out_path = pathlib.Path(__file__).resolve().parent.parent / 'outputs' / OUT_TMPL.format(year=year)
    out.to_csv(out_path, index=False)
    print(f'写入 {out_path}: {len(out)} 条信号, {out["date"].nunique()} 个信号日, '
          f'日均 {len(out)/max(out["date"].nunique(),1):.1f} 候选')
    print(f'总耗时 {time.time()-t0:.0f}s')
    st.close()


if __name__ == '__main__':
    main()