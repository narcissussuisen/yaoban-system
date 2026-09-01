"""R3' 第二轮回放：龙头联动（dragon_link）/ 板块退潮（sector_retreat）组合级信号验证

案例:
  A. 6/5 辽宁能源(600758): v25@01:07 龙头豫能控股(001896)早盘冲高被砸→跟风先出局
     —— 引擎: dragon_signal(001896, 6/5) → manage_day(600758) 清仓时刻/价格 vs 选手「早盘亏损出局」
  B. 6/5 板块退潮: 电力主线概念组(001896+600758) 与 申万L2 410100 板块指数分时破均价线信号
  C. 7/10 华微(600360): 自身反复炸板 zhaban_sell 复验 + 其板块龙头信号
  D. 8/26 百花(600721): 分钟数据受限(F盘止8/21, tick池仅84只且无龙头元数据) —— 日线级冲高回落核验
  E. 001896 全年炸板模式扫描（zhaban 检测器机械验证）

用法: python scripts/r3p_combo_replay.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from data.qfq_store import QFQStore  # noqa: E402
from core.sell import simulate_hold, limit_price  # noqa: E402
from core.combo_sell import (dragon_signal, dragon_leader, sector_retreat,  # noqa: E402
                             sector_index_minute, group_of, concept_of, industry_of)  # noqa: E402


def main():
    st = QFQStore('2026')
    print('=' * 70)
    print('A. 6/5 辽宁能源 龙头联动回放（v25 豫能冲高被砸→先出来）')
    print('=' * 70)
    # 概念组 vs 申万组
    for s, d in [('600758', '2026-06-05'), ('001896', '2026-06-05')]:
        print(f'  {s} {d}: concept={concept_of(s, d)}  sw_l2={industry_of(s, d)}')
    kind, name, members = group_of(st, '600758', '2026-06-05')
    print(f'  600758 分组: {kind}/{name} members={members}')
    leader = dragon_leader(st, members, '2026-06-05')
    print(f'  龙头识别: {leader}')
    sig = dragon_signal(st, '001896', '2026-06-05')
    print(f'  豫能控股 6/5 信号: {sig}')
    if sig:
        combo = {'2026-06-05': {'dragon_time': sig['time'], 'dragon_kind': sig['kind']}}
        # 假设选手 6/4 收盘价持有（入场价未知，取 6/4 收盘为中性基准）
        daily = pd.DataFrame(st.get_stock('600758'),
                             columns=['symbol','date','open','high','low','close','volume','amount'])
        entry_px = float(daily[daily.date == '2026-06-04']['close'].iloc[0])
        print(f'  辽宁能源 6/4 收盘(入场基准): {entry_px:.3f}')
        r = simulate_hold(st, '600758', '2026-06-04 15:00', entry_px, 10000,
                          params={'dragon_link_sell': True, 'dragon_link_action': 'clear',
                                  'stop_loss_pct': 5.0, 'vwap_halve_min_gain': 7.0,
                                  'break_low_arm': 2.0, 'daily_ma5_halve': False,
                                  'daily_ma10_clear': False},
                          horizon_days=2, start_mgmt_day=1, daily_df=daily,
                          combo=combo)
        print(f'  引擎: exit_reason={r["exit_reason"]} exit_ts={r["exit_ts"]} '
              f'exit_px={r["exit_px"]:.3f} pnl={r["realized_pnl_pct"]:.2f}%')
        for f in r['fills']:
            print('   ', f)
    # 无龙头联动对照（同日无 combo 引擎会怎么做）
    daily = pd.DataFrame(st.get_stock('600758'),
                         columns=['symbol','date','open','high','low','close','volume','amount'])
    entry_px = float(daily[daily.date == '2026-06-04']['close'].iloc[0])
    r0 = simulate_hold(st, '600758', '2026-06-04 15:00', entry_px, 10000,
                       params={'stop_loss_pct': 5.0, 'vwap_halve_min_gain': 7.0,
                               'break_low_arm': 2.0, 'daily_ma5_halve': False,
                               'daily_ma10_clear': False},
                       horizon_days=2, start_mgmt_day=1, daily_df=daily)
    print(f'  对照(无龙头联动): exit_reason={r0["exit_reason"]} pnl={r0["realized_pnl_pct"]:.2f}%')

    print()
    print('=' * 70)
    print('B. 6/5 板块退潮信号（概念组 vs 申万L2电力）')
    print('=' * 70)
    univ = {s for s in st.symbols() if not s.startswith(('399', '5', '15', '16'))}
    sw_power = [m for m in univ if industry_of(m, '2026-06-05') == '410100']
    for label, mem in [('概念组(2只)', members), (f'申万410100电力({len(sw_power)}只)', sw_power)]:
        r_ret = sector_retreat(st, mem, '2026-06-05')
        print(f'  {label}: retreat={r_ret}')
    r_idx, _ = sector_index_minute(st, members, '2026-06-05')
    if r_idx is not None:
        print(f'  概念组板块指数(pct): 开盘={r_idx["pct"].iloc[0]*100:.2f}% '
              f'收盘={r_idx["pct"].iloc[-1]*100:.2f}% 日内最低={r_idx["pct"].min()*100:.2f}%')

    print()
    print('=' * 70)
    print('C. 7/10 华微电子 自身炸板复验 + 板块信号')
    print('=' * 70)
    daily = pd.DataFrame(st.get_stock('600360'),
                         columns=['symbol','date','open','high','low','close','volume','amount'])
    d710 = daily[daily.date == '2026-07-10']
    if not d710.empty:
        pc = float(daily[daily.date == '2026-07-09']['close'].iloc[0])
        r710 = d710.iloc[0]
        lp = limit_price(pc, '600360')
        print(f'  华微 7/10: prev={pc:.3f} limit={lp} 高={r710["high"]:.3f} 低={r710["low"]:.3f} '
              f'收={r710["close"]:.3f}')
        print(f'  触板: {r710["high"] >= lp - 0.01}  收盘距板: {(lp - r710["close"]) / lp * 100:.2f}%')
    k, n, mems = group_of(st, '600360', '2026-07-10')
    print(f'  华微分组: {k}/{n} n={len(mems)}')
    if mems:
        ld = dragon_leader(st, mems, '2026-07-10')
        print(f'  板块龙头: {ld}')
        if ld:
            print(f'  龙头信号: {dragon_signal(st, ld[0], "2026-07-10")}')
        print(f'  板块退潮信号: {sector_retreat(st, mems, "2026-07-10")}')

    print()
    print('=' * 70)
    print('D. 8/26 百花医药 —— 分钟数据受限核验（如实记录）')
    print('=' * 70)
    fp = pathlib.Path('F:/WorkBuddyItem/a股level2/daily/600721.parquet')
    if fp.exists():
        d2 = pq.read_table(fp).to_pandas()
        d826 = d2[d2.date == '2026-08-26']
        if not d826.empty:
            r26 = d826.iloc[0]
            pc = float(d2[d2.date == '2026-08-25']['close'].iloc[0])
            print(f'  百花 8/26: prev={pc:.2f} 开={r26["open"]:.2f} 高={r26["high"]:.2f} '
                  f'低={r26["low"]:.2f} 收={r26["close"]:.2f}')
            print(f'  冲高={r26["high"]/pc-1:+.2%} 收盘={r26["close"]/pc-1:+.2%} '
                  f'（锚: +5.33% 止盈 vs 成本, 10:16 龙头炸板）')
    print('  数据限制: F盘分钟止于8/21; 8/26 tick池仅84只且池快照无名称/龙头元数据',
          '→ 分钟级龙头炸板回放不可行, 待tick积累(P1 blocked)')

    print()
    print('=' * 70)
    print('E. 001896 全年炸板/冲高回落信号扫描（检测器机械验证）')
    print('=' * 70)
    rows = st.get_stock('001896')
    dates = [r[1] for r in rows]
    hits = []
    for d in dates:
        if d < '2026-05-01':
            continue
        s = dragon_signal(st, '001896', d)
        if s:
            hits.append((d, s['kind'], s['time'], round(s.get('peak_pct', 0), 2)))
    print(f'  5月以来信号日 {len(hits)} 个:')
    for h in hits:
        print('   ', h)
    st.close()


if __name__ == '__main__':
    main()