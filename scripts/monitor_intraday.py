"""盘中主动监控（操盘手之眼）：拉取持仓+备选当日分时 → 触发评估 → 写提醒

纪律：决策只用当前时刻之前的数据；触发后去重（同标的同信号当日只记一次）。
触发写 outputs/intraday/alerts_YYYYMMDD.json（看板读取）+ 标准输出日志。

用法: python scripts/monitor_intraday.py
（注册为 Windows 计划任务每 5 分钟运行，脚本自行过滤交易时段）
"""
from __future__ import annotations
import json
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'portfolio'))

import pandas as pd  # noqa: E402
from pytdx.hq import TdxHq_API  # noqa: E402

from core.intraday import detect_b_point, detect_dibu_buy, detect_pullback_buy, vwap_series  # noqa: E402
from core.tencent_minline import min_df as tencent_min_df  # noqa: E402
from core.sell import limit_price  # noqa: E402
from ledger import load as load_ledger  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
ALERT_DIR = BASE / 'outputs' / 'intraday'
SERVERS = [('117.34.114.13',7709),('117.34.114.14',7709),('117.34.114.15',7709),('117.34.114.16',7709),
 ('117.34.114.17',7709),('117.34.114.18',7709),('117.34.114.20',7709),('117.34.114.27',7709),
 ('115.238.56.198',7709),('115.238.90.165',7709)]
_NAMES = None


def name_of(sym: str) -> str:
    global _NAMES
    if _NAMES is None:
        try:
            _NAMES = json.loads((BASE / 'data' / 'stock_names_full.json').read_text(encoding='utf-8'))
        except Exception:
            _NAMES = {}
    return _NAMES.get(sym, '')


def market_of(sym: str) -> int:
    if sym.startswith('900'):
        return 1
    if sym[0] in ('4', '8') or sym.startswith('92'):
        return 2
    if sym[0] in ('6', '9', '5'):
        return 1
    return 0


