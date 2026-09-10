"""阶段一核心: 盘中感知主循环（每 1 分钟一轮）
全市场扫描 → 异动池 → 选手模式粗筛(日线形态快检) → 1m分时三引擎确认 → 触发提醒/建仓

用法: python scripts/scan_and_confirm.py [--execute]
--execute: 触发即记账买入（自主执行模式）；否则仅提醒
"""
from __future__ import annotations
import hashlib
import json
import pathlib
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'portfolio'))

import pandas as pd  # noqa: E402
from pytdx.hq import TdxHq_API  # noqa: E402

from market_scan import load_universe, fetch_batch, tencent_symbol  # noqa: E402
from core.combo_sell import industry_of  # noqa: E402
from core.intraday import detect_b_point, detect_dibu_buy, detect_pullback_buy, vwap_series  # noqa: E402
from core.tencent_minline import min_df as tencent_min_df  # noqa: E402
from core.sell import limit_price  # noqa: E402
from ledger import buy, buy_net, load, transact, record_signal_request, record_autonomous_decision  # noqa: E402
from timing_contract import FRESHNESS_SECONDS  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / 'outputs' / 'intraday'
SERVERS = [('115.238.56.198', 7709), ('115.238.90.165', 7709)]


def _load_day_plan(day: str):
    """P0.4: 读取当日计划（fail-closed——计划缺失属基础设施异常，不允许'视为全部计划外'继续买）。"""
    for src in (BASE / 'outputs' / 'plans' / f'{day}_plan.json',):
        try:
            return json.loads(src.read_text(encoding='utf-8'))
        except Exception:
            continue
    return None


def market_of(sym: str) -> int:
    if sym.startswith('900'):
        return 1
    if sym[0] in ('4', '8') or sym.startswith('92'):
        return 2
    if sym[0] in ('6', '9', '5'):
        return 1
    return 0


def is_authorized_symbol(sym: str) -> bool:
    """Shanghai/Shenzhen main boards and ChiNext only."""
    return len(sym) == 6 and sym.startswith(("600", "601", "603", "605", "000", "001", "002", "003", "300", "301"))


def pull_minutes(api, sym: str, day: str):
    if api is None:
        return tencent_min_df(sym, day)
    try:
        bars = api.get_security_bars(0, market_of(sym), sym, 0, 300)
    except Exception:
        bars = None
    if not bars:
        # 2026-09-10: TDX 不可用时降级腾讯 mkline m5 (a-stock-data 备用源速查)
        return tencent_min_df(sym, day)
    rows = []
    for b in bars:
        ts = str(b['datetime'])
        if not ts.startswith(day):
            continue
        rows.append([ts, float(b['open']), float(b['high']), float(b['low']),
                     float(b['close']), float(b['vol']), float(b['amount'])])
    if len(rows) < 5:
        return None
    return pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close', 'volume', 'amount'])


def prev_close(sym: str, day: str):
    from core.daily_src import prev_close_of
    return prev_close_of(sym, day)


def rough_screen(sym: str, chg: float, turn: float, amt: float, min_amt: float = 1.0) -> str | None:
    """选手模式粗筛（盘前形态信息 + 异动特征）"""
    # 涨停/一字买不进
    if chg >= 9.8 and turn < 3:
        return None
    # 成交额门槛（妖票活跃底线; 选手实证: 买入日成交额中位18.9亿; 自主期可用 --min-amt 调）
    if amt < min_amt:
        return None
    # 量能特征：换手 3-30%（活跃非一字）
    if turn is None or not (3 <= turn <= 30):
        return None
    # 涨幅窗口：单窗口 -1%~9.8%（引擎触发时刻涨幅≤3% 为实际买入上界；「强势5~9.8%档」未启用, 待选手证据校准）
    if not (-1 <= chg <= 9.8):
        return None
    return 'active'



