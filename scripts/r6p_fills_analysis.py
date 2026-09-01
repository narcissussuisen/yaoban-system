"""R6' 第三轮证据1：v2 基线成交明细逐笔盈亏分析（确认层筛选力）

输入: outputs/r6p_eq_v2_base_fills.csv（评审整改④标配）
输出: 逐笔盈亏分布 / 按 kind 分组 / 持有天数 / k 分布（需候选k列——评审仪器化fills含k列，交付版无则注记）

用法: python scripts/r6p_fills_analysis.py [--fills outputs/r6p_eq_v2_base_fills.csv]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fills', default='outputs/r6p_eq_v2_base_fills.csv')
    args = ap.parse_args()
    df = pd.read_csv(BASE / args.fills, dtype=str)
    df['px'] = pd.to_numeric(df['px'])
    df['qty'] = pd.to_numeric(df['qty'])
    print(f'fills {len(df)} 笔: 买 {len(df[df.side=="buy"])} / 卖 {len(df[df.side=="sell"])}')
    print('kind 分布:'); print(df[df.side=='buy'].kind.value_counts().to_string())
    # 逐笔配对（按 symbol 顺序配买卖）
    trades = []
    for sym, g in df.groupby('sym'):
        g = g.sort_values('ts')
        buy_q = 0.0
        buy_v = 0.0
        for _, f in g.iterrows():
            if f['side'] == 'buy':
                buy_q += f['qty']
                buy_v += f['qty'] * f['px']
            else:
                sell_q = f['qty']
                if buy_q > 0:
                    cost_per = buy_v / buy_q
                    pnl = (f['px'] - cost_per) * sell_q
                    trades.append({'sym': sym, 'buy_date': g[g.side=='buy'].ts.iloc[0][:10] if len(g[g.side=='buy']) else '',
                                  'sell_ts': f['ts'], 'pnl': pnl, 'invest': buy_v / max(1, buy_q) * sell_q,
                                  'ret': pnl / max(1e-9, cost_per * sell_q) * 100,
                                  'reason': f['kind']})
                    buy_q -= sell_q
                    if buy_q <= 1e-6:
                        buy_q = 0.0; buy_v = 0.0
    t = pd.DataFrame(trades)
    if t.empty:
        print('无配对交易'); return
    print(f'\n配对交易 {len(t)} 笔:')
    print(f'  胜率 {(t.ret>0).mean()*100:.0f}%, 平均 {t.ret.mean():+.2f}%, 中位 {t.ret.median():+.2f}%')
    print(f'  最大盈 {t.ret.max():+.1f}% / 最大亏 {t.ret.min():+.1f}%')
    print(f'  期望(等权) {t.ret.mean():+.2f}% / 总PnL {t.pnl.sum():+.0f}元')
    print('\n按卖出原因:')
    print(t.groupby('reason')['ret'].agg(['count', 'mean']).round(2).to_string())
    print('\n按买入kind:')
    print(t.groupby('buy_kind') if 'buy_kind' in t else '')
    t.to_csv(BASE / 'outputs' / 'r6p_v2_trades.csv', index=False)
    print('\n明细: outputs/r6p_v2_trades.csv')


if __name__ == '__main__':
    main()