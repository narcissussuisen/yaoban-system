"""统一日线读取层（权威源: 腾讯前复权 > F盘 qfq > TDX parquet[已证不可信, 仅兜底]）
用法: from core.daily_src import load_daily, prev_close_of
"""
from __future__ import annotations
import pathlib

import pandas as pd

TENCENT = pathlib.Path(r'F:/WorkBuddyItem/a股level2/daily_tencent')
REBUILT = pathlib.Path(r'F:/WorkBuddyItem/a股level2/daily_rebuilt')
FROOT = pathlib.Path(r'F:/WorkBuddyItem/a股分钟线/parquet_qfq_2026')
TDX = pathlib.Path(r'F:/WorkBuddyItem/a股level2/daily')
_cache: dict = {}


def load_daily(sym: str) -> pd.DataFrame | None:
    """返回升序日线 df[date,open,high,low,close,volume,amount]; 腾讯库优先"""
    if sym in _cache:
        return _cache[sym]
    df = None
    # 60m重建日线优先（TDX可靠源, 覆盖8/22+缺口）
    fp = REBUILT / f'{sym}.parquet'
    if fp.exists():
        try:
            df = pd.read_parquet(fp)
            df['date'] = df['date'].astype(str)
        except Exception:
            df = None
    if df is None:
        fp = TENCENT / f'{sym}.parquet'
        if fp.exists():
            try:
                df = pd.read_parquet(fp)
                df['date'] = df['date'].astype(str)
            except Exception:
                df = None
    if df is None:
        from data.qfq_minute import market_suffix
        fp = FROOT / f'{sym}.{market_suffix(sym)}.parquet'
        if fp.exists():
            try:
                t = pd.read_parquet(fp)
                dt = t['datetime'].astype(str)
                day = t.groupby(dt.str[:8]).agg(open=('open', 'first'), high=('high', 'max'),
                                                low=('low', 'min'), close=('close', 'last'),
                                                volume=('volume', 'sum'), amount=('amount', 'sum'))
                day = day.reset_index().rename(columns={'index': 'date'})
                day['date'] = day['date'].str[:4] + '-' + day['date'].str[4:6] + '-' + day['date'].str[6:8]
                df = day
            except Exception:
                df = None
    if df is None:
        fp = TDX / f'{sym}.parquet'
        if fp.exists():
            try:
                df = pd.read_parquet(fp)
                df['date'] = df['date'].astype(str)
            except Exception:
                df = None
    if df is not None and len(df):
        df = df.sort_values('date').reset_index(drop=True)
        _cache[sym] = df
    return df


def prev_close_of(sym: str, day: str):
    df = load_daily(sym)
    if df is not None and len(df):
        sub = df[df['date'] < day]
        if len(sub):
            return float(sub['close'].iloc[-1])
    # 分钟兜底：TDX 1m 含多日（800根≈3.3天），取前一交易日最后一根收盘（分钟数据已验证可靠）
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API(heartbeat=False)
        for host, port in [('115.238.56.198', 7709), ('123.125.108.14', 7709)]:
            if api.connect(host, port, time_out=8):
                break
        else:
            return None
        from data.qfq_minute import market_suffix as _ms
        m = 1 if sym.startswith(('6', '9', '5')) else (2 if sym[0] in ('4', '8') or sym.startswith('92') else 0)
        bars = api.get_security_bars(0, m, sym, 0, 800)
        api.disconnect()
        if not bars:
            return None
        seen = set()
        prev_close = None
        for b in bars:
            d = str(b['datetime'])[:10]
            if d < day and d not in seen:
                seen.add(d)
                prev_close = float(b['close'])
        return prev_close
    except Exception:
        return None
