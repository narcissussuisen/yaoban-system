"""R4' 第二轮（T部分）：通富微电 77.38→74.31 做T回放（v48/v49 两次正T降成本3元）

选手证据（GAP_FINAL + E_出场持有做T洗盘.md）:
  - 通富微电深套（成本 77.38），7/8+7/9 两次正T，成本降至 74.31（-3.07元 ≈ -3.97%）
  - v48@01:10 (7/8): 分时急跌=弹簧，后续会回拉（正T day1）
  - v49@01:25 (7/9): 继续正T，早上均价线下方补仓，拉升有差价T出，T进T出数量一致
  - C21 已复现 7/9 T进 100@65.44 的买点（GAP_intraday_mode.md L100）

回放口径: entry_px=77.38(成本基准, 非市场价), stop_px=None(深套解套模式不触发止损),
  T 参数=引擎默认(1/3仓, 差价1.5%, 买偏离1.5%, 卖偏离3%, 振幅≥2%)
  7/8+7/9 逐日 manage_day, 统计 T 次数与成本降幅, 对比选手 3.07 元

用法: python scripts/r4p_t_replay.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.qfq_store import QFQStore  # noqa: E402
from core.sell import manage_day, limit_price, DEFAULT_PARAMS  # noqa: E402


def main():
    st = QFQStore('2026')
    sym = '002156'
    cost = 77.38
    base = 30000  # 底仓3万股
    p = dict(DEFAULT_PARAMS)
    p['stop_loss_pct'] = 0  # 深套解套模式: 不触发止损
    p['vwap_halve'] = False
    p['break_low_clear'] = False
    p['second_high_sell'] = False
    p['zhaban_sell'] = False
    p['daily_ma5_halve'] = False
    p['daily_ma10_clear'] = False
    dall = pd.DataFrame(st.get_stock(sym),
                        columns=['symbol','date','open','high','low','close','volume','amount'])
    print(f'通富微电 做T回放 成本 {cost} 底仓 {base}股')
    print()
    for d in ['2026-07-08', '2026-07-09']:
        rows = st.get_minute(sym, start=d, end=d)
        day = pd.DataFrame(rows, columns=['symbol','freq','ts','open','high','low',
                                          'close','volume','amount'])
        dates = dall['date'].tolist()
        prev_close = None
        if d in dates and dates.index(d) > 0:
            prev_close = float(dall.iloc[dates.index(d) - 1]['close'])
        lp = limit_price(prev_close, sym) if prev_close else None
        res = manage_day(day, prev_close, base, None, cost, p, limit_px=lp)
        fills = [f for f in res['fills'] if f['reason'].startswith('t_')]
        t_profit = 0.0
        for f in fills:
            if f['side'] == 'sell':
                t_profit += f['qty'] * f['px']
            else:
                t_profit -= f['qty'] * f['px']
        cost_after = cost - t_profit / base
        hi = day['high'].max()
        lo = day['low'].min()
        print(f'{d}: 昨收{prev_close:.2f} 高{hi:.2f} 低{lo:.2f} 收{day["close"].iloc[-1]:.2f} '
              f'振幅{(hi/lo-1)*100:.1f}%')
        print(f'  T成交 {len(fills)} 笔, t_round={res["t_round"]}, T净利 {t_profit:+.0f}元, '
              f'成本→ {cost_after:.2f} (降{cost-cost_after:.2f}元)')
        for f in fills:
            print(f'    {f["ts"]} {f["side"]} {f["qty"]}@{f["px"]} ({f["reason"]})')
    print()
    print('选手对照: 两次正T 成本 77.38→74.31 = -3.07元 (-3.97%)')
    st.close()


if __name__ == '__main__':
    main()