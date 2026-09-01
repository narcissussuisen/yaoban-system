"""全市场日线重建（TDX 60m K线, 可靠源）→ 补 8/22-8/28 缺口
60m: 800根 ≈ 200 交易日; 每根含 open/high/low/close/vol/amount
按日聚合: open=首根open, close=末根close, high=max, low=min, vol=sum
输出: F:/WorkBuddyItem/a股level2/daily_rebuilt/{sym}.parquet（daily_src 优先读取）
用法: python scripts/fetch_daily_minute_rebuild.py
"""
from __future__ import annotations
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from pytdx.hq import TdxHq_API  # noqa: E402

from data.qfq_store import QFQStore  # noqa: E402

OUT = pathlib.Path(r'F:/WorkBuddyItem/a股level2/daily_rebuilt')
SERVERS = [('115.238.56.198', 7709), ('115.238.90.165', 7709)]


def market_of(sym: str) -> int:
    if sym.startswith('900'):
        return 1
    if sym[0] in ('4', '8') or sym.startswith('92'):
        return 2
    if sym[0] in ('6', '9', '5'):
        return 1
    return 0


def latest_market_date(api) -> str | None:
    """探测市场最新交易日（用活跃股 600000 的 60m 末根）"""
    try:
        bars = api.get_security_bars(4, 1, '600000', 0, 1)
        if bars:
            return str(bars[-1]['datetime'])[:10]
    except Exception:
        pass
    return None


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    st = QFQStore('2026')
    syms = [s for s in st.symbols() if not s.startswith(('399', '899', '5', '15', '16'))]
    st.close()
    api = TdxHq_API(heartbeat=False)
    ok = False
    for host, port in SERVERS:
        if api.connect(host, port, time_out=8):
            ok = True
            break
    if not ok:
        print('ERROR: TDX连接失败', file=sys.stderr); return 2
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    latest = latest_market_date(api)
    print(f'市场最新交易日: {latest}', flush=True)
    if not latest:
        print('ERROR: TDX 60m无最新交易日', file=sys.stderr); api.disconnect(); return 3
    if args.dry_run:
        probe = api.get_security_bars(4, 1, '600000', 0, 8)
        api.disconnect()
        print(f'DRY-RUN: latest={latest} probe_bars={len(probe) if probe else 0}')
        return 0 if probe else 4
    n_ok = n_skip = 0
    for i, sym in enumerate(syms):
        fp = OUT / f'{sym}.parquet'
        if fp.exists() and latest:
            try:
                old = pd.read_parquet(fp, columns=['date'])
                if str(old['date'].iloc[-1]) >= latest:
                    n_skip += 1
                    continue
            except Exception:
                pass
        try:
            bars = api.get_security_bars(4, market_of(sym), sym, 0, 800)  # 4=60分钟
        except Exception:
            bars = None
        if not bars:
            continue
        rows = []
        for b in bars:
            ts = str(b['datetime'])
            rows.append([ts, float(b['open']), float(b['high']), float(b['low']),
                         float(b['close']), float(b['vol']), float(b['amount'])])
        df = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close', 'volume', 'amount'])
        df['date'] = df['ts'].str[:10]
        day = df.groupby('date').agg(open=('open', 'first'), high=('high', 'max'),
                                     low=('low', 'min'), close=('close', 'last'),
                                     volume=('volume', 'sum'), amount=('amount', 'sum')).reset_index()
        day['symbol'] = sym
        day = day[['symbol', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount']]
        # P0-5加固(2026-09-01): 防数据回退——TDX 返回滞后(如服务器数据未就绪), 新聚合 max 可能小于旧文件 max;
        # 若倒退则不覆盖(保留旧数据), 避免次日 preflight 账本检查因数据缺失/回退失败
        try:
            _new_max = str(day['date'].max())
            if fp.exists():
                _old_max = str(pd.read_parquet(fp, columns=['date'])['date'].iloc[-1])
                if _new_max < _old_max:
                    n_skip += 1
                    continue
        except Exception:
            pass
        tmp = fp.with_name(fp.name + f'.{os.getpid()}.tmp')
        try:
            day.to_parquet(tmp, index=False)
            os.replace(tmp, fp)
        finally:
            tmp.unlink(missing_ok=True)
        n_ok += 1
        if (i + 1) % 500 == 0:
            print(f'  {i+1}/{len(syms)} ok={n_ok} {time.time()-t0:.0f}s', flush=True)
        time.sleep(0.15)
    api.disconnect()
    print(f'完成: ok={n_ok} skip={n_skip} 耗时 {time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
