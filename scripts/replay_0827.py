"""8/27 盘中回放执行：按盘前计划(plan_0827)对备选逐bar三引擎确认 → 买入 → 收盘估值

纪律: 计划先行(已发布带时间戳)；只执行计划内触发；触发价成交+成本；T+1 当日不卖
仓位: 90% 档 ÷ 4 只 = 22500/只 → 百股整

用法: python scripts/replay_0827.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'src'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from core.intraday import detect_b_point, detect_dibu_buy, detect_pullback_buy  # noqa: E402
from core.sell import limit_price  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent.parent
DB = BASE / 'data' / 'yaoban.db'


def minute_from_db(sym: str, day: str):
    import sqlite3
    con = sqlite3.connect(DB)
    cand = [sym, sym.lstrip('0'), sym.zfill(6)]
    rows = []
    for s in cand:
        rows = con.execute("SELECT ts, open, high, low, close, volume, amount FROM minute_kline WHERE symbol=? AND freq=? AND ts LIKE ? ORDER BY ts", (s, "1m", day + "%")).fetchall()
        if rows:
            break
    con.close()
    if not rows:
        return None
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "amount"])


def prev_close(sym: str, day: str):
    # TDX 回填日线优先（8/21-8/27），fallback stock_daily
    import sqlite3
    fp = pathlib.Path(r'F:/WorkBuddyItem/a股level2/daily') / f'{sym}.parquet'
    if fp.exists():
        try:
            df = pd.read_parquet(fp)
            df['date'] = df['date'].astype(str)
            sub = df[df['date'] < day]
            if len(sub):
                return float(sub.sort_values('date')['close'].iloc[-1])
        except Exception:
            pass
    con = sqlite3.connect(DB)
    r = con.execute("SELECT close FROM stock_daily WHERE symbol=? AND date < ? ORDER BY date DESC LIMIT 1", (sym, day)).fetchone()
    con.close()
    return float(r[0]) if r else None


def main():
    import sys as _s
    _s.path.insert(0, str(BASE / "portfolio"))
    from ledger import load, save, buy, equity
    st = load()
    plan = st["plans"].get("2026-08-27", {})
    picks = plan.get("picks", [])
    print(f'执行 8/27 盘中回放, 备选 {len(picks)} 只 (计划发布于 {plan.get("published_at")})')
    day = "2026-08-27"
    cap = 0.90
    alloc = 100000 * cap / 4
    executed = []
    for pk in picks:
        sym = pk["sym"]
        df = minute_from_db(sym, day)
        if df is None or len(df) < 30:
            print(f'  {sym}: 无 8/27 分钟数据'); continue
        pc = prev_close(sym, day)
        if not pc:
            print(f'  {sym}: 无昨收'); continue
        prev5_rows = None
        cands = []
        for _, b in detect_b_point(df, prev_close=pc, max_pct=0.03).iterrows():
            cands.append((str(b['ts'])[11:16], float(b['price']), 'B' + str(b['kind'])))
        for _, b in detect_dibu_buy(df, prev_close=pc, prev5_amt=None, realtime=True).iterrows():
            cands.append((str(b['ts'])[11:16], float(b['price']), str(b['kind'])))
        for _, b in detect_pullback_buy(df, prev_close=pc, max_pct=3.0).iterrows():
            cands.append((str(b['ts'])[11:16], float(b['price']), 'pullback'))
        if not cands:
            print(f'  {sym}: 无买点触发, 不买'); continue
        t, px, kind = sorted(cands, key=lambda x: x[0])[0]
        lim = limit_price(pc, sym)
        if px >= lim - 0.01:
            print(f'  {sym}: 触发价 {px} 封板, 买不进, 放弃'); continue
        qty = int(alloc / px // 100 * 100)
        if qty < 100:
            print(f'  {sym}: 可买数量不足'); continue
        buy(st, sym, f"{day} {t}", px, qty, kind, stop_pct=5.0, plan_ref="plan-0827")
        executed.append({"sym": sym, "ts": t, "px": px, "qty": qty, "kind": kind})
        print(f'  ✓ {sym} 买入 {qty}股 @{px} ({kind}, {t})')
    # 收盘估值
    mark = {}
    import sqlite3
    con = sqlite3.connect(DB)
    for sym in list(st["account"]["positions"].keys()):
        r = con.execute("SELECT close FROM stock_daily WHERE symbol=? AND date <= ? ORDER BY date DESC LIMIT 1", (sym, day)).fetchone()
        if r:
            mark[sym] = float(r[0])
    con.close()
    # 优先用分钟末价（更精确）
    for sym in list(st["account"]["positions"].keys()):
        df = minute_from_db(sym, day)
        if df is not None and len(df):
            mark[sym] = float(df["close"].iloc[-1])
    eq = equity(st, day, mark)
    save(st)
    print(f'\n8/27 收盘: 净值 {eq:.2f} ({(eq/100000-1)*100:+.2f}%), 持仓 {len(st["account"]["positions"])} 只')
    for sym, p in st["account"]["positions"].items():
        last = mark.get(sym)
        print(f'  {sym}: {p["qty"]}股 成本{p["cost"]:.3f} 现价{last:.3f} 盈亏{(last/p["cost"]-1)*100:+.2f}%')
    # 复盘记录
    from ledger import record_review
    review = {
        "executed": executed,
        "skipped": [pk["sym"] for pk in picks if pk["sym"] not in [e["sym"] for e in executed]],
        "close": {s: mark.get(s) for s in st["account"]["positions"]},
        "note": "首日: 按计划执行, 触发才买, 无前视重建口径"
    }
    record_review(st, day, review)
    save(st)


if __name__ == '__main__':
    main()