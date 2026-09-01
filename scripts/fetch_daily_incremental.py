"""P0-5(2026-08-31) 增量更新: 腾讯前复权日线合并追加到 F:/WorkBuddyItem/a股level2/daily/

背景: 分钟线(parquet_qfq_2026)停留在 8/21, daily/ 停留在 8/28; r5p/r6p/plan_daily 经
QFQStore 聚合日线。本脚本拉取 8/28 以来腾讯 qfqday, 与 daily/ 已有数据合并。

复权处理(关键): 除权股(如 300489 因子约10)的腾讯 qfqday 在基准更新前返回不复权价,
会与 daily/ 旧 qfq 序列跳变。对策: 用 8/28 双口径锚点计算因子 ratio = 新8/28 / 旧8/28,
新行(>8/28)的 o/h/l/c 全部 /ratio 后合并; ratio 缺失或异常(<=0 或 >5 或 <0.2)则跳过该股,
保留旧数据(等复权因子稳定后再补)。
amount 腾讯接口缺失, 用 volume*close 近似(标注 ADJ_AMT_EST)。

用法: python scripts/fetch_daily_incremental.py [--sleep 0.3] [--limit 0]
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys
import time
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.qfq_store import QFQStore  # noqa: E402

OUT = pathlib.Path(r'F:/WorkBuddyItem/a股level2/daily')
REBUILT = pathlib.Path(r'F:/WorkBuddyItem/a股level2/daily_rebuilt')
ANCHOR = '2026-08-28'  # 复权因子锚定日(旧数据最后覆盖日)


def fetch_bs(sym: str):
    """baostock 前复权日线(adjustflag=2)。volume 股→手(/100, 与 daily/ 旧数据单位一致); amount 真实元。
    8/28 后除权股(如 300489)的 qfq 基准未及时更新, 返回未调权价 —— 由 main 的锚定 ratio 统一调权。"""
    import baostock as bs
    code = ('sh.' if sym[0] in ('6', '9', '5') else 'sz.') + sym
    rs = bs.query_history_k_data_plus(code, 'date,open,high,low,close,volume,amount',
                                      start_date='2026-08-27', end_date='2026-08-31',
                                      frequency='d', adjustflag='2')
    if rs.error_code != '0':
        raise RuntimeError(f'baostock {code}: {rs.error_msg}')
    out = []
    while rs.next():
        r = rs.get_row_data()
        try:
            out.append({'symbol': sym, 'date': r[0], 'open': float(r[1]), 'close': float(r[2]),
                        'high': float(r[3]), 'low': float(r[4]),
                        'volume': float(r[5]) / 100.0, 'amount': float(r[6])})
        except Exception:
            continue
    return out


def fetch_tx(sym: str):
    """腾讯 ifzq qfqday 回退源(可能被限流 501)。volume 单位=手; amount 缺失。"""
    prefix = 'sh' if sym[0] in ('6', '9') else 'sz'
    url = f'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{sym},day,2026-08-27,2026-12-31,640,qfq'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=15).read().decode('utf-8', errors='ignore')
    data = json.loads(raw)
    node = data.get('data', {}).get(f'{prefix}{sym}', {})
    rows = node.get('qfqday') or node.get('day') or []
    out = []
    for r in rows:
        # [date, open, close, high, low, volume(手)] — 腾讯 fqkline day 6 列
        out.append({'symbol': sym, 'date': str(r[0]), 'open': float(r[1]), 'close': float(r[2]),
                    'high': float(r[3]), 'low': float(r[4]),
                    'volume': float(r[5]) if len(r) > 5 else 0.0})
    return out


def fetch_rebuilt(sym: str):
    """本地 daily_rebuilt(TDX 60m 聚合, rebuild 任务每日维护, 含全市场当日实盘OHLCV/amount)。
    volume 股→手(/100, daily/ 存储口径); amount 真实元。锚 8/28 双口径 ratio 由 main 统一调权转 qfq。"""
    fp = REBUILT / f'{sym}.parquet'
    if not fp.exists():
        raise FileNotFoundError(f'{sym} not in daily_rebuilt')
    d = pd.read_parquet(fp)
    d = d[d['date'] >= '2026-08-27']
    out = []
    for _, row in d.iterrows():
        out.append({'symbol': sym, 'date': str(row['date'])[:10], 'open': float(row['open']),
                    'high': float(row['high']), 'low': float(row['low']), 'close': float(row['close']),
                    'volume': float(row['volume']) / 100.0, 'amount': float(row['amount'])})
    return out


def fetch_daily(sym: str):
    """主源 daily_rebuilt(本地, 无网络限速; TDX 实盘口径, ratio 调权转 qfq); 回退腾讯 ifzq。
    注: baostock qfq 为发行价基准, 与 daily/ 基准不兼容, 不可作写入源。"""
    try:
        return fetch_rebuilt(sym)
    except Exception:
        return fetch_tx(sym)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sleep', type=float, default=0.3)
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()
    st = QFQStore('2026')
    syms = [s for s in st.symbols() if not s.startswith(('399', '899', '5', '15', '16'))]
    st.close()
    if args.limit:
        syms = syms[:args.limit]
    OUT.mkdir(parents=True, exist_ok=True)
    import baostock as bs
    lg = bs.login()
    if lg.error_code != '0':
        raise RuntimeError(f'baostock login failed: {lg.error_msg}')
    t0 = time.time()
    ok = skip = fail = adj = 0
    for i, sym in enumerate(syms):
        fp = OUT / f'{sym}.parquet'
        rows = None
        for attempt in (1, 2):
            try:
                rows = fetch_daily(sym)
                break
            except Exception:
                rows = None
                time.sleep(1.0)
        if rows is None:
            fail += 1
            continue
        if not rows:
            skip += 1
            continue
        try:
            new = pd.DataFrame(rows).drop_duplicates('date', keep='last')
            new = new[new['date'] > '2026-08-21']  # 只关心缺口窗口
            if fp.exists():
                old = pd.read_parquet(fp)
            else:
                old = pd.DataFrame(columns=['symbol', 'date', 'open', 'high', 'low', 'close', 'volume', 'amount'])
            old_rows = old[old['date'] <= '2026-08-28']
            new_rows = new[new['date'] > '2026-08-28']
            if new_rows.empty:
                skip += 1
                continue
            # 复权因子: 锚定日双口径
            ratio = None
            try:
                n_anchor = float(new.loc[new['date'] == ANCHOR, 'close'].iloc[0])
                o_anchor = float(old.loc[old['date'] == ANCHOR, 'close'].iloc[0])
                if o_anchor > 0 and n_anchor > 0:
                    ratio = n_anchor / o_anchor
            except Exception:
                ratio = None
            if ratio is None or ratio <= 0 or ratio > 5 or ratio < 0.2:
                skip += 1  # 因子不可靠, 保留旧数据等待稳定
                continue
            if abs(ratio - 1.0) > 1e-6:
                for c in ('open', 'high', 'low', 'close'):
                    new_rows[c] = new_rows[c] / ratio
                adj += 1
            new_rows = new_rows.copy()
            # volume 统一为手(daily/ 存储口径, _agg_daily 侧 ×100 转股); amount 缺失(腾讯回退)时近似
            if 'amount' not in new_rows.columns:
                new_rows['amount'] = new_rows['volume'] * new_rows['close'] * 100.0  # ADJ_AMT_EST(元)
            merged = pd.concat([old_rows, new_rows], ignore_index=True)
            merged = merged.drop_duplicates('date', keep='last').sort_values('date').reset_index(drop=True)
            merged.to_parquet(fp, index=False)
            ok += 1
        except Exception:
            fail += 1
            continue
        if (i + 1) % 500 == 0:
            print(f'  {i+1}/{len(syms)} ok={ok} skip={skip} fail={fail} adj={adj} {time.time()-t0:.0f}s', flush=True)
        time.sleep(args.sleep)
    try:
        bs.logout()
    except Exception:
        pass
    print(f'完成: ok={ok} skip={skip} fail={fail} adj={adj} 耗时 {time.time()-t0:.0f}s', flush=True)


if __name__ == '__main__':
    main()
