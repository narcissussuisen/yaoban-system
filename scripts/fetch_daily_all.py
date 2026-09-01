"""全市场前复权日线回填（低频多源重试版: 腾讯ifzq → 东财push2his 轮换）
应对风控: 0.8s 间隔, 连续失败 10 次切源, 失败 30 次暂停 60s
用法: python scripts/fetch_daily_all.py
"""
from __future__ import annotations
import json
import pathlib
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.qfq_store import QFQStore  # noqa: E402

OUT = pathlib.Path(r'F:/WorkBuddyItem/a股level2/daily_tencent')


def fetch_tx(sym: str):
    prefix = 'sh' if sym[0] in ('6', '9') else 'sz'
    url = f'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{sym},day,2025-01-01,2026-12-31,640,qfq'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='ignore')
    data = json.loads(raw)
    node = data.get('data', {}).get(f'{prefix}{sym}', {})
    rows = node.get('qfqday') or node.get('day') or []
    return [{'symbol': sym, 'date': str(r[0]), 'open': float(r[1]), 'close': float(r[2]),
             'high': float(r[3]), 'low': float(r[4])} for r in rows]


def fetch_em(sym: str):
    secid = ('1.' if sym[0] in ('6', '9') else '0.') + sym
    url = (f'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}'
           f'&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58'
           f'&klt=101&fqt=1&beg=20250101&end=20261231')
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='ignore')
    data = json.loads(raw)
    kl = (data.get('data') or {}).get('klines') or []
    out = []
    for line in kl:
        p = line.split(',')
        if len(p) < 6:
            continue
        out.append({'symbol': sym, 'date': p[0], 'open': float(p[1]), 'close': float(p[2]),
                    'high': float(p[3]), 'low': float(p[4]),
                    'volume': float(p[5]), 'amount': float(p[6]) if len(p) > 6 else 0})
    return out


def main():
    st = QFQStore('2026')
    syms = [s for s in st.symbols() if not s.startswith(('399', '899', '5', '15', '16'))]
    st.close()
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ok = skip = 0
    fails = 0
    source = 0  # 0=腾讯 1=东财
    fetchers = [fetch_tx, fetch_em]
    for i, sym in enumerate(syms):
        fp = OUT / f'{sym}.parquet'
        if fp.exists():
            skip += 1
            continue
        rows = None
        for attempt in range(3):
            try:
                rows = fetchers[source](sym)
                if rows:
                    break
            except Exception:
                rows = None
            fails += 1
            if fails % 10 == 0:
                source = 1 - source
                print(f'  切换数据源 -> {"东财" if source else "腾讯"} (fails={fails})', flush=True)
            if fails % 30 == 0:
                print('  连续失败 30 次, 暂停 60s', flush=True)
                time.sleep(60)
        if rows:
            pd.DataFrame(rows).to_parquet(fp, index=False)
            ok += 1
            fails = 0
        if (i + 1) % 300 == 0:
            print(f'  {i+1}/{len(syms)} ok={ok} skip={skip} {time.time()-t0:.0f}s', flush=True)
        time.sleep(0.8)
    print(f'完成: ok={ok} skip={skip} 耗时 {time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
