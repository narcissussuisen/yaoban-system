"""R6' 全流程 2026 复现 v1：候选表 + 三引擎盘中确认 + 卖出引擎 + 组合级联动 + 温度计仓位档

管线（逐日 2026-04-01 ~ 08-21）:
  1. 候选: r6p_candidates_2026.csv（R1\' 信号日起 5 交易日有效）
  2. 收敛: 软评分排序(突破前5日最高收盘>收阳>T+1放量) + 当日温度 → 每日取前 N 只备选
  3. 买点: 三引擎(B点 max_pct=3 / 抄底 realtime=True / 回踩低吸) 当日首个信号 → 确认价+成本买入
  4. 仓位: 冰点避险档（R4R5 复审放行口径）——冰点日(dt>zt且zt<40) 20%，其余 90%;
     温度计加权合成不放行驱动仓位档（权重无视频出处）; 最多 4 只并行, 单票=cap×净值/4
  5. 卖出: manage_day（12触发+做T）+ 组合级 dragon_link/sector_retreat（按持仓板块日频计算）
     + 日线兜底(破MA5减半/破MA10清/5日时间止损); T+1 约束
  6. 成本: 佣金0.025%双边+印花0.05%卖+滑点0.1%单边

对标: 选手 6.4× (5W→32W, 76天) | 裁决: ≥3× 且 回撤≤选手(-13%级)

用法: python scripts/r6p_replication.py [--top-n 4] [--max-pos 4] [--debug-symbols 600758]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from data.qfq_store import QFQStore, build_daily_map  # noqa: E402
from core.intraday import detect_b_point, detect_dibu_buy, detect_pullback_buy, vwap_series  # noqa: E402
from core.sell import manage_day, limit_price, DEFAULT_PARAMS  # noqa: E402
from core.combo_sell import (group_of, dragon_signal, dragon_leader, sector_retreat,  # noqa: E402
                             preload_daily)
from core.sentiment import emotion_thermometer  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
COMM, STAMP, SLIP = 0.00025, 0.0005, 0.001
START_CASH = 50000.0
MIN_INTERVAL = 5


def buy_net(px):
    return px * (1 + COMM + SLIP)


def sell_net(px):
    return px * (1 - COMM - SLIP - STAMP)


def temp_cap(temp: float, is_bingdian: bool) -> float:
    """仓位档（R4R5 评审口径）：冰点避险——冰点日 20%，否则 90%。
    温度计加权合成不放行驱动仓位档（权重无视频出处）；冰点特例(dt>zt)有真实区分度（评审确认）。"""
    return 0.20 if is_bingdian else 0.90


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--top-n', type=int, default=4)
    ap.add_argument('--max-pos', type=int, default=4)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--debug-symbols', default='')
    ap.add_argument('--max-days', type=int, default=0, help='调试: 只跑前 N 天')
    ap.add_argument('--verbose', action='store_true')
    ap.add_argument('--alloc-frac', type=float, default=0.0, help='单票资金占比(×净值×cap); 0=等权 1/max-pos')
    ap.add_argument('--ride-limit', action='store_true', help='连板骑乘: 涨停日不清仓/不兜底/时间止损时钟冻结')
    ap.add_argument('--ride-strong', action='store_true', help='强化骑乘: 前一日涨停收盘的持仓, 当日禁用破线减半/次高点/冲高止盈/做T (仅止损+炸板)')
    ap.add_argument('--time-stop-profit', type=float, default=0.0, help='盈利感知时间止损: 5日后仅当浮盈 < X%% 才时间止损清仓 (X=10 → 浮盈≥10%%的赢家继续骑乘; 0=原逻辑全部清)')
    ap.add_argument('--no-lowopen', action='store_true', help='禁用 dibu_lowopen 买入(证据: 回放7笔均亏-2.3%%/中位-5%%, 圣阳4/2即死于此)')
    ap.add_argument('--e4-support', action='store_true', help='E4支撑位低吸: 当日低点回踩MA5(<=1.01x) + 盘中涨幅>=2%%且站上VWAP才入场 (选手证据: 锚股买入日收MA5上方+7.3%%放量长阳)')
    ap.add_argument('--take-profit', type=float, default=0.0, help='分批兑现止盈: 日内高点>=成本x(1+pct)且未涨停收盘 -> 卖1/3 (选手: 每笔中位+6%%快速兑现)')
    ap.add_argument('--max-buys-per-day', type=int, default=0, help='每日新仓上限 (0=不限; 选手节奏: 每周3.8笔, 4月8笔/21日)')
    ap.add_argument('--min-temp', type=float, default=0.0, help='情绪门控: 前日温度低于该值不建新仓 (0=不限; 选手6月强势期最活跃21笔)')
    ap.add_argument('--min-amt', type=float, default=0.0, help='前日成交额门槛(亿): 选手买入日成交额中位18.9亿/89%%>=5亿, 回放仅4.9亿 -> 妖票活跃底线 (0=不限)')
    ap.add_argument('--temp-ladder', action='store_true', help='温度联动仓位(泛化发现): 前日温度>=70满档/50-70单仓/<50空仓(弱市降档, 2023/2024弱市0.75-0.80x应对)')
    ap.add_argument('--sector-heat', action='store_true', help='软评分加入板块热度(前日板块涨停数)排序')
    ap.add_argument('--max-k', type=int, default=5, help='信号日后可买入窗口上限(1=仅次日, 选手k=-1实证)')
    ap.add_argument('--year', default='2026')
    ap.add_argument('--require-gate', action='store_true', help='入场硬条件: 信号日收阳且突破前5日最高收盘')
    ap.add_argument('--out', default='', help='输出CSV路径(缺省 outputs/r6p_equity_v1.csv)')
    args = ap.parse_args()

    year = args.year
    st = QFQStore(year, cache_minute=64)
    # 日线映射预载一次（避免逐 symbol 全年级分钟聚合——性能优化）
    print('预载日线映射...', flush=True)
    import time as _t
    _t0 = _t.time()
    _syms = [s for s in st.symbols() if not s.startswith(('399', '5', '15', '16', '899', '688', '689', '4', '8', '92'))]
    daily_map = build_daily_map(year, _syms, workers=args.workers)
    preload_daily(daily_map)
    print(f'日线预载完成 {_t.time()-_t0:.0f}s', flush=True)
    cand = pd.read_csv(BASE / 'outputs' / f'r6p_candidates_{year}.csv', dtype=str)
    cand['date'] = cand['date'].astype(str)
    heat_map = {}
    if args.sector_heat:
        hp = BASE / 'outputs' / f'r6p_sector_heat_{year}.csv'
        if hp.exists():
            heat = pd.read_csv(hp, dtype=str)
            heat_map = {(r['date'], r['l2']): float(r['zt_n']) for _, r in heat.iterrows()}
            print(f'板块热度表 {len(heat_map)} 行', flush=True)
    sent = pd.read_csv(BASE / 'outputs' / f'sentiment_full_{year}.csv')
    sent['date'] = sent['date'].astype(str)
    sent['temp'] = [emotion_thermometer(int(r['zt']), int(r['dt']) if pd.notna(r['dt']) else None,
                                       float(r['zhaban_rate']), int(r['max_h']),
                                       float(r['lianban_rate']) if pd.notna(r['lianban_rate']) else None,
                                       float(r['median_pct']) if pd.notna(r['median_pct']) else None)['temp']
                    for _, r in sent.iterrows()]
    sent['bing'] = [(int(r['dt']) if pd.notna(r['dt']) else 0) > int(r['zt']) and int(r['zt']) < 40
                    for _, r in sent.iterrows()]
    temp_of = dict(zip(sent['date'], sent['temp']))
    bing_of = dict(zip(sent['date'], sent['bing']))
    days = sorted(cand['date'].unique().tolist())
    # 候选索引: date -> [(symbol, yang, brk5, buy_yang, buy_vol_up)]
    cand_idx = {}
    for _, r in cand.iterrows():
        cand_idx.setdefault(r['date'], []).append((r['symbol'], r['yang'], r['brk5'],
                                                   r['buy_yang'], r['buy_vol_up']))
    # 交易日历（含候选日前5日的窗口需要完整日历——用 F 盘某只股票全日历）
    cal_rows = daily_map.get('000001') or daily_map.get('600000')
    calendar = [r[1] for r in cal_rows if f'{year}-03-20' <= r[1] <= f'{year}-08-21']
    print(f'日历 {len(calendar)} 天, 候选信号日 {len(days)} 天')

    cash = START_CASH
    pos: dict[str, dict] = {}   # symbol -> {qty, cost, stop_px, low_track, entry_ts, days_held}
    last_exit: dict[str, int] = {}  # symbol -> 日历索引 of last exit
    fills: list = []
    daily_eq: list = []
    debug_only = set(args.debug_symbols.split(',')) if args.debug_symbols else None

    watch_sym_cache: dict[str, list] = {}   # (symbol, day) 分钟缓存
    group_sig_cache: dict = {}             # (symbol, day) -> combo dict

    def combo_for(sym: str, d: str) -> dict | None:
        """持仓股当日组合级信号（按分组缓存——同板块持仓共享）"""
        kind, name, members = group_of(st, sym, d)
        key = (kind, name, d)
        if key in group_sig_cache:
            return group_sig_cache[key]
        out = {}
        if members:
            ld = dragon_leader(st, members, d, as_of_hm='10:00')
            if ld and ld[0] != sym:
                sig = dragon_signal(st, ld[0], d)
                if sig:
                    out['dragon_time'] = sig['time']
            ret = sector_retreat(st, members, d)
            if ret:
                out['sector_retreat_time'] = ret['time']
        group_sig_cache[key] = out
        return out

    def min_day(sym: str, d: str) -> pd.DataFrame | None:
        rows = st.get_minute(sym, start=d, end=d)
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=['symbol', 'freq', 'ts', 'open', 'high', 'low',
                                         'close', 'volume', 'amount'])
        # 数据守卫（R2' 复审新增）：ts 去重 + 240 根守卫（2026-06-17 系统性 480 根重复）
        df = df.drop_duplicates(subset=['ts'])
        if len(df) > 240:
            df = df.iloc[:240]
        return df

    def confirm_buy(sym: str, d: str):
        """三引擎当日首个买入信号 → (price, kind, ts) 或 None"""
        day = min_day(sym, d)
        if day is None:
            return None
        dall = daily_map.get(sym)
        if not dall:
            return None
        dates = [r[1] for r in dall]
        if d not in dates:
            return None
        i0 = dates.index(d)
        pc = float(dall[i0 - 1][5]) if i0 > 0 else float(day['open'].iloc[0])
        prev5 = float(pd.Series([r[7] for r in dall[i0 - 5:i0]]).mean()) if i0 >= 5 else None
        if args.e4_support:
            if args.min_amt > 0 and i0 >= 1:
                prev_amt = float(dall[i0 - 1][7]) if len(dall[i0 - 1]) > 7 else 0.0
                if prev_amt < args.min_amt * 1e8:
                    return None  # 前日成交额不足(选手: 买入日成交额中位18.9亿)
            if i0 < 6:
                return None
            ma5 = float(pd.Series([r[5] for r in dall[i0 - 5:i0]]).mean())
            ma10 = float(pd.Series([r[5] for r in dall[i0 - 10:i0]]).mean()) if i0 >= 10 else ma5
            if float(day['low'].min()) > min(ma5, ma10) * 1.03:
                return None  # 未回踩MA5/MA10支撑(选手: 73%买入日低点在此±3%内)
            vw = vwap_series(day)
            for i in range(5, len(day)):
                ts = str(day['ts'].iloc[i])[11:16]
                px = float(day['close'].iloc[i])
                if (px / pc - 1) >= 0.02 and px >= float(vw.iloc[i]):
                    return (px, 'e4_support', ts)
            return None
        cands = []
        for _, b in detect_b_point(day, prev_close=pc, max_pct=0.03).iterrows():
            cands.append((str(b['ts'])[11:16], float(b['price']), 'B' + str(b['kind'])))
        for _, b in detect_dibu_buy(day, prev_close=pc, prev5_amt=prev5, realtime=True).iterrows():
            if args.no_lowopen and str(b['kind']) == 'dibu_lowopen':
                continue
            cands.append((str(b['ts'])[11:16], float(b['price']), str(b['kind'])))
        for _, b in detect_pullback_buy(day, prev_close=pc, max_pct=3.0).iterrows():
            cands.append((str(b['ts'])[11:16], float(b['price']), 'pullback'))
        if not cands:
            return None
        t, px, kind = sorted(cands, key=lambda x: x[0])[0]
        limit = limit_price(pc, sym)
        if px >= limit - 0.01:
            return None  # 封板价买不进
        return (px, kind, t)

    # 逐日循环
    for di, d in enumerate(calendar):
        # ---- 盘前: 温度计(前日) → 仓位档
        prev_d = calendar[di - 1] if di > 0 else d
        temp = temp_of.get(prev_d, temp_of.get(d, 40.0))
        is_bing = bing_of.get(prev_d, False)
        cap = temp_cap(temp, is_bing)
        # ---- 候选收敛: 窗口(信号日≤T-1, T+1入场; 默认 k=1..5)未持有/未冷却 → 软评分排序 top-N
        # R6' 评审整改①: k=0(当日信号当日买)依赖当日完整日线=前视 → 窗口改 k=1..max_k
        watch = []
        if True:
            for k in range(1, args.max_k + 1):
                sd = calendar[di - k] if di - k >= 0 else None
                if sd is None or sd not in cand_idx:
                    continue
                for (sym, yang, brk5, buy_yang, buy_vol_up) in cand_idx[sd]:
                    if debug_only is not None and sym not in debug_only:
                        continue
                    if sym in pos:
                        continue
                    if sym in last_exit and di - last_exit[sym] < MIN_INTERVAL:
                        continue
                    watch.append((sym, sd, yang, brk5, buy_yang, buy_vol_up))
            # 软评分: (板块热度) → brk5 → 收阳; 同日去重保留最新信号
            # R6' 评审整改②: buy_yang/buy_vol_up 为信号日次日数据(k=1 时=当日收盘, 不可知) → 移除
            seen = {}
            for w in watch:
                seen[w[0]] = w
            watch = list(seen.values())
            if args.sector_heat:
                from core.combo_sell import industry_of as _ind_of, lianban_of as _lb_of
                def _heat_of(w):
                    sym = w[0]
                    l2 = _ind_of(sym, prev_d) or ''
                    h = heat_map.get((prev_d, l2), 0.0)
                    lb = _lb_of(st, sym, prev_d)
                    return (h, lb, w[3] == 'True', w[2] == 'True')
                watch.sort(key=_heat_of, reverse=True)
            else:
                watch.sort(key=lambda w: (w[3] == 'True', w[2] == 'True'), reverse=True)
            watch = watch[:args.top_n]
        if args.verbose:
            print(f'[{d}] watch={len(watch)} top={[w[0] for w in watch]} pos={len(pos)} cash={cash:.0f}', flush=True)
        # ---- 盘中: 三引擎确认买入
        bought_today = []
        n_pos = len(pos)
        eff_max = args.max_pos
        if args.temp_ladder:
            if temp >= 70: eff_max = args.max_pos
            elif temp >= 50: eff_max = min(1, args.max_pos)
            else: eff_max = 0
            if args.verbose and eff_max != args.max_pos:
                print(f'[{d}] 温度{temp:.0f} -> 仓位档{eff_max}仓', flush=True)
        for (sym, sd, yang, brk5, buy_yang, buy_vol_up) in watch:
            if args.min_temp > 0 and temp < args.min_temp:
                break  # 情绪门控: 温度不足不建新仓
            if n_pos >= eff_max or cash < 3000:
                break
            if args.max_buys_per_day > 0 and len(bought_today) >= args.max_buys_per_day:
                break
            cf = confirm_buy(sym, d)
            if cf is None:
                if args.verbose:
                    print(f'    {sym}: 无确认', flush=True)
                continue
            if args.require_gate:
                # 信号日 yang & brk5 硬条件（本候选自身属性=已知；评审修复：
                # 原实现误读去重循环泄漏的 w——日级开关伪条件，C8/C9 数字作废）
                if yang != 'True' or brk5 != 'True':
                    continue
            px, kind, t = cf
            if args.verbose:
                print(f'    {sym}: 确认 {t} px={px:.2f} kind={kind} → 买入', flush=True)
            cur_eq = daily_eq[-1]['equity'] if daily_eq else START_CASH
            frac = args.alloc_frac if args.alloc_frac > 0 else 1.0 / args.max_pos
            alloc = cur_eq * cap * frac  # 单票目标金额（当前净值×仓位档×单票占比）
            alloc = min(alloc, cash)
            qty = int(alloc / buy_net(px) // 100 * 100)
            if qty < 100:
                continue
            cost = buy_net(px) * qty
            cash -= cost
            pos[sym] = {'qty': qty, 'cost_px': px, 'stop_px': px * 0.95,
                        'low_track': px, 'entry_ts': f'{d} {t}', 'days': 0}
            n_pos += 1
            bought_today.append(sym)
            fills.append({'date': d, 'ts': f'{d} {t}', 'sym': sym, 'side': 'buy',
                          'qty': qty, 'px': round(px, 3), 'kind': kind})
        # ---- 持仓管理（卖出/做T；当日买入的不可卖份额在 base0 之外——T+1 近似）
        exited = []
        for sym in list(pos.keys()):
            p = pos[sym]
            day = min_day(sym, d)
            if day is None:
                continue
            dall = daily_map.get(sym)
            if not dall:
                continue
            dates = [r[1] for r in dall]
            i0 = dates.index(d) if d in dates else -1
            pc = float(dall[i0 - 1][5]) if i0 > 0 else float(day['open'].iloc[0])
            # T+1: 当日买入的持仓跳过管理（不可卖）
            if sym in bought_today:
                p['days'] += 1
                continue
            combo = combo_for(sym, d)
            params = dict(DEFAULT_PARAMS)
            params.update({'dragon_link_sell': True, 'sector_retreat_sell': True})
            # 强化骑乘（选手圣阳7连板证据）: 前一日涨停收盘 → 当日只留止损+炸板, 禁用一切止盈/做T
            if args.ride_strong and i0 >= 1 and float(dall[i0 - 1][5]) >= limit_price(float(dall[i0 - 2][5]) if i0 >= 2 else float(dall[i0 - 1][4]), sym) - 0.005:
                params.update({'vwap_halve': False, 'second_high_sell': False,
                               'profit_take_pct': 0.0, 't_enabled': False})
            res = manage_day(day, pc, p['qty'], p['stop_px'], p['low_track'], params,
                             limit_px=limit_price(pc, sym), combo=combo)
            for f in res['fills']:
                fills.append({'date': d, 'ts': f['ts'], 'sym': sym, 'side': f['side'],
                              'qty': f['qty'], 'px': f['px'], 'kind': f['reason']})
                if f['side'] == 'sell':
                    cash += sell_net(f['px']) * f['qty']
                else:
                    cash -= buy_net(f['px']) * f['qty']
            p['qty'] = res['qty']
            close_px = float(day['close'].iloc[-1])
            if args.take_profit > 0 and p['qty'] >= 300 and close_px < limit_price(pc, sym) - 0.005:
                day_high = float(day['high'].max())
                if day_high >= p['cost_px'] * (1 + args.take_profit / 100.0):
                    third = p['qty'] // 3 // 100 * 100
                    if third >= 100:
                        p['qty'] -= third
                        cash += sell_net(close_px) * third
                        fills.append({'date': d, 'ts': f'{d} 14:57', 'sym': sym, 'side': 'sell',
                                      'qty': third, 'px': round(close_px, 3), 'kind': f'tp{args.take_profit:.0f}'})
            # 连板骑乘（选手圣阳7连板证据）：涨停收盘日不清仓/不兜底/时间止损时钟冻结
            if args.ride_limit and close_px >= limit_price(pc, sym) - 0.005:
                continue
            p['days'] += 1
            # 日线兜底: 破MA5减半(持有≥2天且有浮盈) / 破MA10清(连续3日) / 5日时间止损
            if p['qty'] > 0 and i0 >= 9 and d in dates:
                c = pd.Series([float(x[5]) for x in dall[:i0 + 1]])
                ma5 = float(c.rolling(5).mean().iloc[-1])
                ma10 = float(c.rolling(10).mean().iloc[-1])
                if close_px < ma5 and p['days'] >= 2 and close_px > p['cost_px']:
                    half = p['qty'] // 2 // 100 * 100
                    if half >= 100:
                        p['qty'] -= half
                        cash += sell_net(close_px) * half
                        fills.append({'date': d, 'ts': f'{d} 15:00', 'sym': sym, 'side': 'sell',
                                      'qty': half, 'px': round(close_px, 3), 'kind': 'ma5_halve'})
                if close_px < ma10 and i0 >= 3 and p['days'] >= 3:
                    c_arr = c.to_numpy()
                    ma10_arr = c.rolling(10).mean().to_numpy()
                    if (c_arr[-2] < ma10_arr[-2] and c_arr[-3] < ma10_arr[-3]):
                        q0 = p['qty']
                        p['qty'] = 0
                        cash += sell_net(close_px) * q0
                        fills.append({'date': d, 'ts': f'{d} 15:00', 'sym': sym, 'side': 'sell',
                                      'qty': q0, 'px': round(close_px, 3), 'kind': 'ma10_clear'})
            if p['qty'] > 0 and p['days'] >= 5:
                pnl_now = (close_px / p['cost_px'] - 1) * 100
                if pnl_now < args.time_stop_profit or args.time_stop_profit <= 0:
                    q0 = p['qty']
                    p['qty'] = 0
                    cash += sell_net(close_px) * q0
                    fills.append({'date': d, 'ts': f'{d} 15:00', 'sym': sym, 'side': 'sell',
                                  'qty': q0, 'px': round(close_px, 3), 'kind': 'time_stop'})
            if p['qty'] <= 0:
                exited.append(sym)
                last_exit[sym] = di
                del pos[sym]
        # ---- 净值
        eq = cash
        for sym, p in pos.items():
            day = min_day(sym, d)
            if day is not None:
                eq += p['qty'] * float(day['close'].iloc[-1])
        daily_eq.append({'date': d, 'equity': round(eq, 2), 'cash': round(cash, 2),
                         'n_pos': len(pos), 'temp': temp, 'cap': cap})
        if args.max_days > 0 and di + 1 >= args.max_days:
            break
        if debug_only is not None and (d in ['2026-06-05'] or len(daily_eq) == 1):
            print(d, 'eq', round(eq, 2), 'pos', {k: v['qty'] for k, v in pos.items()})

    st.close()
    # ---- 输出
    eq_df = pd.DataFrame(daily_eq)
    if eq_df.empty:
        print('无交易日记录');
        return
    eq_df['ret'] = eq_df['equity'].pct_change()
    final = eq_df['equity'].iloc[-1]
    multiple = final / START_CASH
    peak = eq_df['equity'].cummax()
    mdd = ((eq_df['equity'] - peak) / peak).min() * 100
    print(f'==== R6\' 复现 v1 结果 ====')
    print(f'期末净值 {final:.0f} = {multiple:.2f}×  (选手 6.4×, 裁决线 ≥3×)')
    print(f'最大回撤 {mdd:.1f}%  (选手至暗 -13% 级)')
    print(f'交易笔数(买卖) {len(fills)}, 持仓峰值 {eq_df["n_pos"].max()}')
    print()
    eq_df['mon'] = eq_df['date'].str[:7]
    mon = eq_df.groupby('mon').agg(起=('equity', 'first'), 末=('equity', 'last'),
                                   月收益=('equity', lambda x: (x.iloc[-1] / x.iloc[0] - 1) * 100))
    print(mon.round(1).to_string())
    print()
    print('锚点对照(选手资金曲线):')
    for d, label in [('2026-04-21', '4/21 99.8%仓'), ('2026-06-18', '6/18 69.3%'),
                     ('2026-07-06', '7/6 34.8%'), ('2026-07-17', '7/17 20%至暗'),
                     ('2026-08-13', '8/13 51.2%')]:
        row = eq_df[eq_df.date <= d]
        if not row.empty:
            eq_a = row['equity'].iloc[-1]
            print(f'  {d} {label}: 净值 {eq_a:.0f} = {eq_a / START_CASH:.2f}×')
    out_path = pathlib.Path(args.out) if args.out else BASE / 'outputs' / 'r6p_equity_v1.csv'
    eq_df.to_csv(out_path, index=False)
    fills_path = out_path.with_name(out_path.stem + '_fills.csv')
    pd.DataFrame(fills).to_csv(fills_path, index=False)
    print()
    print(f'资金曲线: {out_path}')
    print(f'成交明细: {fills_path}')


if __name__ == '__main__':
    main()