def companion_health(day: str, now: datetime) -> tuple[bool, str]:
    hm = now.strftime('%H:%M')
    if hm < '09:40':
        return True, 'opening grace'
    latest = None
    for path in (BASE / 'outputs' / 'task_logs' / day).glob('*_monitor.json'):
        try:
            row = json.loads(path.read_text(encoding='utf-8-sig'))
            if row.get('date') != day or int(row.get('exit_code', -1)) != 0:
                continue
            ts = datetime.strptime(row['finished_at'], '%Y-%m-%d %H:%M:%S.%f')
            latest = max(latest, ts) if latest else ts
        except Exception:
            continue
    if latest is None or (now - latest).total_seconds() > 900:
        return False, 'monitor stale >15m'
    try:
        live = json.loads((OUT / 'pos_live.json').read_text(encoding='utf-8-sig'))
        tick = datetime.strptime(day + ' ' + live['time'], '%Y-%m-%d %H:%M:%S')
        if live.get('date') != day or (now - tick).total_seconds() > 120:
            return False, 'tick stale >2m'
    except Exception:
        return False, 'tick unavailable'
    return True, 'ok'

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute', action='store_true', help='仅 autonomous_paper 账户执行保守模拟成交')
    ap.add_argument('--force', action='store_true', help='非交易时段强制运行（测试用）')
    ap.add_argument('--min-amt', type=float, default=1.0, help='成交额门槛(亿), 默认1.0; 选手实证中位18.9亿')
    ap.add_argument('--e4-support', action='store_true', help='E4支撑位低吸确认(基板规格): 回踩MA5/MA10(±3%)+盘中涨幅≥2%站VWAP, 替代三引擎')
    ap.add_argument('--temp-ladder', action='store_true', help='温度联动(基板规格, 回测验证): 前日温度<50 本轮不触发买点(弱市降档)')
    args = ap.parse_args()
    now = datetime.now()
    day = now.strftime('%Y-%m-%d')
    # 交易时段过滤（--force 用于测试/盘后重放）
    if not args.force:
        if now.weekday() >= 5:
            print(f'[{now:%H:%M}] 周末非交易日，退出（--force 可强制）')
            return
        hm = now.strftime('%H:%M')
        if not (('09:30' <= hm <= '11:30') or ('13:00' <= hm <= '15:00')):
            print(f'[{hm}] 非交易时段，退出（--force 可强制）')
            return 0
    if not args.force:
        healthy, detail = companion_health(day, now)
        if not healthy:
            print(f'伴随监控失效，禁止新仓: {detail}', file=sys.stderr, flush=True)
            return 6
    st = load()
    held = set(st['account']['positions'].keys())
    # P0.4: 读取当日计划（fail-closed）; 盘前研究对交易的约束以计划匹配契约落地
    day_plan = _load_day_plan(day)
    if day_plan is None:
        print(f'当日计划缺失, fail-closed 禁止扫描交易: {BASE / "outputs" / "plans" / f"{day}_plan.json"}', file=sys.stderr, flush=True)
        return 7
    plan_picks = {}
    for _pi, _p in enumerate(day_plan.get('picks') or []):
        plan_picks[_p.get('sym')] = {'idx': _pi, 'pick': _p}
    try:
        plan_sha256 = hashlib.sha256((BASE / 'outputs' / 'plans' / f'{day}_plan.json').read_bytes()).hexdigest()
    except Exception:
        plan_sha256 = ''
    now_str = now.strftime('%Y-%m-%d %H:%M:%S')
    fresh_floor = now - timedelta(seconds=FRESHNESS_SECONDS)
    temp_now = None
    if args.temp_ladder:
        try:
            import pandas as _pd
            from core.sentiment import emotion_thermometer as _et
            _sf = _pd.read_csv(BASE / 'outputs' / 'sentiment_full_2026.csv')
            _r = _sf.iloc[-1]
            temp_now = _et(int(_r['zt']), int(_r['dt']) if _pd.notna(_r['dt']) else None,
                           float(_r['zhaban_rate']), int(_r['max_h']),
                           float(_r['lianban_rate']) if _pd.notna(_r['lianban_rate']) else None,
                           float(_r['median_pct']) if _pd.notna(_r['median_pct']) else None)['temp']
            print(f'[温度联动] 前日({_r['date']}) 温度={temp_now:.0f}', flush=True)
        except Exception as _e:
            print(f'温度联动加载失败: {_e}', file=sys.stderr, flush=True)
            return 3
    # 1) 全市场扫描
    syms = load_universe()
    codes = [tencent_symbol(s) for s in syms]
    allq = {}
    batch_failures = []
    for i in range(0, len(codes), 400):
        try:
            allq.update(fetch_batch(codes[i:i + 400]))
        except Exception as exc:
            batch_failures.append({'offset': i, 'error': type(exc).__name__})
    coverage = len(allq) / max(1, len(syms))
    if batch_failures or coverage < 0.90:
        print(f'行情覆盖不完整: quotes={len(allq)}/{len(syms)} coverage={coverage:.1%} failures={batch_failures}', file=sys.stderr, flush=True)
        return 4
    pool = []
    for sym, v in allq.items():
        if v['chg'] is None or not is_authorized_symbol(sym) or sym in held:
            continue
        if 'ST' in v['name'] or v['name'].startswith('*'):
            continue  # ST/退市风险股不参与（选手模式无 ST 证据）
        tag = rough_screen(sym, v['chg'], v['turn'], v['amt'] / 10000, min_amt=args.min_amt)
        if tag:
            pool.append({'sym': sym, 'name': v['name'], 'chg': v['chg'],
                         'turn': v['turn'], 'amt': v['amt']})
    pool.sort(key=lambda x: -x['chg'])
    # 1.5) 板块轮动监测（每轮聚合板块涨幅/涨停数 → board_momentum.json 供看板）
    _SW_NAMES = {}
    try:
        _SW_NAMES = json.loads((BASE / 'data' / 'sw_l2_names.json').read_text(encoding='utf-8')).get('names', {})
    except Exception:
        _SW_NAMES = {}
    from collections import defaultdict
    sec = defaultdict(lambda: {'n': 0, 'chg_sum': 0.0, 'zt': 0})
    for sym, v in allq.items():
        if v['chg'] is None or sym.startswith('900'):
            continue
        l2 = industry_of(sym, day) or 'NA'
        sec[l2]['n'] += 1
        sec[l2]['chg_sum'] += v['chg']
        if v['chg'] >= 9.8:
            sec[l2]['zt'] += 1
    momentum = [{'l2': k, 'name': _SW_NAMES.get(k, ''), 'n': v['n'], 'avg_chg': round(v['chg_sum'] / v['n'], 2), 'zt': v['zt']}
                for k, v in sec.items()]
    momentum.sort(key=lambda x: -x['avg_chg'])
    (OUT / 'board_momentum.json').write_text(
        json.dumps({'time': now.strftime('%H:%M:%S'), 'sectors': momentum[:12]},
                   ensure_ascii=False), encoding='utf-8')
    # 2) 异动池（Top 20 以内）
    pool = pool[:20]
    if not pool:
        print(f'[{now:%H:%M}] 无候选（扫描 {len(allq)} 只）')
        return 0
    # 3) 分时三引擎确认（并发拉取全部候选 1m——先限 8 只优先）
    api = TdxHq_API(heartbeat=False)
    ok = False
    for host, port in SERVERS:
        if api.connect(host, port, time_out=8):
            ok = True
            break
    if not ok:
        print('[WARN] TDX连接失败, 分时确认降级腾讯 mkline m5', file=sys.stderr)
        api = None
    triggered = []
    if args.temp_ladder and temp_now is not None and temp_now < 50:
        print(f'[温度联动] 温度{temp_now:.0f}<50 弱市降档: 本轮不触发买点(仅扫描)', flush=True)
    else:
        pass
    _temp_gate = args.temp_ladder and temp_now is not None and temp_now < 50
    for c in pool[:8]:
        df = pull_minutes(api, c['sym'], day)
        if df is None:
            continue
        pc = prev_close(c['sym'], day)
        if not pc:
            continue
        if args.e4_support:
            # E4支撑位低吸(基板规格, 选手证据): ①低点回踩MA5/MA10 ②盘中涨幅≥2%站VWAP ③不做开盘追高分钟票
            from core.daily_src import load_daily as _ld
            ddf = _ld(c['sym'])
            if ddf is None or len(ddf) < 6:
                continue
            _cl = ddf['close'].astype(float)
            ma5 = float(_cl.iloc[-5:].mean())
            ma10 = float(_cl.iloc[-10:].mean()) if len(ddf) >= 10 else ma5
            if float(df['low'].min()) > min(ma5, ma10) * 1.03:
                continue
            _vw = vwap_series(df)
            t = px = kind = None
            # P0.2: 只扫新鲜窗口内的 bar（标签 >= now-120s; 在途 bar 视为最新）
            # —— 消灭全天重放取最早信号导致的回溯成交（300468 案例）
            for _i in range(5, len(df)):
                try:
                    _dt = datetime.strptime(str(df['ts'].iloc[_i]), '%Y-%m-%d %H:%M')
                except ValueError:
                    continue
                if _dt < fresh_floor:
                    continue
                _px = float(df['close'].iloc[_i])
                if (_px / pc - 1) >= 0.02 and _px >= float(_vw.iloc[_i]):
                    t, px, kind = str(df['ts'].iloc[_i])[11:16], _px, 'e4_support'
                    break
            if t is None:
                continue
        else:
            cands = []
        # P1-2: 禁用 B1（评审: B1 负贡献 -0.33%，维持仅 B2 口径）
            for _, b in detect_b_point(df, prev_close=pc, max_pct=0.03).iterrows():
                if str(b['kind']) != 'B1':
                    cands.append((str(b['ts'])[11:16], float(b['price']), 'B' + str(b['kind'])))
            for _, b in detect_dibu_buy(df, prev_close=pc, prev5_amt=None, realtime=True).iterrows():
                cands.append((str(b['ts'])[11:16], float(b['price']), 'D'))
            for _, b in detect_pullback_buy(df, prev_close=pc, max_pct=3.0).iterrows():
                cands.append((str(b['ts'])[11:16], float(b['price']), 'P'))
            # P0.2: 三引擎候选先按 120 秒新鲜度过滤再取最早（在途 bar 视为最新）
            _fresh = []
            for _c in cands:
                try:
                    _cdt = datetime.strptime(f'{day} {_c[0]}', '%Y-%m-%d %H:%M')
                except ValueError:
                    continue
                if _cdt >= fresh_floor:
                    _fresh.append(_c)
            if not _fresh:
                continue
            t, px, kind = sorted(_fresh, key=lambda x: x[0])[0]
        vw = vwap_series(df)
        lim = limit_price(pc, c['sym'])
        if px >= lim - 0.01:
            continue
        # P1-3: broke 只对 ≤触发时刻 判定（评审: 整df否决=前视）
        t_idx = df.index[df['ts'].str[11:16] == t]
        if len(t_idx):
            up_to_t = df.loc[:t_idx[0]]
            broke = bool((up_to_t['close'] < vwap_series(up_to_t) * 0.997).any())
        else:
            broke = False
        if broke:
            print(f"[{now:%H:%M}] 候选 {c['sym']} {c['name']}: 买点{t}@{px:.2f} 但破均价线 → 否决")
            continue
        # P1-4: 建仓价=触发后下一根开盘（真实成交口径；无下一根则触发bar收盘）
        i_t = int(t_idx[0]) if len(t_idx) else len(df) - 1
        px_exec = float(df.iloc[i_t + 1]['open']) if i_t + 1 < len(df) else px
        # R8-4: 下一根一字跳空至涨停 → 买不进, 否决
        if px_exec >= lim - 0.01:
            print(f"[{now:%H:%M}] 候选 {c['sym']} {c['name']}: 下一根{px_exec:.2f}已封板, 买不进 → 否决")
            continue
        if _temp_gate:
            continue
        exec_index = i_t + 1 if i_t + 1 < len(df) else i_t
        exec_ts = str(df.iloc[exec_index]['ts'])[11:16]
        triggered.append({'sym': c['sym'], 'name': c['name'], 'ts': t, 'exec_ts': exec_ts, 'px': px_exec, 'kind': kind,
                          'chg': c['chg'], 'px_signal': px,
                          'exec_bar_volume': int(float(df.iloc[exec_index].get('volume', 0)))})
        print(f"[{now:%H:%M}] ★买点 {c['sym']} {c['name']} {t}@{px_exec:.2f} ({kind}) 涨幅{c['chg']:+.1f}%")
    if api is not None:
        api.disconnect()
    # 3.5) P0.2/P0.4: 候选快照（内容寻址）——执行裁决链的可复核输入
    candidates_snapshot = {
        'date': day, 'time': now.strftime('%H:%M:%S'),
        'pool': pool[:8], 'triggered': [dict(tg) for tg in triggered],
        'mode_flags': {'e4_support': bool(args.e4_support), 'temp_ladder': bool(args.temp_ladder),
                       'min_amt_yi': args.min_amt, 'execute': bool(args.execute)},
        'day_plan_ref': {'file': f'outputs/plans/{day}_plan.json', 'sha256': plan_sha256,
                         'picks': [p.get('sym') for p in (day_plan.get('picks') or [])]},
    }
    candidates_ref = hashlib.sha256(
        json.dumps(candidates_snapshot, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    # 4) Account-scoped execution. Human-confirmed accounts only create requests;
    # autonomous_paper accounts may execute conservative simulated fills.
    request_ids = []
    fill_ids = []
    for tg in triggered[:1]:
        # P0.4: 计划匹配契约（in_plan → pick_id; 计划外 → 显式理由, 嵌套于 plan_match）
        pick_info = plan_picks.get(tg['sym'])
        if pick_info is not None:
            _pick_id = f"plan-{day}#{pick_info['idx']}:{tg['sym']}"
            plan_match = {'in_plan': True, 'pick_id': _pick_id, 'plan_sha256': plan_sha256,
                          'off_plan_reason': None}
            plan_ref_value = _pick_id
        else:
            _rank = next((i + 1 for i, p in enumerate(pool[:8]) if p['sym'] == tg['sym']), None)
            plan_match = {'in_plan': False, 'pick_id': None, 'plan_sha256': plan_sha256,
                          'off_plan_reason': {'code': 'intraday_scan_capture',
                                              'detail': f"engine={tg['kind']};chg={tg['chg']:+.1f}%;pool_rank={_rank}"}}
            plan_ref_value = f'offplan-{day}:{tg["sym"]}'

        def _mutate(state):
            policy = state.get('policy', {})
            if args.execute:
                if policy.get('account_mode') != 'autonomous_paper' or policy.get('require_human_decision'):
                    raise ValueError('自主执行仅允许 autonomous_paper 且 require_human_decision=false')
                # Target one 45% sleeve, rounded down to board lots. The ledger
                # independently enforces cash, concentration, gross exposure and daily limits.
                equity_budget = float(state.get('start_cash', 100000.0))
                target_cash = equity_budget * float(policy.get('max_single_weight', 0.45))
                qty = int(target_cash / buy_net(float(tg['px'])) / 100) * 100
                bar_volume = int(tg.get('exec_bar_volume', 0))
                participation_cap = (bar_volume // 20 // 100) * 100  # at most 5% of next-minute volume
                qty = min(qty, participation_cap)
                if qty < 100:
                    raise ValueError('下一分钟可成交量不足一手，模拟不成交')
                # P0.4: 先登记自主决策（裁决链可重放）, 再以完整 provenance 成交
                did = record_autonomous_decision(state, {
                    'sym': tg['sym'], 'name': tg['name'],
                    'signal_ts': f'{day} {tg["ts"]}', 'signal_px': tg.get('px_signal'),
                    'rule': tg['kind'], 'candidates_ref': candidates_ref,
                    'plan_pick_ref': plan_match.get('pick_id'),
                    'off_plan_reason': plan_match.get('off_plan_reason'),
                    'risk_gates': 'ledger._validate_buy_policy',
                })
                buy(state, tg['sym'], f'{day} {tg["exec_ts"]}', float(tg['px']), qty, tg['kind'],
                    plan_ref=plan_ref_value, decision_id=did,
                    signal_ts=f'{day} {tg["ts"]}', decision_ts=now_str,
                    candidates_ref=candidates_ref, plan_match=plan_match)
                fill = state['account']['fills'][-1]
                return {'mode': 'filled', 'id': f"{fill['ts']}:{fill['sym']}:{fill['qty']}", 'qty': qty}
            existing = [r for r in state.get('signal_requests', {}).values()
                        if r.get('sym') == tg['sym'] and r.get('date') == day
                        and r.get('status') == 'pending']
            if existing:
                return {'mode': 'request', 'id': existing[0]['request_id']}
            rid = record_signal_request(state, {
                'date': day, 'sym': tg['sym'], 'name': tg['name'], 'kind': tg['kind'],
                'signal_ts': f'{day} {tg["ts"]}', 'signal_px': tg.get('px_signal'),
                'suggested_px': tg['px'], 'chg_pct': tg['chg'],
                'plan_ref': plan_ref_value, 'expires_at': f'{day} 15:00',
                'candidates_ref': candidates_ref,
                'plan_match': plan_match,
                'evidence': {'e4_support': bool(args.e4_support),
                             'temp_ladder': bool(args.temp_ladder),
                             'min_amt_yi': args.min_amt}
            })
            return {'mode': 'request', 'id': rid}
        try:
            state, result = transact(_mutate)
            if result['mode'] == 'filled':
                fill_ids.append(result['id'])
                tg['fill_qty'] = result['qty']
                print(f'  自主模拟成交: {tg["sym"]} {result["qty"]}股 @{tg["px"]:.2f}', flush=True)
            else:
                request_ids.append(result['id'])
                tg['request_id'] = result['id']
                print(f'  待人工确认: {result["id"]} {tg["sym"]} 建议价{tg["px"]:.2f}', flush=True)
        except ValueError as exc:
            tg['execution_rejected'] = str(exc)
            print(f'  模拟不成交: {tg["sym"]} {exc}', flush=True)
    fp = OUT / f'confirm_{now.strftime("%Y%m%d_%H%M")}.json'
    fp.write_text(json.dumps({'date': day, 'time': now.strftime('%H:%M:%S'), 'pool': pool[:8],
                              'triggered': triggered, 'request_ids': request_ids, 'fill_ids': fill_ids,
                              'candidates_ref': candidates_ref,
                              'candidates_snapshot': candidates_snapshot}, ensure_ascii=False), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
