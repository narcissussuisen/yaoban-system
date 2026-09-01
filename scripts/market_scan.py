"""阶段零-2: 全市场异动扫描器（腾讯批量行情 qt.gtimg.cn）
每轮: 全市场 5600 只分批 URL → 解析现价/涨跌幅/量比/换手/成交额 → 异动排序
输出: outputs/intraday/market_scan_{date}_{HHMM}.json + 汇总打印
用法: python scripts/market_scan.py
"""
from __future__ import annotations
import json
import pathlib
import re
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data.qfq_store import QFQStore  # noqa: E402
from core.combo_sell import industry_of  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / 'outputs' / 'intraday'
BATCH = 400  # 每 URL 标的数


def load_universe() -> list[str]:
    st = QFQStore('2026')
    syms = [s for s in st.symbols() if not s.startswith(('399', '5', '15', '16', '899', '688', '689', '4', '8', '92'))]
    st.close()
    return syms


def tencent_symbol(sym: str) -> str:
    return ('sh' if sym[0] in ('6', '9', '5') else 'sz') + sym


def fetch_batch(codes: list[str]) -> dict:
    q = ','.join(codes)
    url = f'https://qt.gtimg.cn/q={q}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=20).read().decode('gbk', errors='ignore')
    out = {}
    for line in raw.strip().split(';'):
        m = re.match(r'v_(\w+)="(.*)"', line.strip())
        if not m:
            continue
        parts = m.group(2).split('~')
        if len(parts) < 40 or not parts[3]:
            continue
        try:
            price = float(parts[3])
            prev = float(parts[4]) if parts[4] else None
            chg = (price / prev - 1) * 100 if prev and prev > 0 else None
            vol = float(parts[6]) if parts[6] else 0  # 手
            amount = float(parts[37]) if len(parts) > 37 and parts[37] else 0  # 万元
            turnover = float(parts[38]) if len(parts) > 38 and parts[38] else None  # 换手%
            pe = float(parts[39]) if len(parts) > 39 and parts[39] else None
            code = m.group(1)[2:]
            out[code] = {'px': price, 'chg': chg, 'vol': vol, 'amt': amount,
                         'turn': turnover, 'name': parts[1]}
        except (ValueError, IndexError):
            continue
    return out


def main():
    syms = load_universe()
    t0 = time.time()
    allq = {}
    codes = [tencent_symbol(s) for s in syms]
    for i in range(0, len(codes), BATCH):
        chunk = codes[i:i + BATCH]
        try:
            allq.update(fetch_batch(chunk))
        except Exception as e:
            print(f'batch {i} fail: {e}')
        if (i // BATCH) % 3 == 2:
            time.sleep(0.3)
    dt = time.time() - t0
    rows = []
    for sym, v in allq.items():
        if v['chg'] is None or sym.startswith('900'):
            continue
        rows.append({'sym': sym, 'name': v['name'], 'chg': v['chg'], 'turn': v['turn'],
                     'amt': v['amt'], 'vol': v['vol'], 'l2': industry_of(sym, '2026-08-28') or ''})
    rows.sort(key=lambda x: -(x['chg'] or -99))
    from datetime import datetime
    now = datetime.now()
    fp = OUT / f'market_scan_{now.strftime("%Y%m%d_%H%M")}.json'
    fp.write_text(json.dumps({'time': now.strftime('%Y-%m-%d %H:%M:%S'), 'n': len(rows),
                              'rows': rows[:300]}, ensure_ascii=False), encoding='utf-8')
    print(f'全市场扫描 {len(rows)} 只 耗时 {dt:.1f}s')
    print('涨幅 Top10:')
    for r in rows[:10]:
        print(f"  {r['sym']} {r['name']}: {r['chg']:+.2f}% 换手{r['turn']}% 额{r['amt']/10000:.1f}亿")
    print('跌幅 Top5:')
    for r in rows[-5:]:
        print(f"  {r['sym']} {r['name']}: {r['chg']:+.2f}%")


if __name__ == '__main__':
    main()
