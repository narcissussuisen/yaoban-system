"""全市场腾讯前复权日线回填（权威源库，替代不可信 TDX 日线）
分片串行, 5600 只 ≈ 40 分钟; 断点续跑（已存在则跳过）。
用法: python scripts/fetch_daily_tencent_all.py
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


def fetch(sym: str, start: str):
    prefix = 'sh' if sym[0] in ('6', '9') else 'sz'
    url = f'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{sym},day,{start},2026-12-31,640,qfq'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='ignore')
    data = json.loads(raw)
    node = data.get('data', {}).get(f'{prefix}{sym}', {})
    rows = node.get('qfqday') or node.get('day') or []
    out = []
    for r in rows:
        out.append({'symbol': sym, 'date': str(r[0]), 'open': float(r[1]), 'close': float(r[2]),
                    'high': float(r[3]), 'low': float(r[4])})
    return out


def main():
    st = QFQStore('2026')
    syms = [s for s in st.symbols() if not s.startswith(('399', '899', '5', '15', '16'))]
    st.close()
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ok = skip = 0
    for i, sym in enumerate(syms):
        fp = OUT / f'{sym}.parquet'
        if fp.exists():
            skip += 1
            continue
        try:
            rows = fetch(sym, '2025-06-01')
        except Exception:
            continue
        if rows:
            df = pd.DataFrame(rows)
            df.to_parquet(fp, index=False)
            ok += 1
        if (i + 1) % 500 == 0:
            print(f'  {i+1}/{len(syms)} ok={ok} skip={skip} {time.time()-t0:.0f}s', flush=True)
        time.sleep(0.25)
    print(f'完成: ok={ok} skip={skip} 耗时 {time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
