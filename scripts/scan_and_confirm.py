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
from core.intraday import (detect_b_point, detect_dibu_buy, detect_pullback_buy, vwap_series,  # noqa: E402
                           KLINE_1MIN, TX_PERIOD_1MIN)
from core.tencent_minline import min_df as tencent_min_df  # noqa: E402
from core.sell import limit_price  # noqa: E402
from ledger import buy, buy_net, cost_equity, load, transact, record_signal_request, record_autonomous_decision  # noqa: E402
from decision_chain import intraday_veto  # noqa: E402  ⭐ R4.1 盘中 D6 否决权
from timing_contract import FRESHNESS_SECONDS  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / 'outputs' / 'intraday'
SERVERS = [('59.36.5.11',7709),('117.34.114.18',7709),('117.34.114.13',7709),('117.34.114.27',7709),
 ('117.34.114.16',7709),('117.34.114.20',7709),('117.34.114.17',7709),('117.34.114.14',7709),
 ('117.34.114.15',7709),('115.238.56.198',7709)]

# ⭐ e4_support 的买入涨幅上界 = 选手显式硬约束
#   依据：`config/parameters.toml` rule#8「买点-当日涨幅上限 = ≤3%」，confidence=实锤
#        （出处 v12@01:07 / v19@01:13；口径复述见 05-07《公开选漂战法秘籍》「只低吸不追高打板」）
#   选手实盘反证：2026-09-11 依顿电子 +2.71%、2026-09-14 博敏电子 +2.61% —— 连续两笔均贴上限而不过。
#   ⚠️ 2026-09-14 修复：e4_support 原先写作 `(_px / pc - 1) >= 0.02`，**只有下界、无上界**，
#      导致首个自主交易日 10:00 买在 +9.3%（001896，pool_rank=1），上界实际退化为
#      `rough_screen` 的 9.8%。该值把 e4_support 拉回与选手一致的口径。
#      「强势 5~9.8% 档」仍未启用（无选手证据支持）。
E4_MAX_PCT = 0.03
# ⚠️ 浮点边界容差（2026-09-14，由 `tests/test_scan_freshness.py` 的边界 fixture 抓出）：
#   `10.3 / 10.0 - 1` 在 IEEE-754 下 = 0.030000000000000027 > 0.03 ⇒ **恰好 +3.00% 会被误判为越界**。
#   选手口径「涨幅 ≤3%」是**闭区间**，故上下界各放 1e-9 的容差。
#   实测：(10.3/10-1) = 0.030000000000000027（拒）、含容差后通过；(10.2/10-1) = 0.020000000000000018（原本靠运气通过）。
PCT_EPS = 1e-9
# 时间新鲜度的**上界**容差（秒）：容忍在途 bar 的收尾标注，同时挡住「未来标签 bar」。
# 1 分钟 bar 的收尾标注最多超前 60s，故取 60；午餐时段被标成 13:00 的 bar 会被此上界挡掉。
FUTURE_BAR_TOL_SECONDS = 60


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
    """当日**1 分钟**分时。

    ⚠️ 2026-09-14 修复（P0-A，生产/回测同源）：原先写 `get_security_bars(0, …)`，
    而 pytdx **category=0 是 5 分钟**（1 分钟是 7/8）⇒ 生产跑 5 分钟、回测
    （`QFQStore.get_minute` freq 硬编码 `1m`）跑 1 分钟，**不同源**。
    与 `docs/TRADER_ROADMAP_v2.md §1.2`「确认队列标的拉 **1m** 分时」的约定不符，
    也与 `participation_cap` 注释「5% of next-**minute** volume」的口径不符。
    同一缺陷 2026-09-11 已在 `tick_monitor` 修过一次（`KLINE_1MIN`），当时未推广。
    """
    if api is None:
        return tencent_min_df(sym, day, TX_PERIOD_1MIN)
    try:
        bars = api.get_security_bars(KLINE_1MIN, market_of(sym), sym, 0, 300)
    except Exception:
        bars = None
    if not bars:
        # 2026-09-10: TDX 不可用时降级腾讯 mkline (a-stock-data 备用源速查)
        # 2026-09-14: 周期显式传 m1（原先走 `min_df` 默认值 m5 ⇒ 备胎源也是 5 分钟，两源都错）
        return tencent_min_df(sym, day, TX_PERIOD_1MIN)
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
    # 涨幅窗口（**候选观察窗，不是买入窗**）：单窗口 -1%~9.8%。
    #   ⚠️ 2026-09-14 修正此注释：原文写作「引擎触发时刻涨幅≤3% 为实际买入上界」，但当时
    #      `e4_support` 分支并无上界（详见 E4_MAX_PCT 处注释），注释与代码不符。
    #   事实是：**买入上界由各引擎自持** —— detect_b_point(max_pct=0.03) /
    #   detect_pullback_buy(max_pct=3.0) / e4_support(E4_MAX_PCT=0.03, rule#8)。
    #   本函数只负责「哪些票进入观察池」，选手候选池在 09:35-09:47 发布时其标的本就
    #   已涨 4.8%~8.8% ⇒ 池窗必须宽于买入窗，二者不可混为一谈。
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
    ap.add_argument('--e4-support', action='store_true', help='E4支撑位低吸确认(基板规格): 回踩MA5/MA10(±3%%)+盘中涨幅≥2%%站VWAP, 替代三引擎')
    ap.add_argument('--temp-ladder', action='store_true', help='温度联动: 读前日温度并标记弱市(2026-09-13 起仅记录, 不再阻断买点)')
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
    # ⚠️ 2026-09-14 修复（P0-B）：原先只有下界（`if _dt < fresh_floor: continue`），
    #   **没有上界** ⇒ **标签时间晚于当前时刻的 bar 永远被视为「新鲜」**。
    #   实测：午餐时段（11:50）取 301071，最后一根 bar 被数据源标成 **13:00** 且带真实成交量
    #   ⇒ 11:30 那一轮 scan 产出的 6 条 triggered 全部是 `ts=13:00 / exec_ts=13:00`，
    #   把 ~11:30 的判定记成 13:00（幸好 R0.3 时序契约会拒掉，未产生成交，但留痕误导复盘）。
    #   上界取「当前 + 1 根 bar」：容忍在途 bar 的收尾标注，同时挡住跨会话的未来标签。
    fresh_ceil = now + timedelta(seconds=FUTURE_BAR_TOL_SECONDS)
    temp_now = None
    if args.temp_ladder:
        try:
            import pandas as _pd
            from core.sentiment import emotion_thermometer as _et
            _sf = _pd.read_csv(BASE / 'outputs' / 'sentiment_full_2026.csv')
            _r = _sf.iloc[-1]
            _dt7 = _r.get('dt7_count')     # R2.5 新增列；旧 CSV 可能没有 → 用 .get 容错
            temp_now = _et(int(_r['zt']), int(_r['dt']) if _pd.notna(_r['dt']) else None,
                           float(_r['zhaban_rate']), int(_r['max_h']),
                           float(_r['lianban_rate']) if _pd.notna(_r['lianban_rate']) else None,
                           float(_r['median_pct']) if _pd.notna(_r['median_pct']) else None,
                           int(_dt7) if (_dt7 is not None and _pd.notna(_dt7)) else None)['temp']
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
    # R4.1：触发检测阶段的分钟序列**旁路缓存**，供否决阶段复用（不放进 tg —— 它要进 json 快照）。
    #   ⚠️ 必须在 `api.disconnect()` 之前填充，否则否决阶段无法再取数。
    _min_df = {}
    # ⭐ 2026-09-13 修正（依据 9/11 实盘行为，用户裁定）：弱市**不再硬停买点**。
    #   证据：9/11 选手实际**仓位 38.6%、持仓 3 只**（杭电 +6.259%），当日做的是「**调仓换股**」
    #   ——清掉破 MA10 的诺普信、留下趋势未破的；他的「可以选择观望」是对**打板接力**说的，不是空仓。
    #   ⇒ **弱市 ≠ 不操作；弱市 = 换股 + 降仓**。
    #   ⚠️ 「降档」本应由市况分档（牛 50-70 / 震 30-50 / 熊 ≤20）承担，但 SOP 与口述**都未给出
    #      「temp → 档位」的切分阈值** ⇒ 不擅自引入未标定参数。此处**只记录弱市标记**，
    #      仓位仍由 ledger policy（`max_single_weight`）约束；「弱市降档」登记为**待标定项**。
    #   ⚠️ 另：本闸读的是**前一交易日**收盘情绪（`sentiment_full_2026.csv` 末行 = T−1；
    #      周末后失真）。用户裁定的正确顺序是「① 早盘外盘 → ② 早盘集合竞价 → ③ 前日收盘」，
    #      当前只实现了 ③ ⇒ 完整口径待补（见 docs / status.json 的已知偏离）。
    _temp_gate = bool(args.temp_ladder and temp_now is not None and temp_now < 50)
    if _temp_gate:
        print(f'[温度联动] 前日温度{temp_now:.0f}<50 → 弱市标记（仅记录；不再阻断买点，降档待标定）',
              flush=True)
    elif temp_now is not None:
        print(f'[温度联动] 前日温度{temp_now:.0f}>=50 → 常规档', flush=True)
    for c in pool[:8]:
        df = pull_minutes(api, c['sym'], day)
        if df is None:
            continue
        _min_df[c['sym']] = df   # R4.1 否决阶段复用（api 稍后会 disconnect）
        pc = prev_close(c['sym'], day)
        if not pc:
            continue
        if args.e4_support:
            # E4 支撑位低吸（选手证据口径）：
            #   ① 当日低点回踩 MA5/MA10（以低点贴合度为判据）
            #   ② 触发时刻涨幅 ∈ [2%, E4_MAX_PCT=3%] —— 下界来自引擎设计，上界来自 rule#8（实锤）
            #   ③ 触发时刻站上当日均价线 VWAP（选手：「分时在均价线下方坚决不买」）
            #   ⚠️ 2026-09-14：②原先只有下界，已补上界（首笔实盘 +9.3% 的根因）。
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
                if _dt < fresh_floor or _dt > fresh_ceil:
                    continue
                _px = float(df['close'].iloc[_i])
                _chg = _px / pc - 1
                if 0.02 - PCT_EPS <= _chg <= E4_MAX_PCT + PCT_EPS and _px >= float(_vw.iloc[_i]):
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
                if fresh_floor <= _cdt <= fresh_ceil:
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
        exec_index = i_t + 1 if i_t + 1 < len(df) else i_t
        exec_ts = str(df.iloc[exec_index]['ts'])[11:16]
        triggered.append({'sym': c['sym'], 'name': c['name'], 'ts': t, 'exec_ts': exec_ts, 'px': px_exec, 'kind': kind,
                          'chg': c['chg'], 'px_signal': px,
                          # 弱市标记（2026-09-13 起仅记录、不阻断；供 candidates_snapshot 与复盘核对）
                          'weak_market': _temp_gate,
                          'exec_bar_volume': int(float(df.iloc[exec_index].get('volume', 0)))})
        _wm = ' [弱市]' if _temp_gate else ''
        print(f"[{now:%H:%M}] ★买点 {c['sym']} {c['name']} {t}@{px_exec:.2f} ({kind}) 涨幅{c['chg']:+.1f}%{_wm}")
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
    rejected = []
    # ⚠️ 2026-09-14 修复（P0-C，用户裁定「这个错误太严重了，需要处理」）：
    #   原为 `for tg in triggered[:1]:` —— **每轮只尝试「池中涨幅最高」的第 1 条候选**，
    #   同一轮内其余买点**既不执行、也不留任何 rejection 记录**（`execution_rejected` /
    #   `request_id` / `fill_qty` 全为 null）⇒ 复盘时**无法区分「被规则拒了」与「根本没尝试」**。
    #   最危险的失效模式：若第 1 条因**与该轮无关的原因**失败（如 `qty<100 下一分钟可成交量不足一手`），
    #   系统会认为「这轮没机会」，而第 2~6 条明明可能买得进 ⇒ **静默漏单**。
    #   ⚠️ 注意：`[:1]` 过去曾被当作「把每轮 D6 的 LLM 调用限成 ≤1 次」的成本节流手段；
    #      但用户 2026-09-14 明确「我并没有限制 LLM 调用次数」⇒ 该理由不成立。
    #      且 D6 本身有**日级锁定**（同标的当日只真判一次，其余走 `kind=reuse`），
    #      故放开遍历不会造成 LLM 调用量级膨胀。
    #   现改为**遍历全部候选并逐个留痕**；「一轮买几只 / 一天买几只」仍由账本 policy
    #   （`max_new_buys_per_day` / `max_single_weight` / `max_gross_exposure`）独立强制，
    #   不再由这里静默截断。
    for _i_run, tg in enumerate(triggered):
        tg['trigger_rank'] = _i_run + 1          # 1-based 轮内顺序（池按涨幅降序）
        # ⭐ R4.1（2026-09-13）盘中 D6 否决权：LLM 在**买入之前**行使裁量。
        #    冻结裁定：只接 D6 · 当日首次判一次并锁定至收盘 · 计划内/计划外全覆盖 ·
        #    任何失败（超时/降级/schema 不过）一律**放行** —— 等价于退回「无否决权」现状，
        #    相对本次变更是零新增风险（⚠️ 不是「资金保守」，这点最容易读反）。
        #    ⚠️ judge 内部已保证不抛异常；此处再包一层，确保**绝不阻断 scan**（硬约束）。
        try:
            _veto = intraday_veto.judge(
                tg['sym'], day=day, df=_min_df.get(tg['sym']),
                prev_df=intraday_veto.load_prev_df(tg['sym'], day),
                now=now, signal_ts=f'{day} {tg["ts"]}:00',
                candidates_ref=candidates_ref, plan_ref=f'{day}:{tg["sym"]}')
        except Exception as _e:  # noqa: BLE001
            _veto = {'allowed': True, 'veto_reason': f'judge_error:{type(_e).__name__}',
                     'choice': None, 'reason': ''}
        if not _veto.get('allowed'):
            # 2026-09-14：D6 否决也必须在 confirm 快照里留痕（原先只 print，JSON 里看不出被否过）
            tg['d6_veto'] = {'allowed': False, 'choice': _veto.get('choice'),
                             'veto_reason': _veto.get('veto_reason'),
                             'reason': _veto.get('reason') or 'C_reject'}
            tg['execution_rejected'] = f"D6 否决: {_veto.get('reason') or 'C_reject'}"
            rejected.append({'sym': tg['sym'], 'name': tg.get('name', ''),
                             'trigger_rank': tg.get('trigger_rank'), 'stage': 'd6', 'reason': tg['execution_rejected']})
            print(f"[{now:%H:%M}] [D6-VETO] {tg['sym']} {tg.get('name', '')} "
                  f"{tg['ts']}@{tg['px']:.2f}: {_veto.get('reason') or 'C_reject'} -> skip buy",
                  flush=True)
            continue
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
                # R0.9（2026-09-12）：定档基准改为**动态权益**，与 ledger._validate_buy_policy 同源。
                # 原用 state['start_cash'] 是初始本金 —— 50 万主账本下会把每档固定放大到 22.5 万，
                # 且不随盈亏变化，破坏「单笔亏损 ≤2% 总资金」纪律。
                equity_budget = cost_equity(state)
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
                tg['outcome'] = 'filled'
                print(f'  自主模拟成交: {tg["sym"]} {result["qty"]}股 @{tg["px"]:.2f}', flush=True)
            else:
                request_ids.append(result['id'])
                tg['request_id'] = result['id']
                tg['outcome'] = 'request'
                print(f'  待人工确认: {result["id"]} {tg["sym"]} 建议价{tg["px"]:.2f}', flush=True)
        except ValueError as exc:
            tg['execution_rejected'] = str(exc)
            tg['outcome'] = 'rejected'
            rejected.append({'sym': tg['sym'], 'name': tg.get('name', ''),
                             'trigger_rank': tg.get('trigger_rank'), 'stage': 'policy', 'reason': str(exc)})
            print(f'  模拟不成交: {tg["sym"]} {exc}', flush=True)
    # 2026-09-14：轮末对账——**本轮每条候选都必须有结局**，否则说明又出现了静默丢弃。
    _no_outcome = [tg['sym'] for tg in triggered
                   if 'outcome' not in tg and 'execution_rejected' not in tg]
    if _no_outcome:
        print(f'  ⚠️ 轮内未产生结局的候选 {len(_no_outcome)} 条: {_no_outcome} '
              f'(若出现即说明执行段又发生静默丢弃)', flush=True)
    fp = OUT / f'confirm_{now.strftime("%Y%m%d_%H%M")}.json'
    fp.write_text(json.dumps({'date': day, 'time': now.strftime('%H:%M:%S'), 'pool': pool[:8],
                              'triggered': triggered, 'request_ids': request_ids, 'fill_ids': fill_ids,
                              'rejected': rejected, 'n_triggered': len(triggered),
                              'n_no_outcome': len(_no_outcome),
                              'candidates_ref': candidates_ref,
                              'candidates_snapshot': candidates_snapshot}, ensure_ascii=False), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
