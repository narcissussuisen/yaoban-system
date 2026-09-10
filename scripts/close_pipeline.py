"""收盘自动流水线（操盘手日循环收口）：回填→全天决策重建→记账→看板→归因→次日预案

流程:
  1. TDX 回填当日日线（持仓+备选+候选池全市场快照——增量用 fill 现有机制）
  2. 拉当日全天分钟（持仓+备选）
  3. 全天决策重建（上午段≤11:30 + 下午段≤15:00 分段执行, 防前视）
  4. 记账(ledger) + 收盘估值 + build_board + 归因写入
用法: python scripts/close_pipeline.py
"""
from __future__ import annotations
import json
import pathlib
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'portfolio'))

import pandas as pd  # noqa: E402
from pytdx.hq import TdxHq_API  # noqa: E402

from core.intraday import detect_b_point, detect_dibu_buy, detect_pullback_buy, vwap_series  # noqa: E402
from core.tencent_minline import min_df as tencent_min_df, quote as tencent_quote  # noqa: E402
from core.sell import limit_price  # noqa: E402
from ledger import load, save, equity, record_review  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
INTRADAY = BASE / 'outputs' / 'intraday'
SERVERS = [('115.238.56.198', 7709), ('115.238.90.165', 7709)]


def market_of(sym: str) -> int:
    if sym.startswith('900'):
        return 1
    if sym[0] in ('4', '8') or sym.startswith('92'):
        return 2
    if sym[0] in ('6', '9', '5'):
        return 1
    return 0


def prev_close(sym: str, day: str):
    from core.daily_src import prev_close_of
    return prev_close_of(sym, day)


def pull_day_minutes(sym: str, day: str, api) -> pd.DataFrame | None:
    if api is None:
        return tencent_min_df(sym, day)
    try:
        bars = api.get_security_bars(0, market_of(sym), sym, 0, 800)
    except Exception:
        bars = None
    if not bars:
        # 2026-09-10: TDX 挂时收盘估值降级腾讯 mkline m5 (a-stock-data 备用源速查)
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


def build_close_decision(decisions: dict, st: dict, eq: float, mark: dict) -> dict:
    """构建 close_decision 扩展 schema（终审三轮方案A，2026-09-02 裁定）。

    在原 date/buys/sells/t/notes 之上追加 G1 机械核对所需字段:
    run_id / generated_at / ledger_revision / equity / positions{qty, close_px, market_value}。
    必须在 save(st) 之后调用, 使 ledger_revision 指向已含当日净值点的账本版本(CAS)。

    复核四轮裁定（2026-09-02 19:48 用户裁定: 共享 run_id）——scheduled-run 归属升级为精确关联:
    launch.ps1 生成 run_id 并以 YAOBAN_RUN_ID 环境变量注入, task log 与本产物双写同值,
    G1 机械核对 task_log.run_id == close_decision.run_id; 无 env 的手动运行回退自造
    close-* ID（不得充当 scheduled-run 证据, 手动补跑只追加记录, 见计划 §2.5）。
    """
    import os as _os
    import uuid as _uuid
    day = decisions['date']
    positions = {}
    for sym, pos in st['account']['positions'].items():
        px = float(mark[sym])
        qty = int(pos.get('qty', 0))
        positions[sym] = {'qty': qty, 'close_px': px, 'market_value': round(qty * px, 2)}
    run_id = _os.environ.get('YAOBAN_RUN_ID') or f"close-{day}-{datetime.now():%H%M%S}-{_uuid.uuid4().hex[:6]}"
    doc = dict(decisions)
    doc.update({
        'run_id': run_id,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'ledger_revision': int(st.get('_revision', 0)),
        'equity': round(float(eq), 2),
        'positions': positions,
    })
    return doc


