"""阶段二: 交易员日报（操作流水+模式绩效+符合度审计+迭代建议）
盘后自动运行（可接 close_pipeline 尾部或 16:00 定时）。
输出: outputs/reviews/trader_daily_{date}.md + .json（看板自省区数据源）
用法: python scripts/trader_daily.py [--date 2026-08-28]
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'portfolio'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ledger import load  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
OUT = BASE / 'outputs' / 'reviews'

BUY_CN = {'pullback': '回踩确认', 'dibu_lowopen': '低开高走', 'dibu_pullback': '下杀回拉',
          'dibu_shrink': '缩量企稳', 'B2': '放量回踩', 'entry': '建仓'}
SELL_CN = {'stop_loss': '止损', 'zhaban_sell': '炸板卖出', 'vwap_halve': '破均价线减半',
           'vwap_break_all': '再破均价线清仓', 'break_low': '冲高回落止损', 'second_high': '次高点卖出',
           'dragon_link': '龙头联动', 'sector_retreat': '板块退潮', 'ma5_halve': '破5日线减半',
           'ma10_clear': '破10日线清仓', 'time_stop': '时间止损', 'profit_take': '冲高止盈',
           'window_end': '窗口了结'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='')
    args = ap.parse_args()
    st = load()
    fills = st['account']['fills']
    curve = st['account']['equity_curve']
    if not args.date:
        args.date = curve[-1]['date'] if curve else ''
    day_fills = [f for f in fills if f['date'] == args.date]
    # ---- 1.1 灰度审计动作（close_pipeline 决策, 仅审计未执行, 不入账本不计入统计）----
    # P1 修复(2026-09-04, 9/3 复盘): 日报必须区分"实际成交(账本 fills)"与
    # "收盘灰度审计建议(close_decision, 未执行)" —— 9/3 的 603538 审计止损卖出
    # 曾与"卖出触发: 无"并存造成误读。
    close_decision_path = BASE / 'outputs' / 'intraday' / f'close_decision_{args.date}.json'
    close_decision = {}
    if close_decision_path.exists():
        try:
            close_decision = json.loads(close_decision_path.read_text(encoding='utf-8-sig'))
        except Exception:
            close_decision = {}
    audit_only_buys = close_decision.get('buys', []) or []
    audit_only_sells = close_decision.get('sells', []) or []
    # ---- 2.1 操作流水（按时间排序）
    day_fills.sort(key=lambda f: f['ts'])
    # ---- 配对盈亏（全历史）
    trades = []
    pos_q = {}
    pos_v = {}
    for f in fills:
        if f['side'] == 'buy' and not f['reason'].startswith('t_'):
            pos_q[f['sym']] = pos_q.get(f['sym'], 0) + f['qty']
            pos_v[f['sym']] = pos_v.get(f['sym'], 0.0) + f['qty'] * f['px']
        elif f['side'] == 'sell' and not f['reason'].startswith('t_'):
            sym = f['sym']
            if pos_q.get(sym, 0) > 0:
                avg = pos_v[sym] / pos_q[sym]
                trades.append({'sym': sym, 'sell_ts': f['ts'], 'sell_px': f['px'], 'qty': f['qty'],
                               'avg_cost': avg, 'pnl_pct': (f['px'] / avg - 1) * 100,
                               'reason': f['reason']})
                pos_q[sym] -= f['qty']
                pos_v[sym] -= avg * f['qty']
    # ---- 做T统计
    t_rounds = []
    t_open = {}
    for f in fills:
        if f['reason'] == 't_buy':
            t_open[f['sym']] = f
        elif f['reason'] in ('t_sell', 't_sell_out') and f['sym'] in t_open:
            b = t_open.pop(f['sym'])
            t_rounds.append({'sym': f['sym'], 'buy_px': b['px'], 'sell_px': f['px'],
                             'diff_pct': (f['px'] / b['px'] - 1) * 100, 'qty': f['qty']})
    # ---- 2.2 模式绩效
    buy_kinds = Counter()
    sell_reasons = Counter()
    for f in day_fills:
        if f['side'] == 'buy':
            buy_kinds[BUY_CN.get(f['reason'], f['reason'])] += 1
        else:
            sell_reasons[SELL_CN.get(f['reason'], f['reason'])] += 1
    # ---- 2.4 符合度审计
    plan = st.get('plans', {}).get(args.date, {})
    pick_syms = {p['sym'] for p in plan.get('picks', [])} if plan else set()
    audit = []
    for f in day_fills:
        if f['side'] != 'buy':
            continue
        in_plan = f['sym'] in pick_syms
        has_ref = bool(f.get('plan_ref'))
        if in_plan and has_ref:
            audit.append({'sym': f['sym'], 'verdict': '符合', 'note': '计划内标的+执行'})
        elif in_plan:
            audit.append({'sym': f['sym'], 'verdict': '部分符合', 'note': '计划内标的, 非计划流程触发'})
        else:
            audit.append({'sym': f['sym'], 'verdict': '偏离', 'note': '计划外标的（盘中捕捉）'})
    offplan_syms = {a['sym'] for a in audit if a['verdict'] == '偏离'}
    # ---- 当日闭环（实际成交, 当日卖出的 round-trip）----
    trades_today = [t for t in trades if str(t.get('sell_ts', '')).startswith(args.date)]
    # ---- 汇总
    wins = [t for t in trades if t['pnl_pct'] > 0]
    losses = [t for t in trades if t['pnl_pct'] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else None
    avg_win = sum(t['pnl_pct'] for t in wins) / len(wins) if wins else None
    avg_loss = sum(t['pnl_pct'] for t in losses) / len(losses) if losses else None
    eq_today = curve[-1]['equity'] if curve else None
    eq_prev = curve[-2]['equity'] if len(curve) >= 2 else None
    day_pnl = (eq_today / eq_prev - 1) * 100 if eq_today and eq_prev else None
    summary = {
        'date': args.date, 'day_pnl': round(day_pnl, 2) if day_pnl else None,
        'fills': len(day_fills), 'trades_total': len(trades),
        'win_rate': round(win_rate, 1) if win_rate is not None else None,
        'avg_win': round(avg_win, 2) if avg_win is not None else None,
        'avg_loss': round(avg_loss, 2) if avg_loss is not None else None,
        't_rounds': len(t_rounds),
        't_avg_diff': round(sum(t['diff_pct'] for t in t_rounds) / len(t_rounds), 2) if t_rounds else None,
        'buy_kinds': dict(buy_kinds), 'sell_reasons': dict(sell_reasons),
        'audit': audit,
        'trades_today': len(trades_today),
        'offplan_buys': sorted(offplan_syms),
        'audit_only_actions': {'buys': audit_only_buys, 'sells': audit_only_sells, 'executed': False},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'trader_daily_{args.date}.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    # ---- Markdown 日报
    lines = [f'# 交易员日报 · {args.date}', '']
    # P0-2 修复(2026-08-31): day_pnl/avg_win/avg_loss 为 None 时不再抛 TypeError(首日无前日净值、无盈利样本等场景)
    day_pnl_txt = f'{summary["day_pnl"]:+.2f}%' if summary['day_pnl'] is not None else 'N/A'
    lines.append(f'- 当日盈亏: {day_pnl_txt} | 当日实际成交 {summary["fills"]} 笔 | 当日闭环 {summary["trades_today"]} 笔 | 累计闭环 {summary["trades_total"]} 笔')
    if summary['win_rate'] is not None:
        avg_win_txt = f'{summary["avg_win"]:+.2f}%' if summary['avg_win'] is not None else 'N/A'
        avg_loss_txt = f'{summary["avg_loss"]:+.2f}%' if summary['avg_loss'] is not None else 'N/A'
        pl_ratio = (summary['avg_win'] / abs(summary['avg_loss'])
                    if (summary['avg_win'] is not None and summary['avg_loss']) else None)
        pl_txt = f'{pl_ratio:.2f}' if pl_ratio is not None else 'N/A'
        lines.append(f'- 累计闭环胜率 {summary["win_rate"]}% | 平均盈利 {avg_win_txt} | 平均亏损 {avg_loss_txt} | 盈亏比 {pl_txt}（仅计实际成交闭环）')
    if summary['t_rounds']:
        lines.append(f'- 做T {summary["t_rounds"]} 次, 平均差价 {summary["t_avg_diff"]:+.2f}%')
    lines.append('')
    lines.append('## 当日操作流水')
    lines.append('| 时间 | 代码 | 方向 | 数量 | 价格 | 说明 | 计划引用 |')
    lines.append('|---|---|---|---|---|---|---|')
    for f in day_fills:
        side = '买' if f['side'] == 'buy' else '卖'
        reason = SELL_CN.get(f['reason'], BUY_CN.get(f['reason'], f['reason']))
        if f['side'] == 'buy' and f['sym'] in offplan_syms:
            reason = f'[计划外] {reason}'
        lines.append(f"| {f['ts'][11:16]} | {f['sym']} | {side} | {f['qty']} | {f['px']} | {reason} | {f.get('plan_ref', '')} |")
    lines.append('')
    lines.append('## 模式绩效（当日·实际成交）')
    lines.append(f"- 买点分布: {summary['buy_kinds'] or '无'}")
    lines.append(f"- 卖出触发: {summary['sell_reasons'] or '无'}")
    lines.append('')
    lines.append('## 灰度审计动作（收盘流水线 · 未执行，不计入交易统计）')
    if audit_only_sells or audit_only_buys:
        for sd in audit_only_sells:
            lines.append(f"- 卖出建议: {sd['sym']} {sd['reason']} {sd['qty']}股 @{sd['px']} —— 仅审计未执行（账本仍持仓）")
        for bd in audit_only_buys:
            lines.append(f"- 买入建议: {bd['sym']} @{bd['px']} ({bd.get('seg', '')}{bd.get('kind', '')}) —— 仅审计未执行")
    else:
        lines.append('- 当日无灰度审计动作')
    lines.append('')
    lines.append('## 符合度审计')
    for a in audit:
        lines.append(f"- {a['sym']}: {a['verdict']}（{a['note']}）")
    if not audit:
        lines.append('- 当日无买入')
    lines.append('')
    lines.append('## 迭代建议')
    suggests = []
    if summary['win_rate'] is not None and summary['win_rate'] < 50:
        suggests.append(f"胜率 {summary['win_rate']}% < 50%——检查买入点质量（是否追涨/确认不足）")
    if summary['t_rounds'] and summary['t_avg_diff'] and summary['t_avg_diff'] < 1.5:
        suggests.append(f"做T平均差价 {summary['t_avg_diff']}% < 1.5%——差价不足, 检查T进点位（要求远离均价线）")
    bad_reasons = [k for k, v in summary['sell_reasons'].items() if k in ('止损', '时间止损') and v > 0]
    if bad_reasons:
        suggests.append(f"出现止损类卖出 {bad_reasons}——检查买点确认与止损价设置")
    lines += [f'- {s}' for s in suggests] or ['- 无显著偏离，维持现有规则']
    lines.append('')
    lines.append('> 自动生成 · 数据源 ledger + 盘中扫描记录')
    lines.append('> 统计口径: 盈亏/胜率/做T 仅统计账本实际成交; 灰度审计动作未执行, 不计入交易结果。')
    # 阶段四: 迭代提案落盘（人工确认后生效）
    if suggests:
        prop_dir = BASE / 'outputs' / 'iteration_proposals'
        prop_dir.mkdir(parents=True, exist_ok=True)
        (prop_dir / f'{args.date}.json').write_text(json.dumps(
            {'date': args.date, 'proposals': suggests,
             'status': 'pending_confirm'}, ensure_ascii=False, indent=1), encoding='utf-8')
    (OUT / f'trader_daily_{args.date}.md').write_text(chr(10).join(lines), encoding='utf-8')
    print(f'交易员日报: outputs/reviews/trader_daily_{args.date}.md')
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
