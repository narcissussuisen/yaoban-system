"""腾讯前复权日线交叉验证（vs TDX parquet 回填）
用法: python scripts/verify_daily.py
"""
import json
import urllib.request

SYMS = ['601212', '601388', '603132', '000426']


def fetch(sym: str):
    prefix = 'sh' if sym[0] in ('6', '9') else 'sz'
    url = f'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{sym},day,2026-08-24,2026-08-28,10,qfq'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='ignore')
    data = json.loads(raw)
    node = data.get('data', {}).get(f'{prefix}{sym}', {})
    rows = node.get('qfqday') or node.get('day') or []
    out = []
    for r in rows:
        out.append({'date': r[0], 'open': r[1], 'close': r[2], 'high': r[3], 'low': r[4]})
    return out


for sym in SYMS:
    try:
        rows = fetch(sym)
        print(f'{sym}: ' + ' | '.join(f"{r['date'][5:]}:c{r['close']}" for r in rows[-3:]))
    except Exception as e:
        print(f'{sym}: ERR {e}')