def in_trading_window(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    hm = now.strftime('%H:%M')
    return ('09:30' <= hm <= '11:30') or ('13:00' <= hm <= '15:00')


def prev_close(sym: str, day: str):
    fp = pathlib.Path('F:/WorkBuddyItem/a股level2/daily') / f'{sym}.parquet'
    if fp.exists():
        try:
            df = pd.read_parquet(fp)
            df['date'] = df['date'].astype(str)
            sub = df[df['date'] < day]
            if len(sub):
                return float(sub.sort_values('date')['close'].iloc[-1])
        except Exception:
            pass
    return None


def main():
    now = datetime.now()
    if not in_trading_window(now):
        print(f'[{now:%H:%M}] 非交易时段，跳过')
        return 0
    day = now.strftime('%Y-%m-%d')
    st = load_ledger()
    pos_syms = list(st['account']['positions'].keys())
    plan_path = BASE / 'outputs' / 'plans' / f'{day}_plan.json'
    try:
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
    except Exception as exc:
        print(f'当日计划不可用: {type(exc).__name__}', file=sys.stderr)
        return 2
    watch_syms = [p['sym'] for p in plan.get('picks', [])]
    syms = sorted(set(pos_syms + watch_syms))
    if not syms:
        print(f'[{now:%H:%M}] 无持仓无备选')
        return 0
    api = TdxHq_API(heartbeat=False)
    ok = False
    for host, port in SERVERS:
        if api.connect(host, port, time_out=8):
            ok = True
            break
    if not ok:
        print('[WARN] TDX连接失败, 监控降级腾讯 mkline m5', file=sys.stderr)
        api = None
    alerts = []
    unavailable = []
    for sym in syms:
        bars = None
        if api is not None:
            try:
                bars = api.get_security_bars(0, market_of(sym), sym, 0, 300)
            except Exception:
                bars = None
        rows = []
        if bars:
            for b in bars:
                ts = str(b['datetime'])
                if not ts.startswith(day):
                    continue
                rows.append([ts, float(b['open']), float(b['high']), float(b['low']),
                             float(b['close']), float(b['vol']), float(b['amount'])])
        if not rows:
            # 2026-09-10: TDX 不可用时降级腾讯 mkline m5 (a-stock-data 备用源速查)
            fb = tencent_min_df(sym, day)
            if fb is None:
                unavailable.append(sym)
                continue
            df = fb
            rows_len = len(df)
        else:
            df = pd.DataFrame(rows, columns=['ts', 'open', 'high', 'low', 'close', 'volume', 'amount'])
            rows_len = len(df)
        if rows_len < 5:
            # 开盘首根 5m K 线 09:35 生成, 凑齐 5 根需到 09:55; 此前属累积期, 不算数据缺失
            # (2026-09-01 修复: 原豁免仅到 09:35, 导致 09:35-09:55 误报 unavailable
            #  → scan companion_health 判 monitor stale → 禁止新仓的连锁误伤)
            if now.strftime('%H:%M') < '09:55':
                continue  # intraday bars are still accumulating
            unavailable.append(sym)
            continue
        pc = prev_close(sym, day)
        if not pc:
            unavailable.append(sym)
            continue
        vw = vwap_series(df)
        last_close = float(df['close'].iloc[-1])
        last_ts = str(df['ts'].iloc[-1])[11:16]
        am_high = float(df['high'].max())
        am_low = float(df['low'].min())
        lim = limit_price(pc, sym)
        pos = st['account']['positions'].get(sym)
        if pos:
            stop_px = pos.get('stop_px')
            if stop_px and am_low <= stop_px:
                alerts.append({'sym': sym, 'name': name_of(sym), 'ts': last_ts, 'event_kind': 'risk',
                               'event_label': '持仓风险规则事件', 'rule_id': 'risk_floor', 'px': last_close})
            elif lim and am_high >= lim - 0.01 and last_close < lim * 0.995:
                alerts.append({'sym': sym, 'name': name_of(sym), 'ts': last_ts, 'event_kind': 'risk',
                               'event_label': '持仓风险规则事件', 'rule_id': 'limit_reopen', 'px': last_close})
            elif am_high >= pc * 1.07 and last_close < vw.iloc[-1] * 0.997:
                alerts.append({'sym': sym, 'name': name_of(sym), 'ts': last_ts, 'event_kind': 'risk',
                               'event_label': '持仓风险规则事件', 'rule_id': 'vwap_break', 'px': last_close})
            # 做T条件提示
            day_range = (am_high / max(am_low, 1e-9) - 1) * 100
            if day_range >= 2.0 and last_close < vw.iloc[-1] * 0.985:
                alerts.append({'sym': sym, 'name': name_of(sym), 'ts': last_ts, 'event_kind': 'volatility',
                               'event_label': '盘中波动规则事件', 'rule_id': 'range_vwap', 'px': last_close})
        else:
            cands = []
            for _, b in detect_b_point(df, prev_close=pc, max_pct=0.03).iterrows():
                cands.append((str(b['ts'])[11:16], float(b['price']), 'B'))
            for _, b in detect_dibu_buy(df, prev_close=pc, prev5_amt=None, realtime=True).iterrows():
                cands.append((str(b['ts'])[11:16], float(b['price']), 'D'))
            for _, b in detect_pullback_buy(df, prev_close=pc, max_pct=3.0).iterrows():
                cands.append((str(b['ts'])[11:16], float(b['price']), 'P'))
            if cands:
                t, px, kind = sorted(cands, key=lambda x: x[0])[0]
                broke = bool((df['close'] < vw * 0.997).any())
                if not broke and px < lim - 0.01:
                    alerts.append({'sym': sym, 'name': name_of(sym), 'ts': t, 'event_kind': 'candidate',
                                   'event_label': '候选信号规则事件', 'rule_id': f'candidate_{kind.lower()}', 'px': px})
                elif broke:
                    alerts.append({'sym': sym, 'name': name_of(sym), 'ts': t, 'event_kind': 'candidate',
                                   'event_label': '候选信号规则事件', 'rule_id': 'candidate_invalidated', 'px': px})
    if api is not None:
        api.disconnect()
    if unavailable:
        print(f'监控数据不完整: unavailable={sorted(set(unavailable))}', file=sys.stderr)
        return 4
    # 去重+落盘
    fp = ALERT_DIR / f'alerts_{day.replace("-", "")}.json'
    old = []
    if fp.exists():
        try:
            old = json.loads(fp.read_text(encoding='utf-8'))
        except Exception:
            old = []
    def event_key(a):
        return (a.get('sym'), a.get('event_kind') or a.get('type'), a.get('rule_id') or a.get('reason'))
    keys = {event_key(a) for a in old if isinstance(a, dict)}
    fresh = [a for a in alerts if event_key(a) not in keys]
    merged = old + fresh
    fp.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding='utf-8')
    for a in fresh:
        print(f"[{now:%H:%M}] {a['sym']} {a['event_label']} @{a['px']}", flush=True)
    if not fresh:
        print(f'[{now:%H:%M}] 监控 {len(syms)} 只：无新触发（已触发 {len(old)} 条）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
