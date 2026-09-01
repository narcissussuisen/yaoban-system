"""R4' 第一轮：选手仓位锚 vs 环境分档位验证

选手仓位轨迹（GAP_FINAL_2026-08-26.md）: 99.8%(4/21) → 69.3%(6/18) → 34.8%(7/6) → 20%(7/17 至暗) → 51.2%(8/13)
引擎: env_score 五维(情绪数据可用时) / 简版(trend+volume) → regime → position_cap

用法: python scripts/r4p_env_anchor.py
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.env_score import env_score, env_score_full  # noqa: E402

DB = pathlib.Path(__file__).resolve().parent.parent / 'data' / 'yaoban.db'
SENT = pathlib.Path(__file__).resolve().parent.parent / 'outputs' / 'sentiment_daily_2026.csv'

ANCHORS = [('2026-04-21', 99.8), ('2026-06-18', 69.3), ('2026-07-06', 34.8),
           ('2026-07-17', 20.0), ('2026-08-13', 51.2)]


def main():
    con = sqlite3.connect(DB)
    idx = pd.read_sql('SELECT date, open, high, low, close, volume FROM index_daily '
                      "WHERE symbol='sh000001' AND date >= '2026-01-01' ORDER BY date", con)
    con.close()
    sent = None
    if SENT.exists():
        sent = pd.read_csv(SENT)
        sent['date'] = sent['date'].astype(str)
    print(f'指数日线 {len(idx)} 行(至{idx.date.iloc[-1]}); 情绪数据 {len(sent) if sent is not None else 0} 行')
    print('说明: 四维 = trend+volume+limit_up(zt)+height(max_h); 缺 breadth(红盘占比/dt)')
    print()
    print(f'{"日期":<12}{"选手仓位%":>8} {"简版分":>6}{"简版档":>6}{"cap%":>7} {"四维分":>6}{"四维档":>6}{"cap%":>7}  判定')
    for d, pos in ANCHORS:
        sub = idx[idx.date <= d]
        if sub.empty:
            print(f'{d:<12} NO_INDEX'); continue
        s = env_score(sub)
        f4s, f4r, f4c = '-', '-', '-'
        if sent is not None:
            row = sent[sent.date == d]
            if not row.empty:
                r = row.iloc[0]
                f4 = env_score(sub, limit_up_count=int(r.get('zt') or 0),
                               max_board_height=int(r.get('max_h') or 0))
                f4s, f4r, f4c = f4['score'], f4['regime'], f4['position_cap']
        # 方向判定（选手强市可超cap, 属加仓弹性）: strong→pos≥50, neutral→30-70, weak→pos≤30
        ok = ''
        if s['regime'] == 'strong' and pos >= 50:
            ok = '✓强'
        elif s['regime'] == 'neutral' and 30 <= pos <= 70:
            ok = '✓中'
        elif s['regime'] == 'weak' and pos <= 30:
            ok = '✓弱'
        else:
            ok = '✗'
        print(f'{d:<12}{pos:>8.1f} {s["score"]:>6} {s["regime"]:>6}{s["position_cap"]:>7.0f} '
              f'{f4s!s:>6} {f4r!s:>6}{f4c!s:>7}  {ok}')
    print()
    print('量能 2.5 万亿门槛(8/26证据): index_daily 无 amount 列, 两市成交额数据缺口 → R4\'第二轮补(涨停池行情接口)')


if __name__ == '__main__':
    main()