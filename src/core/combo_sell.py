"""R3' 第二轮：组合级卖出信号（龙头联动 / 板块退潮先行）

数据：F盘分钟线（合成板块分时）+ data/sw_industry_history.csv（申万行业历史归属）
      + config/concepts.json（人工维护题材概念映射——选手口径的『板块』=题材主线）

证据：
  - 龙头冲高回落联动: v25@01:07 豫能控股早盘冲高被砸→辽宁能源先出局（6/5）
  - 龙头炸板联动: 8/26 百花医药「龙头炸板，有赚就走」（v16 见顶信号#8 龙头跳水→跟风补跌）
  - 板块退潮先行: 7/10「风格强分歧日，主力从老主线撤」→ 华微反复炸板止盈

接口（信号层，可离线回放验证；组合级回测由调用方组装 combo 上下文）:
  industry_of(symbol, date) -> l2_code | None
  concept_of(symbol, date) -> concept_name | None
  group_of(qfq, symbol, date) -> (kind, name, members)
  lianban_of(qfq, symbol, date) -> int  （截至 date 前收盘的连续涨停天数）
  dragon_leader(qfq, members, date) -> (symbol, lianban, first_touch, amount)
  dragon_signal(qfq, symbol, date) -> {'kind': 'zhaban'|'surge_fall', 'time', ...} | None
  sector_index_minute(qfq, members, date) -> (df[ts,close,vwap], prev_close|None)
  sector_retreat(qfq, members, date) -> {'time', 'decline_pct'} | None
"""
from __future__ import annotations

import json
import pathlib

import pandas as pd

from core.sell import limit_pct_of

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_IND_CSV = ROOT / 'data' / 'sw_industry_history.csv'
_CONCEPT_JSON = ROOT / 'config' / 'concepts.json'

_ind_map: dict | None = None
_daily_cache: dict = {}  # symbol -> [(symbol,date,open,high,low,close,volume,amount)] 预载缓存


def preload_daily(dmap: dict) -> None:
    """预载日线映射（组合级批量调用场景：避免逐 symbol 全年级分钟聚合）"""
    _daily_cache.update(dmap)


def _daily_rows(qfq, symbol: str) -> list:
    if symbol in _daily_cache:
        return _daily_cache[symbol]
    rows = qfq.get_stock(symbol)
    if rows:
        _daily_cache[symbol] = rows
    return rows


def _load_ind() -> dict:
    global _ind_map
    if _ind_map is None:
        df = pd.read_csv(_IND_CSV, usecols=['code', 'start_date', 'l2_code'])
        df['code'] = df['code'].astype(str).str.zfill(6)
        df['start_date'] = df['start_date'].str[:10]
        df['l2_code'] = df['l2_code'].astype(str)
        df = df.sort_values(['code', 'start_date'])
        _ind_map = {}
        for code, g in df.groupby('code'):
            _ind_map[code] = [(str(s), str(c)) for s, c in zip(g['start_date'], g['l2_code'])]
    return _ind_map


def industry_of(symbol: str, date: str) -> str | None:
    """symbol 在 date 所属申万 L2 行业代码"""
    m = _load_ind().get(symbol)
    if not m:
        return None
    out = None
    for s, c in m:
        if s <= date:
            out = c
        else:
            break
    return out


_concepts_cache: dict | None = None


def _concepts() -> dict:
    global _concepts_cache
    if _concepts_cache is not None:
        return _concepts_cache
    try:
        _concepts_cache = json.loads(_CONCEPT_JSON.read_text(encoding='utf-8'))
    except Exception:
        _concepts_cache = {}
    return _concepts_cache


def concept_of(symbol: str, date: str) -> str | None:
    """symbol 在 date 所属题材主线（人工维护）"""
    for name, spec in _concepts().items():
        if not isinstance(spec, dict):
            continue
        if symbol in spec.get('members', []):
            s = spec.get('start', '1990-01-01')
            e = spec.get('end', '2099-12-31')
            if s <= date <= e:
                return name
    return None


_univ_cache: set | None = None


def group_of(qfq, symbol: str, date: str) -> tuple:
    """(kind, name, members)：题材概念优先（选手口径），否则申万 L2 兜底"""
    global _univ_cache
    if _univ_cache is None:
        _univ_cache = {s for s in qfq.symbols() if not s.startswith(('399', '5', '15', '16'))}
    univ = _univ_cache
    c = concept_of(symbol, date)
    if c:
        spec = _concepts()[c]
        members = [m for m in (spec.get('members', []) if isinstance(spec, dict) else []) if m in univ]
        return ('concept', c, members)
    l2 = industry_of(symbol, date)
    members = [m for m in univ if industry_of(m, date) == l2]
    return ('sw_l2', l2, members)


