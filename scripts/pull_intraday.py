"""盘中拉取当日分钟（TDX 1m 实时接口）→ 输出 JSON 供决策重建
用法: python scripts/pull_intraday.py --symbols 601212,601388,603132,000426,301003,301205,002017,002313
输出: outputs/intraday/{sym}_{date}.json
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from pytdx.hq import TdxHq_API
SERVERS = [('117.34.114.13',7709),('117.34.114.14',7709),('117.34.114.15',7709),('117.34.114.16',7709),
 ('117.34.114.17',7709),('117.34.114.18',7709),('117.34.114.20',7709),('117.34.114.27',7709),
 ('115.238.56.198',7709),('115.238.90.165',7709)]
OUT = pathlib.Path(__file__).resolve().parent.parent / 'outputs' / 'intraday'

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
    ap.add_argument('--symbols', default='601212,601388,603132,000426')
    args = ap.parse_args()
    syms = [s.strip().zfill(6) for s in args.symbols.split(',') if s.strip()]
    api = TdxHq_API(heartbeat=False)
    ok = False
    for host, port in SERVERS:
        if api.connect(host, port, time_out=8):
            ok = True
            break
    if not ok:
        print('连接失败'); return
    OUT.mkdir(parents=True, exist_ok=True)
    for sym in syms:
        try:
            bars = api.get_security_bars(0, market_of(sym), sym, 0, 800)
        except Exception:
            print(f'  {sym}: ERR'); continue
        if not bars:
            print(f'  {sym}: 无数据'); continue
        rows = []
        day = ''
        for b in bars:
            ts = str(b['datetime'])
            day = ts[:10]
            rows.append([ts, float(b['open']), float(b['high']), float(b['low']),
                         float(b['close']), float(b['vol']), float(b['amount'])])
        fp = OUT / f'{sym}_{day}.json'
        fp.write_text(json.dumps(rows), encoding='utf-8')
        print(f'  {sym}: {len(rows)} 根 {rows[0][0]} ~ {rows[-1][0]} 收{rows[-1][4]:.2f} → {fp.name}', flush=True)
    api.disconnect()

if __name__ == '__main__':
    main()
