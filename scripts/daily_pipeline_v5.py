"""R7' 上线基板：v5 全流程每日盘后信号管线（全自动信号输出，人工确认执行）

流程（T 日盘后）:
  1. 数据: F盘 qfq 分钟/日线（2025 预热 + 2026）+ TDX 日线回填（近端补缺）
  2. 选股: detect_huigui_v5 全市场 → 当日+近5日信号 → 候选池（含 l2/概念）
  3. 板块热度: 当日各申万L2 涨停数/成交额占比 → 主线排序
  4. 温度计: 当日 zt/dt/炸板率/连板高度/中位数 → 温度+冰点避险档
  5. 软评分排序: 板块热度 > 突破前5日最高收盘 > 收阳 → 备选 N 只
  6. 输出报告: 备选名单+买点监控条件(三引擎阈值+昨收/涨停价)+持仓卖出触发清单+仓位建议

用法: python scripts/daily_pipeline_v5.py [--date 2026-08-21] [--top-n 4] [--workers 6]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from data.qfq_store import QFQStore, build_daily_map  # noqa: E402
from core import strategies as S  # noqa: E402
from core.combo_sell import industry_of, concept_of, preload_daily  # noqa: E402
from core.sentiment import emotion_thermometer  # noqa: E402
from core.sell import limit_pct_of  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = BASE / 'outputs' / 'signals'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='', help='报告日；缺省=日线映射最新交易日（自动派生）')
    ap.add_argument('--top-n', type=int, default=4)
    ap.add_argument('--workers', type=int, default=6)
    args = ap.parse_args()
    d = args.date
    st = QFQStore('2026', cache_minute=64)
    syms = [s for s in st.symbols() if not s.startswith(('399', '5', '15', '16', '899'))]
    print('预载日线映射...', flush=True)
    dmap = build_daily_map('2026', syms, workers=args.workers)
    preload_daily(dmap)
    if not d:
        _maxd = max(r[1] for rows in dmap.values() for r in rows if rows)
        d = _maxd
        print(f'自动派生报告日: {d}', flush=True)
    zt = dt_cnt = touch = zhaban = 0
    pcts = []
    max_h = 0
    sector_zt = {}
    for sym, rows in dmap.items():
        if len(rows) < 2:
            continue
        dates = [r[1] for r in rows]
        if d not in dates:
            continue
        i = dates.index(d)
        if i == 0:
            continue
        pc = float(rows[i - 1][5])
        c = float(rows[i][5])
        hi = float(rows[i][3])
        pct_lim = limit_pct_of(sym)
        lim = round(pc * (1 + pct_lim), 2)
        dlim = round(pc * (1 - pct_lim), 2)
        is_zt = c >= lim - 0.005
        if is_zt:
            zt += 1
        if c <= dlim + 0.005:
            dt_cnt += 1
        if hi >= lim - 0.005:
            touch += 1
            if not is_zt:
                zhaban += 1
        pcts.append(c / pc - 1)
        l2 = industry_of(sym, d) or 'NA'
        sector_zt[l2] = sector_zt.get(l2, 0) + (1 if is_zt else 0)
        lb = 0
        j = i
        while j >= 1 and float(rows[j][5]) >= round(float(rows[j - 1][5]) * (1 + pct_lim), 2) - 0.005:
            lb += 1
            j -= 1
        max_h = max(max_h, lb)
    med = float(np.median(pcts)) * 100 if pcts else None
    zhaban_rate = zhaban / touch * 100 if touch else 0.0
    t = emotion_thermometer(zt, dt_cnt, zhaban_rate, max_h, median_pct=med)
    print('情绪: zt={} dt={} touch={} 炸板率={:.1f}% 高度={} 中位数={} 温度={} {}'.format(
        zt, dt_cnt, touch, zhaban_rate, max_h,
        (f'{med:.2f}%' if med is not None else 'N/A'), t['temp'], t['stage']), flush=True)
    from datetime import date as _d, timedelta as _td
    window = []
    dd = _d.fromisoformat(d)
    while len(window) < 6:
        if dd.weekday() < 5:
            window.append(dd.isoformat())
        dd -= _td(days=1)
    cands = []
    for sym, rows in dmap.items():
        if len(rows) < 70:
            continue
        df = pd.DataFrame(rows, columns=['symbol', 'date', 'open', 'high', 'low',
                                          'close', 'volume', 'amount'])
        df['date'] = df['date'].astype(str)
        sig = S.detect_huigui_v5(df)
        dlist = df['date'].tolist()
        for sd in window:
            if sd not in dlist:
                continue
            if not bool(sig[df['date'] == sd].any()):
                continue
            i = dlist.index(sd)
            r = df.iloc[i]
            yang = float(r['close']) > float(r['open'])
            brk5 = False
            if i >= 5:
                brk5 = float(r['close']) >= max(float(x) for x in df['close'].iloc[i - 5:i])
            l2 = industry_of(sym, sd) or ''
            cands.append({'symbol': sym, 'sig_date': sd, 'l2': l2,
                          'yang': yang, 'brk5': brk5,
                          'heat': sector_zt.get(l2, 0) if sd == d else 0,
                          'concept': concept_of(sym, sd) or ''})
    print(f'候选池 {len(cands)} 只（{d} 及前5日信号）', flush=True)
    cand_df = pd.DataFrame(cands).drop_duplicates(subset=['symbol'])
    cand_df = cand_df.sort_values(['heat', 'brk5', 'yang'], ascending=False)
    top = cand_df.head(args.top_n)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bing = (dt_cnt > zt and zt < 40)
    pos_advice = '冰点避险 ≤20%' if bing else '正常 90%（冰点=dt>zt且zt<40）'
    med_s = f'{med:+.2f}%' if med is not None else 'N/A'
    lines = ['# 妖板交易系统 v5 信号报告 ' + d, '',
             '- 情绪: 温度 {}（{}）| 涨停 {} | 跌停 {} | 炸板率 {:.1f}% | 连板高度 {} | 中位数 {}'.format(
                 t['temp'], t['stage'], zt, dt_cnt, zhaban_rate, max_h, med_s),
             '- 仓位建议: ' + pos_advice,
             '- 板块主线（当日涨停数前5）: ' + '、'.join(f'{k}({v})' for k, v in sorted(sector_zt.items(), key=lambda x: -x[1])[:5]),
             '', '## 备选名单（T+1 盘中确认，三引擎触发才买）', '',
             '| 代码 | 信号日 | 板块 | 概念 | 昨收 | 涨停价 | 买点监控条件 |',
             '|---|---|---|---|---|---|---|']
    for _, r in top.iterrows():
        sym = r['symbol']
        rows = dmap.get(sym)
        if not rows:
            continue
        dates = [x[1] for x in rows]
        if d not in dates:
            continue
        i = dates.index(d)
        # 评审整改: 报告日为 T，买入日为 T+1 —— 昨收/涨停价锚定 T 日收盘（rows[i]，原误用 T-1）
        pc = float(rows[i][5])
        lim = round(pc * (1 + limit_pct_of(sym)), 2)
        cond = '回踩均价线收复≤+3% 或 下杀≥2%回拉 或 低开≥3%高走'
        lines.append(f'| {sym} | {r["sig_date"]} | {r["l2"]} | {r["concept"]} | '
                      f'{pc:.2f} | {lim:.2f} | {cond} |')
    lines += ['', '## 持仓卖出触发清单（盘中）',
              '- 破分时均价线减半（当日高点≥+7%后）/ 再破清仓；冲高回落破位止损（+2%布防/峰值回撤4%）',
              '- 炸板卖出（触板回落>0.5%）；次高点卖出（冲高≥5%后60分钟不创新高且回落1%）',
              '- 龙头联动：板块龙头炸板/冲高回落≥3% → 跟风出局；板块退潮（指数破均价线且跌>0.5%）→ 减半',
              '- 乖离率≥8%冲高回落减半；KDJ死叉（昨日死叉今日执行）清仓',
              '- 日线兜底：破5日线减半(持有≥2天且浮盈)/破10日线3天清/5日时间止损（涨停收盘日豁免=连板骑乘）',
              '- 止损底线：-5%跳空低开立即执行，收盘确认（收盘<止损且<MA10）清', '',
              '> 全自动信号输出 · 人工确认执行（V5_ALIGNMENT #2）']
    out_path = OUT_DIR / f'{d}_signal_report_v5.md'
    out_path.write_text('\n'.join(lines), encoding='utf-8')
    print(f'报告: {out_path}')
    st.close()


if __name__ == '__main__':
    main()