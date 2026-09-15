# -*- coding: utf-8 -*-
"""EvoAlpha 单日**逐分钟复跑**（用当前代码重演某个历史交易日的卖出执行）。

## 语义（先钉死，避免"复跑=重现历史"的错觉）

复跑 **不是** 重演当时发生过的事，而是：
> **把某个历史日的行情 + 那一天的起跑账本，喂给"现在的"代码，看今天会怎么做。**

因此它与当日实盘的差异 = **代码演进带来的行为差**（例如 9/11 时还没有 R0.5 完整卖点引擎、
没有 R0.8 盘后窗口、也没有 D11 脑裁量）。这正是它有用的原因。

## 复刻的是生产的哪条链路（逐字对齐，不另立口径）

`scripts/tick_monitor.py` 的 per-tick 分支：
  1. `df` = 当日**至今** 1m bar，列 `['ts','open','high','low','close','volume','amount']`
  2. legacy 秒级硬止损：`pos.stop_px` 且 `px <= stop_px` → `('stop_loss', qty)`
  3. 引擎：`manage_day(df, pc, t1, stop_px, cost, params=SELL_ENGINE_PARAMS, limit_px=lup)`，
     只取 `side=='sell'` 且 `qty>0` 的 fills
  4. 盘后窗口（15:05-15:30）：`daily_guard_signals(...)`
  5. 去重键 `(sym, trig, fill_ts)`（生产用 `fired` 集合）
  6. 硬挡：`px <= 跌停+0.005` 或 近 3 根无量 → 不咨询脑、只机械
  7. D11 脑：`brain_sell_decide(sym, trig, day, df, st, t1, qty, cost, now=该分钟)`
  8. 护栏语义：**只有真裁量成功且判 hold** 才跳过；其余（降级/异常）机械原量
  9. 执行量再按 `min(qty, sellable_qty)` 向下取整到整手；不足 1 手 → 只告警
  10. 记账：`ledger.transact(lambda s: execute_tick_risk_sell(s, sym, day, t, px, qty, trig))`

成交价一律用**该分钟 bar 的收盘价**（生产用实时价；同族纪律「信号 entry 用信号 bar close 价」）。

## 安全边界（本工具只读生产 + 只写沙盒）

* 账本：`EVOALPHA_LEDGER` → 沙盒副本（起跑态来自 `--ledger-src`，可为 git rev）
* `tick_monitor.append_event` → 改道（**绝不写生产 risk_events.jsonl**）
* `decision_chain.brain.record` → 改道（不污染生产 brain_<day>.jsonl）
* 结束自检：生产 `portfolio/ledger.json` 与 `outputs/intraday/risk_events.jsonl` 的
  mtime/大小必须与开跑前一致，否则报错退出。

## 两条臂（A/B）

* **A 机械臂**：忽略脑（`BRAIN_SELL_ENABLED=False` 语义）—— 即"现在的机械纪律"表现
* **B 脑臂**：D11 真裁量（真调 LLM，可在报告里逐笔看到 clear_all/halve/hold）

用法：
  python -X utf8 tools/replay_day.py --date 2026-09-11 --ledger-src 7e403c4:portfolio/ledger.json
  python -X utf8 tools/replay_day.py --date 2026-09-11 --ledger-src <file> --no-brain
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = pathlib.Path(__file__).resolve().parent.parent
TZ = ZoneInfo('Asia/Shanghai')
COLS = ['ts', 'open', 'high', 'low', 'close', 'volume', 'amount']

# 与生产同源的导入路径（tick_monitor 在 scripts/、ledger 在 portfolio/、core/decision_chain 在 src/）
for _p in (str(BASE), str(BASE / 'src'), str(BASE / 'scripts'), str(BASE / 'portfolio')):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ────────────────────────────── 输入装载 ──────────────────────────────
def load_ledger_source(src: str) -> dict:
    """起跑账本：`<rev>:<path>` 走 git，否则当文件路径。"""
    if ':' in src and not pathlib.Path(src).exists():
        r = subprocess.run(['git', 'show', src], cwd=str(BASE), capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=120)
        if r.returncode != 0:
            raise SystemExit(f'git show {src} 失败: {r.stderr[:300]}')
        return json.loads(r.stdout)
    return json.loads(pathlib.Path(src).read_text(encoding='utf-8-sig'))


def load_minutes(sym: str, day: str, extra_dirs: list[pathlib.Path]) -> 'pd.DataFrame':
    """优先生产仓库 data/minute/1m，其次 --minute-csv 目录里的 <sym>.csv / _replay_<sym>_1m_911.csv。"""
    import pandas as pd
    cands = []
    store = BASE / 'data' / 'minute' / '1m' / f'{sym}.parquet'
    if store.exists():
        cands.append(('parquet', store))
    for d in extra_dirs:
        for name in (f'{sym}.csv', f'_replay_{sym}_1m_911.csv', f'{sym}_1m.csv'):
            f = d / name
            if f.exists():
                cands.append(('csv', f))
    for kind, f in cands:
        try:
            df = pd.read_parquet(f) if kind == 'parquet' else pd.read_csv(f)
        except Exception:
            continue
        if 'ts' not in df.columns:
            continue
        df['ts'] = df['ts'].astype(str)
        d = df[df['ts'].str.startswith(day)].copy().reset_index(drop=True)
        if len(d):
            return d[COLS]
    return pd.DataFrame(columns=COLS)


# ────────────────────────────── 复跑主体 ──────────────────────────────
def run_replay(day: str, start_state: dict, brain_on: bool, minute_dirs: list, csv_dir: pathlib.Path,
               verbose: bool = True) -> dict:
    import pandas as pd
    import tick_monitor as tm
    import ledger as lg
    from core.daily_src import prev_close_of
    from core.sell import limit_pct_of, limit_price

    # ---- 留痕改道：绝不写生产 ----
    brain_records = []
    events = []

    def _rec(day_, rec):
        rec = dict(rec)
        rec['_sink'] = 'replay'
        brain_records.append(rec)
        return pathlib.Path('(replay-sink)')

    def _ev(ev, *a, **k):
        events.append(dict(ev))

    # ⚠️ 必须 patch **调用路径上那一个** brain 模块对象：`tick_monitor` 用
    #    `from decision_chain import brain as _brain` 绑定的是**包属性**上的子模块。
    #    若只 `sys.modules.pop('decision_chain.brain')` 而没 pop 包本身，
    #    再 `import decision_chain.brain` 会造出**第二个模块对象** ⇒ patch 打在空处、
    #    留痕仍写进生产 brain_<day>.jsonl（2026-09-13 实测踩到，靠末尾自检拦下）。
    brain_mod = tm._brain
    orig_record = brain_mod.record
    orig_append = tm.append_event
    brain_mod.record = _rec
    tm.append_event = _ev
    assert brain_mod.record is _rec, 'brain.record 改道失败（模块对象不符）'
    assert tm.append_event is _ev, 'tick_monitor.append_event 改道失败'
    orig_brainsell = tm.BRAIN_SELL_ENABLED

    timeline, executes, brain_verdicts, blocked = [], [], [], []

    try:
        st = start_state
        for sym, pos in list(st['account']['positions'].items()):
            bars = load_minutes(sym, day, list(minute_dirs) + [pathlib.Path(csv_dir)])
            if not len(bars):
                timeline.append({'sym': sym, 'error': 'no_minute_data'})
                continue
            pc = prev_close_of(sym, day)
            if not pc:
                timeline.append({'sym': sym, 'error': 'no_prev_close'})
                continue
            lup = limit_price(pc, sym)
            ldn = round(pc * (1 - limit_pct_of(sym)), 2)
            stop_px = pos.get('stop_px')
            cost = float(pos.get('cost') or 0)
            entry_date = str(pos.get('entry_ts') or '')[:10]
            fired = set()

            # 分钟游标：连续竞价 09:31-15:00（用当日至今 df）+ 盘后 15:05-15:30（df 保持 15:00）
            stamps = list(bars['ts'])
            after_hours = ['%s 15:%02d' % (day, m) for m in range(5, 31)]
            seq = [(ts, False) for ts in stamps] + [(ts, True) for ts in after_hours]

            with open(os.devnull, 'w') as _null:
                for ts, is_ah in seq:
                    hm = ts[11:16]
                    df = bars if is_ah else bars[bars['ts'] <= ts]
                    if not len(df):
                        continue
                    last = float(df['close'].iloc[-1])
                    px = last
                    t1 = lg.sellable_qty(st, sym, day)
                    trigs = []
                    if stop_px and px <= float(stop_px):
                        trigs.append(('stop_loss', int(pos['qty']), ''))
                    try:
                        eng = tm.manage_day(df, pc, max(0, int(t1)), stop_px, cost,
                                            params=tm.SELL_ENGINE_PARAMS, limit_px=lup)
                        for f in (eng.get('fills') or []):
                            if f.get('side') != 'sell':
                                continue
                            if int(f.get('qty') or 0) > 0:
                                trigs.append((str(f.get('reason') or 'engine'),
                                              int(f['qty']), str(f.get('ts') or '')))
                    except Exception as e:
                        timeline.append({'sym': sym, 'ts': ts, 'engine_error': repr(e)[:120]})
                    if is_ah:
                        for g in tm.daily_guard_signals(sym, entry_date, day, last, cost,
                                                        tm.SELL_ENGINE_PARAMS):
                            trigs.append((g['reason'], int(int(pos['qty']) * float(g['frac'])), ''))

                    for trig, qty, fts in trigs:
                        if (sym, trig, fts) in fired:
                            continue
                        fired.add((sym, trig, fts))
                        volok = float(df['volume'].iloc[-3:].sum()) > 0
                        hard = (px <= ldn + 0.005) or (not volok)
                        n = datetime.strptime(ts, '%Y-%m-%d %H:%M').replace(tzinfo=TZ)
                        b = {'qty': qty, 'hold': False, 'action': 'mechanical', 'status': 'disabled',
                             'choice': None, 'sop': '', 'sop_weak': False, 'reason': ''}
                        if brain_on and not hard:
                            b = tm.brain_sell_decide(sym, trig, day, df, st, t1, qty, cost, now=n)
                        if b['hold']:
                            row = {'ts': ts, 'sym': sym, 'trigger': trig, 'px': round(px, 3),
                                   'orig_qty': qty, 'action': 'brain_hold',
                                   'brain_status': b['status'], 'brain_choice': b['choice'],
                                   'brain_sop': b['sop'], 'sop_weak': b['sop_weak'],
                                   'brain_reason': b['reason']}
                            timeline.append(row)
                            brain_verdicts.append(row)
                            continue
                        q = int(b['qty'] if b.get('qty') is not None else qty)
                        q = min(q, lg.sellable_qty(st, sym, day)) // 100 * 100
                        if hard or q < 100:
                            row = {'ts': ts, 'sym': sym, 'trigger': trig, 'px': round(px, 3),
                                   'orig_qty': qty, 'qty': q, 'action': 'alert_only',
                                   'blocked': 'hard(跌停/无量)' if hard else 'qty<1手',
                                   'brain_status': b['status'], 'brain_choice': b['choice']}
                            blocked.append(row)
                            timeline.append(row)
                            continue
                        try:
                            def mut(s):
                                return tm.execute_tick_risk_sell(s, sym, day, hm, px, q, trig)
                            st, res = lg.transact(mut)
                        except Exception as e:
                            row = {'ts': ts, 'sym': sym, 'trigger': trig, 'px': round(px, 3),
                                   'qty': q, 'action': 'failed', 'error': repr(e)[:140],
                                   'brain_status': b['status'], 'brain_choice': b['choice']}
                            timeline.append(row)
                            continue
                        row = {'ts': ts, 'sym': sym, 'trigger': trig, 'px': round(px, 3),
                               'orig_qty': qty, 'qty': q, 'action': 'execute',
                               'brain_status': b['status'], 'brain_choice': b['choice'],
                               'brain_sop': b['sop'], 'sop_weak': b['sop_weak'],
                               'brain_action': b.get('action'),
                               'brain_reason': b.get('reason')}
                        executes.append(row)
                        timeline.append(row)
                        if verbose:
                            print(f'    [EXEC] {ts} {sym} {trig} qty={q} px={px} '
                                  f'brain={b["status"]}/{b["choice"]}', flush=True)
    finally:
        brain_mod.record = orig_record
        tm.append_event = orig_append
        tm.BRAIN_SELL_ENABLED = orig_brainsell

    return {'timeline': timeline, 'executes': executes, 'brain_verdicts': brain_verdicts,
            'blocked': blocked, 'brain_records': brain_records, 'end_state': st}


def mark_to_market(day: str, state: dict) -> dict:
    """按 9/11 日线收盘估值。"""
    import ledger as lg
    from core.daily_src import load_daily
    acc = state['account']
    mv, marks = 0.0, {}
    for sym, q in (acc.get('positions') or {}).items():
        d = load_daily(sym)
        px = None
        if d is not None and len(d):
            row = d[d['date'].astype(str).str.startswith(day)]
            if len(row):
                px = float(row['close'].iloc[-1])
        marks[sym] = {'qty': q.get('qty'), 'cost': q.get('cost'), 'close': px}
        if px:
            mv += float(q.get('qty') or 0) * px
    return {'cash': acc.get('cash'), 'mv': round(mv, 2),
            'equity': round(float(acc.get('cash') or 0) + mv, 2), 'marks': marks}


def main() -> int:
    ap = argparse.ArgumentParser(description='EvoAlpha 单日逐分钟复跑（当前代码 × 历史日）')
    ap.add_argument('--date', required=True)
    ap.add_argument('--ledger-src', required=True, help='<rev>:portfolio/ledger.json 或文件路径')
    ap.add_argument('--minute-csv-dir', default=str(pathlib.Path(__file__).resolve().parent.parent / '_scratch'))
    ap.add_argument('--out-dir', default=None)
    ap.add_argument('--no-brain', action='store_true', help='只跑机械臂')
    a = ap.parse_args()
    day = a.date
    out_dir = pathlib.Path(a.out_dir) if a.out_dir else (BASE / 'outputs' / 'replay' / day)
    out_dir.mkdir(parents=True, exist_ok=True)
    sandbox_root = out_dir / 'sandbox'
    sandbox_root.mkdir(exist_ok=True)

    # 生产状态指纹（结束时自检未被触碰）
    prod = {'ledger': BASE / 'portfolio' / 'ledger.json',
            'events': BASE / 'outputs' / 'intraday' / 'risk_events.jsonl',
            'brain': BASE / 'outputs' / 'decision_chain' / 'brain' / f'brain_{day}.jsonl'}
    before = {k: (p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
              for k, p in prod.items()}

    base_state = load_ledger_source(a.ledger_src)
    print(f'起跑账本: rev={base_state.get("_revision")} cash={base_state["account"]["cash"]} '
          f'positions={list(base_state["account"]["positions"])}', flush=True)

    report = {'date': day, 'ledger_src': a.ledger_src, 'start_state_rev': base_state.get('_revision'),
              'start_cash': base_state['account']['cash'], 'arms': {}}

    arms = [('A_mechanical', False)]
    if not a.no_brain:
        arms.append(('B_brain', True))

    for arm, brain_on in arms:
        print(f'--- 臂 {arm} (brain_on={brain_on}) ---', flush=True)
        sbx = sandbox_root / f'ledger_{arm}.json'
        shutil.copy2(BASE / 'portfolio' / 'ledger.json', sandbox_root / 'ledger_orig_untouched.json')
        sbx.write_text(json.dumps(base_state, ensure_ascii=False, indent=1), encoding='utf-8')
        os.environ['EVOALPHA_LEDGER'] = str(sbx)
        # ⚠️ 脑开关是**模块层**读取（tick_monitor 第 101 行），必须在 import 之前设好，
        #    否则 BRAIN_SELL_ENABLED 恒为 False、B 臂会退化成 A 臂（空跑）。
        os.environ['EVOALPHA_BRAIN_SELL'] = '1' if brain_on else '0'
        # ⚠️ 连**包**一起 pop：只 pop 子模块会让包属性仍指向旧子模块，import 出双份模块对象。
        for m in [k for k in list(sys.modules)
                  if k == 'tick_monitor' or k == 'ledger' or k == 'decision_chain'
                  or k.startswith('decision_chain.')]:
            sys.modules.pop(m, None)
        res = run_replay(day, copy.deepcopy(base_state), brain_on,
                         [BASE / 'data' / 'minute' / '1m'], pathlib.Path(a.minute_csv_dir))
        mtm = mark_to_market(day, res['end_state'])
        report['arms'][arm] = {'mtm': mtm, 'executes': res['executes'],
                               'brain_verdicts': res['brain_verdicts'],
                               'blocked': res['blocked'], 'timeline': res['timeline'],
                               'brain_records': res['brain_records']}
        print(f'  执行 {len(res["executes"])} 笔；收盘 equity={mtm["equity"]} '
              f'(现金 {mtm["cash"]:.2f} + 市值 {mtm["mv"]})', flush=True)

    # 生产未被触碰自检
    after = {k: (p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
             for k, p in prod.items()}
    touched = {k: (before[k], after[k]) for k in prod if before[k] != after[k]}
    report['production_untouched'] = (not touched)
    if touched:
        print(f'!! 生产状态被改动: {touched}', file=sys.stderr)

    (out_dir / 'replay.json').write_text(json.dumps(report, ensure_ascii=False, indent=1,
                                                    default=str), encoding='utf-8')
    print(f'OK -> {out_dir / "replay.json"}   production_untouched={report["production_untouched"]}')
    return 0 if report['production_untouched'] else 7


if __name__ == '__main__':
    sys.exit(main())
