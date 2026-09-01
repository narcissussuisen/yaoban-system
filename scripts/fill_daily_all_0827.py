"""8/27 盘前数据准备：全市场 TDX 日线回填 8/21-8/27（F盘止于 8/21）
输出: F:/WorkBuddyItem/a股level2/daily/{sym}.parquet
北交所(4/8/92) market=2, 上证(6/900) market=1, 深市 market=0
用法: python scripts/fill_daily_all_0827.py [--max 6000]
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from pytdx.hq import TdxHq_API  # noqa: E402

from data.qfq_store import QFQStore  # noqa: E402

OUT = pathlib.Path(r'F:/WorkBuddyItem/a股level2/daily')
SERVERS = [('115.238.56.198', 7709), ('115.238.90.165', 7709)]
FIELDS = ['symbol', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount']


def market_of(sym: str) -> int:
    if sym.startswith('900'):
        return 1
    if sym[0] in ('4', '8') or sym.startswith('92'):
        return 2
    if sym[0] in ('6', '9', '5'):
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--max', type=int, default=0, help='调试: 只回填前 N 只')
    args = ap.parse_args()
    st = QFQStore('2026')
    syms = [s for s in st.symbols() if not s.startswith(('399', '899'))]
    if args.max:
        syms = syms[:args.max]
    api = TdxHq_API(heartbeat=False)
    ok = False
    for host, port in SERVERS:
        if api.connect(host, port, time_out=8):
            print(f'connected {host}:{port}', flush=True)
            ok = True
            break
    if not ok:
        print('连接失败'); return
    t0 = time.time()
    n_ok = n_skip = 0
    for i, sym in enumerate(syms):
        m = market_of(sym)
        try:
            bars = api.get_security_bars(9, m, sym, 0, 7)  # 最近7个交易日
        except Exception:
            bars = None
        if not bars:
            n_skip += 1
            continue
        df = pd.DataFrame(bars)
        if 'vol' in df.columns and 'volume' not in df.columns:
            df = df.rename(columns={'vol': 'volume'})
        df['date'] = df['datetime'].str[:10]
        df['symbol'] = sym
        for col in FIELDS:
            if col not in df.columns:
                df[col] = 0
        df = df[FIELDS].sort_values('date')
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        df.to_parquet(OUT / f'{sym}.parquet', index=False)
        n_ok += 1
        if (i + 1) % 500 == 0:
            print(f'  {i+1}/{len(syms)} ok={n_ok} skip={n_skip} {time.time()-t0:.0f}s', flush=True)
    api.disconnect()
    print(f'完成: ok={n_ok} skip={n_skip} 耗时 {time.time()-t0:.0f}s')
    st.close()


if __name__ == '__main__':
    main()