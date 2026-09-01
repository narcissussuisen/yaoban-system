"""8/28 上午决策重建（纪律：只用 ≤11:30 数据）
持仓 4 只: 卖出触发检查（破均价线/冲高回落/炸板等）
备选 4 只: 三引擎买点触发（触发价买入）+ 破均价线否决
输出: outputs/intraday/morning_decision_2026-08-28.json + 更新 ledger
用法: python scripts/rebuild_morning.py
"""
from __future__ import annotations
import json
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'portfolio'))
import pandas as pd
from core.intraday import detect_b_point, detect_dibu_buy, detect_pullback_buy, vwap_series
from core.sell import limit_price
from ledger import load, save, buy, sell, t_buy, t_sell, equity, record_review

BASE = pathlib.Path(__file__).resolve().parent.parent
INTRADAY = BASE / 'outputs' / 'intraday'
DAY = '2026-08-28'
POS = ['601212', '601388', '603132', '000426']
WATCH = ['301003', '301205', '002017', '002313']

def load_day(sym):
    fp = INTRADAY / f'{sym}_{DAY}.json'
    if not fp.exists():
        return None
    rows = json.loads(fp.read_text(encoding='utf-8'))
    # 过滤当日 + 截断到 11:30（上午重建纪律）
    am = [r for r in rows if r[0].startswith(DAY) and r[0][11:16] <= '11:30']
    if not am:
        return None
    return pd.DataFrame(am, columns=['ts', 'open', 'high', 'low', 'close', 'volume', 'amount'])

def prev_close(sym):
    import sqlite3
    con = sqlite3.connect(BASE / 'data' / 'yaoban.db')
    r = con.execute("SELECT close FROM stock_daily WHERE symbol=? AND date < ? ORDER BY date DESC LIMIT 1", (sym, DAY)).fetchone()
    con.close()
    if r:
        return float(r[0])
    fp = pathlib.Path('F:/WorkBuddyItem/a股level2/daily') / f'{sym}.parquet'
    if fp.exists():
        df = pd.read_parquet(fp)
        df['date'] = df['date'].astype(str)
        sub = df[df['date'] < DAY]
        if len(sub):
            return float(sub.sort_values('date')['close'].iloc[-1])
    return None

def main():
    st = load()
    decisions = {'date': DAY, 'buys': [], 'sells': [], 'notes': []}
    # ---- 持仓上午检查
    for sym in POS:
        day = load_day(sym)
        if day is None:
            decisions['notes'].append(f'{sym}: 无上午数据')
            continue
        pc = prev_close(sym)
        vw = vwap_series(day)
        am_high = float(day['high'].max())
        am_low = float(day['low'].min())
        last_close = float(day['close'].iloc[-1])
        last_ts = str(day['ts'].iloc[-1])
        lim = limit_price(pc, sym) if pc else None
        broke_vwap = bool((day['close'] < vw * 0.997).any())
        # 卖出触发（预案）：破均价线减半（当日高点≥+7%后）；炸板（触板回落>0.5%）；-5%止损
        stop_px = st['account']['positions'].get(sym, {}).get('stop_px')
        action = None
        if stop_px and am_low <= stop_px:
            action = {'side': 'sell', 'reason': '止损', 'px': min(last_close, stop_px), 'ts': last_ts}
            decisions['sells'].append({'sym': sym, **action})
        elif lim and float(day['high'].max()) >= lim - 0.01 and last_close < lim * 0.995:
            action = {'side': 'sell', 'reason': '炸板卖出', 'px': last_close, 'ts': last_ts}
            decisions['sells'].append({'sym': sym, **action})
        elif broke_vwap and am_high >= pc * 1.07:
            half = st['account']['positions'][sym]['qty'] // 2 // 100 * 100
            if half >= 100:
                action = {'side': 'sell', 'reason': '破均价线减半', 'px': last_close, 'ts': last_ts, 'qty': half}
                decisions['sells'].append({'sym': sym, **action})
        print(f'  持仓 {sym}: 收{last_close:.2f} 高{am_high:.2f} 低{am_low:.2f} 破线={broke_vwap} 触发={action and action["reason"]}')
    # ---- 备选上午买点检查
    for sym in WATCH:
        day = load_day(sym)
        if day is None:
            continue
        pc = prev_close(sym)
        cands = []
        for _, b in detect_b_point(day, prev_close=pc, max_pct=0.03).iterrows():
            cands.append((str(b['ts'])[11:16], float(b['price']), 'B'))
        for _, b in detect_dibu_buy(day, prev_close=pc, prev5_amt=None, realtime=True).iterrows():
            cands.append((str(b['ts'])[11:16], float(b['price']), 'D'))
        for _, b in detect_pullback_buy(day, prev_close=pc, max_pct=3.0).iterrows():
            cands.append((str(b['ts'])[11:16], float(b['price']), 'P'))
        vw = vwap_series(day)
        broke = bool((day['close'] < vw * 0.997).any())  # 破均价线否决（选手东信和平案例）
        if not cands:
            print(f'  备选 {sym}: 无买点触发')
            continue
        t, px, kind = sorted(cands, key=lambda x: x[0])[0]
        if broke:
            print(f'  备选 {sym}: 买点{t}@{px:.2f} 但破均价线 → 否决（选手纪律）')
            decisions['notes'].append(f'{sym}: 买点但破均价线否决')
            continue
        lim = limit_price(pc, sym)
        if px >= lim - 0.01:
            print(f'  备选 {sym}: 封板价, 不买')
            continue
        decisions['buys'].append({'sym': sym, 'ts': t, 'px': px, 'kind': kind})
        print(f'  备选 {sym}: ✓ 买入点 {t}@{px:.2f} ({kind})')
    # 记账：上午买入（最多 3 只, 单票≤30%）；卖出按触发
    cap = 0.9
    alloc = 100000 * cap / 3  # 集中度: ≤3 只
    for bd in decisions['buys'][:3]:
        sym = bd['sym']
        px = bd['px']
        qty = int(alloc / px // 100 * 100)
        if qty >= 100:
            try:
                buy(st, sym, f'{DAY} {bd["ts"]}', px, qty, bd['kind'], stop_pct=5.0, plan_ref='plan-0828')
                bd['qty'] = qty
                print(f'  ✓ 买入 {sym} {qty}股 @{px}')
            except ValueError as e:
                print(f'  {sym} 买入失败: {e}')
    for sd in decisions['sells']:
        sym = sd['sym']
        pos = st['account']['positions'].get(sym)
        if not pos:
            continue
        qty = sd.get('qty', pos['qty'])
        try:
            sell(st, sym, f'{DAY} {sd["ts"]}', sd['px'], qty, sd['reason'], plan_ref='plan-0828')
            sd['qty'] = qty
            print(f'  ✓ 卖出 {sym} {qty}股 @{sd["px"]} ({sd["reason"]})')
        except ValueError as e:
            print(f'  {sym} 卖出失败: {e}')
    save(st)
    (BASE / 'outputs' / 'intraday' / f'morning_decision_{DAY}.json').write_text(
        json.dumps(decisions, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(decisions, ensure_ascii=False, indent=1))

if __name__ == '__main__':
    main()
