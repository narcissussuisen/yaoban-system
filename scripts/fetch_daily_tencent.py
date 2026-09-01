"""腾讯前复权日线库（权威源，替代不可信的 TDX 日线回填）
输出: F:/WorkBuddyItem/a股level2/daily_tencent/{sym}.parquet
用法: python scripts/fetch_daily_tencent.py --symbols 601212,601388,603132,000426,301003,301205,002017,002313
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys
import time
import urllib.request

OUT = pathlib.Path(r'F:/WorkBuddyItem/a股level2/daily_tencent')


def fetch(sym: str, start: str):
    prefix = 'sh' if sym[0] in ('6', '9') else 'sz'
    url = f'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{sym},day,{start},2026-12-31,640,qfq'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='ignore')
    data = json.loads(raw)
    node = data.get('data', {}).get(f'{prefix}{sym}', {})
    rows = node.get('qfqday') or node.get('day') or []
    def _f(v):
        if isinstance(v, dict):
            v = v.get('volume', 0) if 'volume' in v else v.get('amount', 0)
        try:
            return float(v) if v else 0.0
        except (TypeError, ValueError):
            return 0.0
    out = []
    for r in rows:
        out.append({'symbol': sym, 'date': str(r[0]), 'open': float(r[1]), 'close': float(r[2]),
                    'high': float(r[3]), 'low': float(r[4]),
                    'volume': _f(r[5]) if len(r) > 5 else 0,
                    'amount': _f(r[6]) if len(r) > 6 else 0})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--symbols', default='601212,601388,603132,000426')
    ap.add_argument('--start', default='2025-01-01')
    args = ap.parse_args()
    syms = [s.strip().zfill(6) for s in args.symbols.split(',') if s.strip()]
    OUT.mkdir(parents=True, exist_ok=True)
    for i, sym in enumerate(syms):
        try:
            rows = fetch(sym, args.start)
            if rows:
                import pandas as pd
                df = pd.DataFrame(rows)
                df.to_parquet(OUT / f'{sym}.parquet', index=False)
                print(f'{sym}: {len(rows)} 行 最新 {rows[-1]["date"]} 收{rows[-1]["close"]:.2f}', flush=True)
        except Exception as e:
            print(f'{sym}: ERR {type(e).__name__} {str(e)[:80]}', flush=True)
        time.sleep(0.2)


if __name__ == '__main__':
    main()