def lianban_of(qfq, symbol: str, date: str) -> int:
    """截至 date 前收盘的连续涨停天数（封板判定：收盘 ≥ 涨停价-0.01）"""
    rows = _daily_rows(qfq, symbol)
    dates = [r[1] for r in rows]
    if date not in dates:
        return 0
    i = dates.index(date)
    n, pct = 0, limit_pct_of(symbol)
    j = i - 1
    while j >= 1:
        prev_close = float(rows[j - 1][5])
        close = float(rows[j][5])
        if close >= round(prev_close * (1 + pct), 2) - 0.01:
            n += 1
            j -= 1
        else:
            break
    return n


def day_bars(qfq, symbol: str, date: str) -> pd.DataFrame | None:
    rows = qfq.get_minute(symbol, start=date, end=date)
    if not rows:
        return None
    return pd.DataFrame(rows, columns=['symbol', 'freq', 'ts', 'open', 'high', 'low',
                                       'close', 'volume', 'amount'])


def prev_close_of(qfq, symbol: str, date: str) -> float | None:
    rows = _daily_rows(qfq, symbol)
    dates = [r[1] for r in rows]
    if date not in dates:
        return None
    i = dates.index(date)
    if i == 0:
        return None
    return float(rows[i - 1][5])


def _hm_minutes(hm: str) -> int:
    h, m = hm.split(':')
    return int(h) * 60 + int(m) - 9 * 60 - 30


def dragon_leader(qfq, members: list, date: str, as_of_hm: str | None = None,
                   top_k: int = 15) -> tuple | None:
    """板块龙头识别：最高连板(截至昨收) → 当日最早触板 → 当日成交额最大。
    as_of_hm: 增量口径时刻（'HH:MM'）——触板/成交额只用 ≤ 该时刻的数据（接线期整改：
    实时系统须传信号时刻；None=全日口径，离线回放用）。
    top_k: 按前日成交额预选头部成员（龙头=放量股先验；性能优化——避免全组分钟加载）"""
    if top_k and len(members) > top_k:
        prev_amts = {}
        for s in members:
            rows = _daily_rows(qfq, s)
            dates = [r[1] for r in rows]
            if date in dates:
                i = dates.index(date)
                if i > 0:
                    prev_amts[s] = float(rows[i - 1][7])
        if prev_amts:
            members = sorted(prev_amts, key=prev_amts.get, reverse=True)[:top_k]
    best = None
    for s in members:
        lb = lianban_of(qfq, s, date)
        day = day_bars(qfq, s, date)
        if day is None:
            continue
        if as_of_hm is not None:
            day = day[day['ts'].str[11:16] <= as_of_hm]
            if day.empty:
                continue
        pc = prev_close_of(qfq, s, date)
        limit = round(pc * (1 + limit_pct_of(s)), 2) if pc else None
        first_touch = None
        if limit:
            t = day[day['high'] >= limit - 0.01]
            if len(t):
                first_touch = str(t['ts'].iloc[0])[11:16]
        amt = float(day['amount'].sum())
        key = (lb, 1 if first_touch else 0,
               -_hm_minutes(first_touch) if first_touch else -10**9, amt)
        if best is None or key > best[0]:
            best = (key, s, lb, first_touch, amt)
    if best is None:
        return None
    return (best[1], best[2], best[3], best[4])


