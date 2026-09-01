"""操盘看板数据装配：ledger.json + 行情 → outputs/board/board.json + index.html

做T点位分布: 取当日 T 成交标的的 1m 分钟 + 均价线 + T 进/出标记
基准: index_daily 中 000300（沪深300）优先，退而求其次 sh000001

用法: python scripts/build_board.py [--date 2026-08-28] [--fills-day 自动=最新成交日]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from portfolio.ledger import load as load_ledger, LEDGER  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
BOARD = BASE / 'outputs' / 'board'
TPL = BASE / 'templates' / 'board_template.html'
DB = BASE / 'data' / 'yaoban.db'

REASON_CN = {
    'entry': '建仓', 'pullback': '回踩确认', 'dibu_lowopen': '低开高走',
    'dibu_pullback': '下杀回拉', 'dibu_shrink': '缩量企稳', 'B1': '开盘走强',
    'B2': '放量回踩', 't_buy': 'T进', 't_sell': 'T出', 't_sell_out': 'T出',
    't_buy_back': 'T回补', 't_buy_back_eod': '收盘回补', 'vwap_halve': '破均价线减半',
    'vwap_break_all': '再破均价线清仓', 'break_low': '冲高回落止损', 'stop_loss': '止损',
    'profit_take': '冲高止盈', 'zhaban_sell': '炸板卖出', 'second_high': '次高点卖出',
    'ten_oclock': '十点纪律', 'dragon_link': '龙头联动', 'sector_retreat': '板块退潮',
    'ma5_halve': '破5日线减半', 'ma10_clear': '破10日线清仓', 'time_stop': '时间止损',
    'window_end': '窗口了结',
}

_NAME_MAP = None


def stock_name(sym: str) -> str:
    global _NAME_MAP
    if _NAME_MAP is None:
        fp_n = BASE / 'data' / 'stock_names_full.json'
        if fp_n.exists():
            try:
                _NAME_MAP = json.loads(fp_n.read_text(encoding='utf-8'))
            except Exception:
                _NAME_MAP = {}
        else:
            _NAME_MAP = {}
    return _NAME_MAP.get(sym, sym)


def minute_of(sym: str, day: str):
    """1m 分钟线: F盘 parquet 优先, 否则 yaoban.db minute_kline"""
    import pyarrow.parquet as pq
    from data.qfq_minute import market_suffix
    fp = pathlib.Path('F:/WorkBuddyItem/a股分钟线/parquet_qfq_2026') / f'{sym}.{market_suffix(sym)}.parquet'
    if fp.exists():
        try:
            t = pq.read_table(fp, columns=['datetime', 'open', 'high', 'low', 'close',
                                           'volume', 'amount'])
            df = t.to_pandas()
            dt = df['datetime'].astype(str)
            df['ts'] = dt.str[:4] + '-' + dt.str[4:6] + '-' + dt.str[6:8] + ' ' + dt.str[9:14]
            day_df = df[df['ts'].str[:10] == day]
            if len(day_df) > 0:
                return [(str(r['ts']), float(r['close']), float(r['volume']),
                         float(r['amount'])) for _, r in day_df.iterrows()]
        except Exception:
            pass
    con = sqlite3.connect(DB)
    rows = con.execute('SELECT ts, close, volume, amount FROM minute_kline WHERE symbol=? AND freq=\'1m\' AND ts LIKE ? ORDER BY ts',
                       (sym, day + '%')).fetchall()
    con.close()
    return rows


def benchmark_curve():
    con = sqlite3.connect(DB)
    for sym in ('000300', 'sh000300', 'sh000001'):
        rows = con.execute('SELECT date, close FROM index_daily WHERE symbol=? ORDER BY date',
                           (sym,)).fetchall()
        if rows and len(rows) > 10:
            con.close()
            return sym, {r[0]: r[1] for r in rows}
    con.close()
    return None, {}


def _atomic_write(path: pathlib.Path, text: str) -> None:
    """同目录 PID 后缀临时文件 + flush/fsync + os.replace，避免读取端读到半写 JSON。"""
    tmp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with open(tmp, 'w', encoding='utf-8') as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fills-day', default='', help='做T图展示日（缺省=最新有成交的日期）')
    args = ap.parse_args()
    state = load_ledger()
    acct = state['account']
    curve = acct.get('equity_curve', [])
    fills = acct.get('fills', [])
    # 基准曲线（按净值曲线日期对齐）
    bsym, bidx = benchmark_curve()
    bench = []
    if bidx and curve:
        b0 = next((bidx[c['date']] for c in curve if c['date'] in bidx), None)
        if b0:
            for c in curve:
                b = bidx.get(c['date'])
                bench.append(round(b / b0 * 100000, 2) if b else None)
    # 持仓现价：统一日线层（腾讯前复权优先；TDX parquet 已证不可信仅兜底）
    from core.daily_src import load_daily
    mark = {}
    for sym in acct.get('positions', {}):
        df_d = load_daily(sym)
        last = None
        if df_d is not None and len(df_d):
            last = float(df_d['close'].iloc[-1])
        mark[sym] = last
    # 做T点位分布
    t_fill_days = sorted({f['date'] for f in fills if f['reason'].startswith('t_')})
    t_day = args.fills_day or (t_fill_days[-1] if t_fill_days else None)
    t_charts = []
    if t_day:
        for sym in sorted({f['sym'] for f in fills if f['date'] == t_day and f['reason'].startswith('t_')}):
            m = minute_of(sym, t_day)
            marks = [{'ts': f['ts'], 'side': f['side'], 'px': f['px'], 'qty': f['qty']}
                     for f in fills if f['date'] == t_day and f['sym'] == sym and f['reason'].startswith('t_')]
            cum_v, cum_a = 0.0, 0.0
            vwap = []
            for (ts, c, v, a) in m:
                cum_v += float(v); cum_a += float(a)
                vwap.append(round(cum_a / cum_v, 3) if cum_v > 0 else None)
            t_charts.append({'sym': sym, 'name': stock_name(sym), 'date': t_day,
                             'minute': [[ts, round(c, 3)] for (ts, c, v, a) in m],
                             'vwap': vwap, 'marks': marks,
                             't_buys': sum(1 for f in fills if f['date'] == t_day and f['sym'] == sym and f['side'] == 'buy' and f['reason'].startswith('t_')),
                             't_sells': sum(1 for f in fills if f['date'] == t_day and f['sym'] == sym and f['side'] == 'sell' and f['reason'].startswith('t_'))})
    # 持仓明细
    positions = []
    for sym, p in acct.get('positions', {}).items():
        last = mark.get(sym)
        mv = p['qty'] * last if last else 0
        pnl = (last - p['cost']) * p['qty'] if last else None
        positions.append({'sym': sym, 'name': stock_name(sym), 'qty': p['qty'],
                          'cost': round(p['cost'], 3),
                          'last': round(last, 3) if last else None, 'mv': round(mv, 2),
                          'pnl': round(pnl, 2) if pnl is not None else None,
                          'pnl_pct': round((last / p['cost'] - 1) * 100, 2) if last else None,
                          'stop_px': round(p['stop_px'], 3) if p.get('stop_px') else None,
                          'entry_ts': p.get('entry_ts'),
                          'days': p.get('days', 0)})
    # 当日操作
    latest_day = max((c['date'] for c in curve), default='')
    fills_today = [dict(f, name=stock_name(f['sym']),
                        reason_cn=REASON_CN.get(str(f.get('reason', '')), str(f.get('reason', ''))))
                   for f in fills if f['date'] == latest_day]
    total = curve[-1]['equity'] if curve else acct['cash']
    day_pnl = None
    if len(curve) >= 2 and curve[-1]['equity'] != 0:
        day_pnl = (curve[-1]['equity'] / curve[-2]['equity'] - 1) * 100
    # 交易员自省数据（阶段二: 日报 JSON 或内联统计）
    selfcheck = None
    fp_sc = BASE / 'outputs' / 'reviews' / f'trader_daily_{latest_day}.json'
    if fp_sc.exists():
        try:
            selfcheck = json.loads(fp_sc.read_text(encoding='utf-8'))
        except Exception:
            selfcheck = None
    if selfcheck is None:
        selfcheck = {'date': latest_day, 'day_pnl': day_pnl,
                     'fills': len(fills_today), 'trades_total': 0,
                     'win_rate': None, 't_rounds': 0, 'buy_kinds': {}, 'sell_reasons': {},
                     'audit': []}
    # 盈亏分析
    pnl_amt = sum(p['pnl'] for p in positions if p['pnl'] is not None)
    pnl_pct_tot = (total / state.get('start_cash', total) - 1) * 100 if state.get('start_cash') else None
    # 历史已实现盈亏（卖出配对成本，简化：卖出流水 vs 对应买入均价）
    realized = 0.0
    bought_q = {}
    bought_v = {}
    for f in fills:
        if f['side'] == 'buy':
            bought_q[f['sym']] = bought_q.get(f['sym'], 0) + f['qty']
            bought_v[f['sym']] = bought_v.get(f['sym'], 0.0) + f['qty'] * f['px']
        else:
            sym = f['sym']
            if bought_q.get(sym, 0) > 0:
                avg = bought_v[sym] / bought_q[sym]
                realized += (f['px'] - avg) * f['qty']
                bought_q[sym] -= f['qty']
                bought_v[sym] -= avg * f['qty']
    done = [f for f in fills if f['side'] == 'sell' and not f['reason'].startswith('t_')]
    pnl_detail = [{'sym': p['sym'], 'name': p['name'], 'pnl': p['pnl'] or 0.0,
                   'pnl_pct': p['pnl_pct'] or 0.0,
                   'share': round(p['pnl'] / pnl_amt * 100, 2) if pnl_amt and p['pnl'] is not None else None}
                  for p in positions]
    pnl_detail.sort(key=lambda x: -(x['pnl'] or 0))
    pnl_summary = (f'累计收益 {pnl_pct_tot:+.2f}% · 当前浮动盈亏 {pnl_amt:+.0f} 元 · '
                   f'已实现盈亏 {realized:+.0f} 元 · 已完成交易 {len(done)} 笔')
    # ---- 盘中实况数据（阶段三A: 最新扫描/板块热度/持仓实时）
    intraday_dir = BASE / 'outputs' / 'intraday'
    latest_scan = None
    latest_momentum = None
    pos_live = None
    if intraday_dir.exists():
        scans = sorted(intraday_dir.glob('market_scan_*.json'))
        if scans:
            try:
                latest_scan = json.loads(scans[-1].read_text(encoding='utf-8'))
            except Exception:
                latest_scan = None
        if (intraday_dir / 'board_momentum.json').exists():
            try:
                latest_momentum = json.loads((intraday_dir / 'board_momentum.json').read_text(encoding='utf-8'))
            except Exception:
                latest_momentum = None
        if (intraday_dir / 'pos_live.json').exists():
            try:
                pos_live = json.loads((intraday_dir / 'pos_live.json').read_text(encoding='utf-8'))
            except Exception:
                pos_live = None
    # ---- 进度区（阶段三C: vs 选手 6.4x 进度条）
    multiple = total / state.get('start_cash', total) if state.get('start_cash') else None
    player_mult = 6.4
    progress = round(multiple / player_mult * 100, 1) if multiple else None
    mdd = 0.0
    if len(curve) >= 2:
        peak = max(c['equity'] for c in curve)
        mdd = (min(c['equity'] for c in curve) - peak) / peak * 100
    from datetime import datetime as _dt
    board = {
        'date': latest_day, 'build_time': _dt.now().strftime('%Y-%m-%d %H:%M:%S'), 'total_asset': round(total, 2),
        'mv': round(sum(p['mv'] for p in positions), 2), 'cash': round(acct['cash'], 2),
        'start_cash': state.get('start_cash'), 'day_pnl': round(day_pnl, 2) if day_pnl else None,
        'positions': positions, 'fills_today': fills_today,
        'curve': [{'date': c['date'], 'equity': c['equity'],
                   'bench': bench[i] if i < len(bench) else None}
                  for i, c in enumerate(curve)],
        't_charts': t_charts,
        'pnl_summary': pnl_summary, 'pnl_detail': pnl_detail,
        'selfcheck': selfcheck,
        'live': {'scan': latest_scan, 'momentum': latest_momentum, 'pos_live': pos_live},
        'progress': {'multiple': round(multiple, 2) if multiple else None,
                     'player_mult': player_mult,
                     'pct': progress,
                     'mdd': round(mdd, 2)},
    }
    BOARD.mkdir(parents=True, exist_ok=True)
    _atomic_write(BOARD / 'board.json', json.dumps(board, ensure_ascii=False))
    tpl = TPL.read_text(encoding='utf-8')
    html = tpl.replace('__BOARD_JSON__', json.dumps(board, ensure_ascii=False))
    _atomic_write(BOARD / 'index.html', html)
    print(f'看板已生成: http://127.0.0.1:8765/  ({latest_day}, 持仓 {len(positions)} 只, 做T图 {len(t_charts)} 只)')


if __name__ == '__main__':
    main()