"""隔夜全球市场复盘（盘前准备①）：拉取外盘指数/商品 → outputs/global_snapshot.json
腾讯行情接口。8:50 定时运行，plan_daily 注入外围映射。
用法: python scripts/pull_global.py
"""
from __future__ import annotations
import json
import pathlib
import re
import urllib.request

OUT = pathlib.Path(__file__).resolve().parent.parent / 'outputs' / 'global_snapshot.json'
CODES = {
    'usDJI': '道琼斯', 'usIXIC': '纳斯达克', 'usINX': '标普500',
    'hf_CL': 'WTI原油', 'hf_GC': 'COMEX黄金', 'hf_SI': 'COMEX白银',
}


def main():
    q = ','.join(CODES.keys())
    url = f'https://qt.gtimg.cn/q={q}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        raw = urllib.request.urlopen(req, timeout=15).read().decode('gbk', errors='ignore')
    except Exception as e:
        print(f'拉取失败: {e}')
        return
    out = {}
    for line in raw.strip().split(';'):
        m = re.match(r'v_(\w+)="(.*)"', line.strip())
        if not m:
            continue
        parts = m.group(2).split('~')
        if len(parts) < 5 or not parts[3]:
            continue
        code = m.group(1)
        try:
            price = float(parts[3])
            prev = float(parts[4]) if parts[4] else None
            chg = (price / prev - 1) * 100 if prev and prev > 0 else None
            out[code] = {'name': CODES.get(code, code), 'price': round(price, 2),
                         'chg_pct': round(chg, 2) if chg is not None else None}
        except (ValueError, IndexError):
            continue
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print('全球快照:')
    for c, v in out.items():
        s = f"{v['chg_pct']:+.2f}%" if v['chg_pct'] is not None else '—'
        print(f"  {v['name']}: {v['price']} ({s})")


if __name__ == '__main__':
    main()