def dragon_signal(qfq, symbol: str, date: str, surge_pct: float = 5.0,
                  pull_pct: float = 3.0, zhaban_fall_pct: float = 0.5,
                  arm_time: str = '14:00') -> dict | None:
    """龙头当日离场信号（早盘口径）:
    zhaban:      触板(高≥涨停价-0.01)后回落 > zhaban_fall_pct% → 炸板
    surge_fall:  盘中涨幅 ≥ surge_pct%（vs 昨收）后自高点回落 ≥ pull_pct%（豫能6/5型冲高被砸）
    返回 {'kind', 'time', 'peak_pct', ...} 或 None"""
    day = day_bars(qfq, symbol, date)
    pc = prev_close_of(qfq, symbol, date)
    if day is None or not pc:
        return None
    limit = round(pc * (1 + limit_pct_of(symbol)), 2)
    arm = day[day['ts'].str[11:16] <= arm_time]
    if arm.empty:
        return None
    high = arm['high'].to_numpy()
    low = arm['low'].to_numpy()
    close = arm['close'].to_numpy()
    ts = arm['ts'].astype(str).to_numpy()
    # 自股分时均价线（累计成交额/累计成交量）
    import numpy as np
    cum_amt = arm['amount'].cumsum().to_numpy()
    cum_vol = arm['volume'].cumsum().to_numpy()
    vwap = cum_amt / np.maximum(cum_vol, 1)
    touched = False
    peak = 0.0
    for i in range(len(arm)):
        if high[i] >= limit - 0.01:
            touched = True
        if high[i] > peak:
            peak = high[i]
        if touched and close[i] < limit * (1 - zhaban_fall_pct / 100.0):
            return {'kind': 'zhaban', 'time': ts[i][11:16],
                    'peak_pct': (peak / pc - 1) * 100,
                    'fall_from_limit_pct': (limit - close[i]) / limit * 100}
        # 冲高回落：跳过前 2 根（开盘噪声）且须跌破自股均价线 0.5%（封板日回落不破线）
        if (i >= 2 and peak / pc - 1 >= surge_pct / 100.0
                and low[i] < peak * (1 - pull_pct / 100.0)
                and close[i] < vwap[i] * (1 - 0.005)):
            return {'kind': 'surge_fall', 'time': ts[i][11:16],
                    'peak_pct': (peak / pc - 1) * 100,
                    'fall_from_peak_pct': (peak - low[i]) / peak * 100}
    return None


def sector_index_minute(qfq, members: list, date: str, top_n: int = 40) -> tuple | None:
    """成分分时合成板块指数（成交额加权的相对昨收涨跌幅），返回 (df[ts,pct,vwap_pct], baseline)。
    pct=0 即板块指数昨收基准线；vwap_pct=累计成交额加权均值（分时均价线）。
    注：不用成交额/成交量合成价格指数——低价股成交量会淹没高价龙头（6/5 实证 7.74 vs 应≈19.9）。"""
    frames, amts = [], {}
    prev_amts = {}
    for s in members:
        day = day_bars(qfq, s, date)
        if day is None:
            continue
        pc = prev_close_of(qfq, s, date)
        if not pc:
            continue
        day = day.copy()
        day['pct'] = day['close'] / pc - 1
        frames.append(day)
        amts[s] = float(day['amount'].sum())
        # 前日成交额（top_n 成员选择用，完全无前视——接线期整改）
        rows_prev = _daily_rows(qfq, s)
        dates = [r[1] for r in rows_prev]
        if date in dates:
            i = dates.index(date)
            if i > 0:
                prev_amts[s] = float(rows_prev[i - 1][7])
    if not frames:
        return None
    if top_n and len(frames) > top_n:
        if prev_amts:
            top = sorted(prev_amts, key=prev_amts.get, reverse=True)[:top_n]
        else:
            top = sorted(amts, key=amts.get, reverse=True)[:top_n]
        keep = set(top)
        frames = [f for f in frames if f['symbol'].iloc[0] in keep]
    allf = pd.concat(frames, ignore_index=True)
    allf['apct'] = allf['pct'] * allf['amount']
    g = allf.groupby('ts').agg(amt=('amount', 'sum'), apct=('apct', 'sum'))
    pct = (g['apct'] / g['amt'].replace(0, pd.NA)).ffill()
    vwap_pct = (g['apct'].cumsum() / g['amt'].cumsum().replace(0, pd.NA)).ffill()
    out = pd.DataFrame({'ts': g.index.to_numpy(),
                        'pct': pct.to_numpy(), 'vwap_pct': vwap_pct.to_numpy()})
    return out.reset_index(drop=True), 0.0


def sector_retreat(qfq, members: list, date: str, decline_pct: float = 0.5,
                   tol: float = 0.003, confirm: int = 3, top_n: int = 15) -> dict | None:
    """板块退潮：板块指数分时跌破均价线（连续 confirm 根）且较昨收跌幅 ≥ decline_pct%
    → 返回 {'time': 首根确认时刻, 'decline_pct': 较昨收跌幅%} 或 None"""
    r = sector_index_minute(qfq, members, date, top_n=top_n)
    if r is None:
        return None
    idx, _ = r
    c = idx['pct'].to_numpy()
    v = idx['vwap_pct'].to_numpy()
    cnt = 0
    for i in range(len(idx)):
        if c[i] < v[i] - tol and c[i] < -decline_pct / 100.0:
            cnt += 1
            if cnt >= confirm:
                return {'time': str(idx['ts'].iloc[i - confirm + 1])[11:16],
                        'decline_pct': c[i] * 100}
        else:
            cnt = 0
    return None