def main():
    import argparse as _ap
    _parser = _ap.ArgumentParser()
    _parser.add_argument('--execute', action='store_true', help='执行建仓（默认灰度=只记录决策）')
    _parser.add_argument('--dry-run', action='store_true', help='只做 TDX 连接与交易日探测，不写任何文件')
    args = _parser.parse_args()
    now = datetime.now()
    day = now.strftime('%Y-%m-%d')
    st = load()
    pos_syms = list(st['account']['positions'].keys())
    plan = st.get('plans', {}).get(day, {})
    watch_syms = [p['sym'] for p in plan.get('picks', [])] if plan else []
    syms = sorted(set(pos_syms + watch_syms))
    if not syms:
        print('无持仓无备选，跳过')
        return
    print(f'收盘流水线 {day}: 标的 {syms}', flush=True)
    api = TdxHq_API(heartbeat=False)
    ok = False
    for host, port in SERVERS:
        if api.connect(host, port, time_out=8):
            ok = True
            break
    if not ok:
        print('ERROR: TDX连接失败', file=sys.stderr); return 2
    # ---- 0) 非交易日守卫（周末/节假日无当日分钟 → 跳过, 避免空转与重复净值）
    tdx_degraded = False
    try:
        probe = api.get_security_bars(0, 1, '600000', 0, 5)
        probe_day = [str(b['datetime'])[:10] for b in probe] if probe else []
        if not probe_day:
            # 2026-09-10: TDX 停供时用腾讯 mkline 判定当日数据就绪(a-stock-data 备用源速查);
            # 9/10 收盘链 rc=3 即为本门拦截(探测为空被误判非交易日)
            _fb = tencent_min_df('600000', day, 'm5')
            if _fb is not None and len(_fb):
                probe_day = [str(_fb['ts'].iloc[-1])[:10]]
                tdx_degraded = True
                print('WARN: TDX 探测为空, 腾讯备胎确认当日数据就绪', file=sys.stderr)
        if args.dry_run:
            print(f'DRY-RUN: TDX连接正常, 600000最新分钟日={probe_day[-1] if probe_day else "无"}')
            api.disconnect(); return 0
        if day not in probe_day:
            print(f'ERROR: {day} 非交易日或数据未就绪(600000 最新={probe_day[-1] if probe_day else "无"})', file=sys.stderr)
            api.disconnect(); return 3
    except Exception as e:
        print(f'ERROR: {day} 交易日探测失败: {e}', file=sys.stderr); return 4
    # ---- 1) 全天决策重建（分上午/下午两段，严格防前视）
    decisions = {'date': day, 'buys': [], 'sells': [], 't': [], 'notes': []}
    for sym in syms:
        df = pull_day_minutes(sym, day, api)
        if df is None:
            decisions['notes'].append(f'{sym}: 无当日分钟')
            continue
        pc = prev_close(sym, day)
        if not pc:
            continue
        vw = vwap_series(df)
        lim = limit_price(pc, sym)
        pos = st['account']['positions'].get(sym)
        if pos:
            stop_px = pos.get('stop_px')
            am_low = float(df['low'].min())
            last_close = float(df['close'].iloc[-1])
            last_ts = str(df['ts'].iloc[-1])[11:16]
            if stop_px and am_low <= stop_px:
                decisions['sells'].append({'sym': sym, 'ts': last_ts, 'reason': '止损', 'px': last_close, 'qty': pos['qty']})
            elif lim and float(df['high'].max()) >= lim - 0.01 and last_close < lim * 0.995:
                decisions['sells'].append({'sym': sym, 'ts': last_ts, 'reason': '炸板卖出', 'px': last_close, 'qty': pos['qty']})
        else:
            # 分段：上午触发用 ≤11:30；下午触发用 ≤15:00（若上午已触发不重复）
            am = df[df['ts'].str[11:16] <= '11:30']
            pm = df[df['ts'].str[11:16] > '11:30']
            fired = False
            for seg, segname in ((am, 'AM'), (pm, 'PM')):
                if fired or len(seg) < 3:
                    continue
                cands = []
                for _, b in detect_b_point(seg, prev_close=pc, max_pct=0.03).iterrows():
                    cands.append((str(b['ts'])[11:16], float(b['price']), 'B'))
                for _, b in detect_dibu_buy(seg, prev_close=pc, prev5_amt=None, realtime=True).iterrows():
                    cands.append((str(b['ts'])[11:16], float(b['price']), 'D'))
                for _, b in detect_pullback_buy(seg, prev_close=pc, max_pct=3.0).iterrows():
                    cands.append((str(b['ts'])[11:16], float(b['price']), 'P'))
                if not cands:
                    continue
                t, px, kind = sorted(cands, key=lambda x: x[0])[0]
                if px >= lim - 0.01:
                    continue
                decisions['buys'].append({'sym': sym, 'ts': t, 'px': px, 'kind': kind, 'seg': segname})
                fired = True
    api.disconnect()
    # ---- 2) 盘后仅审计，不执行订单；盘中新仓=decision_cli，盘中风险: tick_monitor
    if args.execute:
        print('ERROR: close_pipeline --execute 已禁用，收盘流水线不得重复执行订单', file=sys.stderr)
        return 5
    for bd in decisions['buys']:
        print(f'  (灰度) 决策: 买入 {bd["sym"]} @{bd["px"]} ({bd["seg"]}) —— 未执行', flush=True)
    for sd in decisions['sells']:
        print(f'  (灰度) 审计卖出 {sd["sym"]} {sd["qty"]}股 @{sd["px"]} ({sd["reason"]}) —— 仅审计未执行', flush=True)
    # ---- 3) 收盘估值 + 归因（当日收盘优先用 TDX 1m 权威价——daily_rebuilt 收盘时尚未刷新当日）
    from core.daily_src import load_daily as _ld
    api2 = TdxHq_API(heartbeat=False)
    ok2 = False
    for host, port in SERVERS:
        if api2.connect(host, port, time_out=8):
            ok2 = True
            break
    mark = {}
    for sym in list(st['account']['positions'].keys()):
        mark[sym] = None
        if ok2:
            df = pull_day_minutes(sym, day, api2)
            if df is not None and len(df) and str(df['ts'].iloc[-1]).startswith(day):
                mark[sym] = float(df['close'].iloc[-1])
        if tdx_degraded:
            # 2026-09-10: TDX 降级时 mkline m5 末根可能是 14:55(滞后 1 根), 收盘估值改用实时报价
            # (收盘后 qt 快照即当日收盘价); 否则 净值守恒 会与独立价源对不上(9/10 实测差 125 元)
            _q = tencent_quote([sym])
            if _q.get(sym):
                mark[sym] = float(_q[sym])
        if mark.get(sym) is None:
            ddf = _ld(sym)
            if ddf is not None and len(ddf):
                mark[sym] = float(ddf['close'].iloc[-1])
        if mark.get(sym) is None:
            try:
                df = pd.read_parquet(pathlib.Path('F:/WorkBuddyItem/a股level2/daily') / f'{sym}.parquet')
                df['date'] = df['date'].astype(str)
                df = df.sort_values('date')
                if len(df):
                    mark[sym] = float(df['close'].iloc[-1])
            except Exception:
                mark[sym] = None
    if ok2:
        api2.disconnect()
    missing_marks = [s for s in st['account']['positions'] if not mark.get(s)]
    if missing_marks:
        print(f'ERROR: 收盘估值缺少持仓价格: {missing_marks}', file=sys.stderr)
        return 6
    eq = equity(st, day, mark)
    # 回撤熔断（职业纪律: 单日回撤≤5% → 次日仓位降档）
    circuit_break = None
    curve = st['account']['equity_curve']
    risk_state = st.setdefault('risk_state', {})
    peak_equity = max([float(x.get('equity', 0)) for x in curve] + [float(st.get('start_cash', 100000.0))])
    total_dd = (eq / peak_equity - 1) * 100 if peak_equity else 0.0
    risk_state['peak_equity'] = round(peak_equity, 2)
    risk_state['drawdown_pct'] = round(total_dd, 2)
    pause_at = float(st.get('policy', {}).get('pause_drawdown_pct', 10.0))
    terminate_at = float(st.get('policy', {}).get('terminate_drawdown_pct', 15.0))
    if total_dd <= -terminate_at:
        risk_state.update({'terminated': True, 'paused': True, 'position_multiplier': 0.0,
                           'source_date': day, 'reason': 'portfolio_drawdown_terminate'})
        circuit_break = {'drawdown_pct': round(total_dd, 2), 'action': '终止本轮模拟并进入根因审计', 'date': day}
        print(f'组合回撤 {total_dd:.2f}% 达终止线，本轮停止新仓', flush=True)
    elif total_dd <= -pause_at:
        risk_state.update({'paused': True, 'position_multiplier': 0.0,
                           'source_date': day, 'reason': 'portfolio_drawdown_pause'})
        circuit_break = {'drawdown_pct': round(total_dd, 2), 'action': '暂停新仓并进入审查', 'date': day}
        print(f'组合回撤 {total_dd:.2f}% 达暂停线，停止新仓', flush=True)
    elif len(curve) >= 2:
        prev_eq = curve[-2]['equity']
        day_dd = (eq / prev_eq - 1) * 100 if prev_eq else 0
        if day_dd <= -float(st.get('policy', {}).get('max_daily_loss_pct', 5.0)):
            circuit_break = {'day_dd': round(day_dd, 2),
                             'action': '次日仓位降档50%(回撤熔断)', 'date': day}
            risk_state['position_multiplier'] = 0.5
            risk_state['source_date'] = day
            risk_state['blocked_date'] = day
            risk_state['reason'] = 'single_day_drawdown'
            print(f'单日回撤 {day_dd:.2f}% 达熔断线', flush=True)
        elif risk_state.get('position_multiplier', 1.0) < 1.0 and risk_state.get('source_date', '') < day and not risk_state.get('paused'):
            st['risk_state'] = {'position_multiplier': 1.0, 'source_date': day, 'reason': 'recovered_after_one_trade_day',
                                'peak_equity': round(peak_equity, 2), 'drawdown_pct': round(total_dd, 2)}
    review = {
        'buys': decisions['buys'], 'sells': decisions['sells'], 'notes': decisions['notes'],
        'close': {s: mark.get(s) for s in st['account']['positions']},
        'equity': round(eq, 2),
        'circuit_break': circuit_break,
    }
    record_review(st, day, review)
    save(st)
    close_doc = build_close_decision(decisions, st, eq, mark)
    (INTRADAY / f'close_decision_{day}.json').write_text(
        json.dumps(close_doc, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'收盘净值 {eq:.2f} 持仓 {len(st["account"]["positions"])} 只', flush=True)
    # ---- 4) 交易员日报（阶段二: 自省层）——输出用文件重定向: 任务环境管道捕获EPERM, 参考 premarket 修复)
    with open(INTRADAY / f'trader_daily_{day}.log', 'w', encoding='utf-8') as _f:
        _r4 = subprocess.run([sys.executable, str(BASE / 'scripts' / 'trader_daily.py'), '--date', day],
                            cwd=str(BASE), stdout=_f, stderr=subprocess.STDOUT)
    print(f'交易员日报已生成 rc={_r4.returncode}', flush=True)
    # ---- 5) 看板
    with open(INTRADAY / f'build_board_{day}.log', 'w', encoding='utf-8') as _f:
        _r5 = subprocess.run([sys.executable, str(BASE / 'scripts' / 'build_board.py'), '--fills-day', day],
                            cwd=str(BASE), stdout=_f, stderr=subprocess.STDOUT)
    print(f'看板子任务 rc={_r5.returncode}', flush=True)
    if _r4.returncode != 0 or _r5.returncode != 0:
        print(f'ERROR: 子任务失败: trader_daily={_r4.returncode} build_board={_r5.returncode}', file=sys.stderr)
        return 7
    return 0


if __name__ == '__main__':
    sys.exit(main())
