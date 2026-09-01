"""全市场股票名称：mootdx(沪/京) ∪ pytdx market0(深) → data/stock_names_full.json

用法: python scripts/fetch_stock_names.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

OUT = pathlib.Path(__file__).resolve().parent.parent / 'data' / 'stock_names_full.json'


def from_mootdx():
    from mootdx.quotes import Quotes
    client = Quotes.factory(market='std')
    df = client.stocks()
    names = {}
    for _, r in df.iterrows():
        code = str(r.get('code', '')).zfill(6)
        nm = str(r.get('name', '')).strip()
        if len(code) == 6 and nm and code.isdigit():
            names[code] = nm
    return names


def from_pytdx_deep():
    from pytdx.hq import TdxHq_API
    api = TdxHq_API(heartbeat=False)
    names = {}
    for host, port in [("115.238.56.198", 7709), ("115.238.90.165", 7709)]:
        if api.connect(host, port, time_out=8):
            break
    start = 0
    while True:
        lst = api.get_security_list(0, start)
        if not lst:
            break
        for item in lst:
            code = str(item['code']).zfill(6)
            names[code] = item.get('name', '')
        start += len(lst)
        if len(lst) < 1000:
            break
    api.disconnect()
    return names


def main():
    print('mootdx...', flush=True)
    m = from_mootdx()
    print(f'mootdx {len(m)} 只', flush=True)
    print('pytdx深市...', flush=True)
    p = from_pytdx_deep()
    print(f'pytdx {len(p)} 只', flush=True)
    m.update(p)
    OUT.write_text(json.dumps(m, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    print(f'合并名称表: {len(m)} 只 → {OUT}')
    for probe in ('601212', '601388', '603132', '000426', '600000', '300750', '002156', '002580'):
        print(f'  {probe}: {m.get(probe)}')


if __name__ == '__main__':
    main()