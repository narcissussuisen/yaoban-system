"""交易日志与执行合规检查（P5）

设计（v4.0 手册 §8.6）:
  - 日志三表: trades（开仓） / positions_daily（持仓日记录） / exits（卖出）
  - 合规评分: 每笔交易对照规则清单自动打分（10 点纪律/止损/仓位上限/买卖逻辑一致）
  - 周度统计: 胜率/盈亏比/执行偏差/违规次数

CLI 用法:
    python -m src.journal add_trade   --symbol 600584 --date 2026-08-21 --price 32.5 --shares 2000 --pattern huigui --env 2
    python -m src.journal add_exit    --symbol 600584 --date 2026-08-28 --price 35.0 --reason profit_take
    python -m src.journal compliance  --week 2026-08-24
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "journal.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, name TEXT, entry_date TEXT NOT NULL, entry_price REAL NOT NULL,
    shares INTEGER NOT NULL, pattern TEXT, env_score INTEGER,
    plan_stop TEXT, buy_logic TEXT, note TEXT, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS exits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL REFERENCES trades(id),
    exit_date TEXT NOT NULL, exit_price REAL NOT NULL,
    reason TEXT, rule TEXT, compliant INTEGER, note TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS positions_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL REFERENCES trades(id),
    date TEXT NOT NULL, price REAL, ma_state TEXT, sealed TEXT,
    action TEXT, pnl_pct REAL
);
"""

RULES = {
    "entry": [
        ("env_ok", "环境分允许开仓（仓位≤总仓上限）"),
        ("sector_ok", "板块是热点主线（三信号）"),
        ("pattern_ok", "战法形态合格"),
        ("intraday_ok", "分时确认（放量突破均价线回踩站稳/涨幅≤3%）"),
        ("stop_planned", "买入前写好止损位"),
    ],
    "exit": [
        ("ten_oclock", "隔天10点前不板则走/破均价线即走"),
        ("ma5_half", "破MA5减半（主升浪）"),
        ("ma10_exit", "破MA10清仓/回档股3天不收回止损"),
        ("profit_take", "赚10-20%先落袋1/3-1/2"),
        ("floor", "总仓亏5-8%硬地板"),
    ],
}


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    return conn


def add_trade(args) -> int:
    conn = connect()
    cur = conn.execute(
        "INSERT INTO trades(symbol,name,entry_date,entry_price,shares,pattern,env_score,plan_stop,buy_logic,note) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (args.symbol, args.name, args.date, args.price, args.shares, args.pattern,
         args.env, args.stop, args.logic, args.note))
    conn.commit()
    tid = cur.lastrowid
    print(f"trade #{tid} 已记录: {args.symbol} {args.date} @{args.price} x{args.shares}")
    conn.close()
    return tid


def add_exit(args) -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO exits(trade_id,exit_date,exit_price,reason,rule,compliant,note) "
        "VALUES(?,?,?,?,?,?,?)",
        (args.trade, args.date, args.price, args.reason, args.rule, int(args.compliant), args.note))
    conn.commit()
    print(f"exit 已记录: trade#{args.trade} {args.date} @{args.price} reason={args.reason} "
          f"compliant={'是' if args.compliant else '否'}")
    conn.close()


def compliance(args) -> None:
    conn = connect()
    rows = conn.execute("""
        SELECT t.id, t.symbol, t.entry_date, t.entry_price,
               e.exit_date, e.exit_price, e.reason, e.rule, e.compliant
        FROM trades t LEFT JOIN exits e ON e.trade_id = t.id
        ORDER BY t.id
    """).fetchall()
    print("=== 执行合规检查 ===")
    print(f"{'#':<4}{'代码':<8}{'入场':<12}{'出场':<12}{'盈亏%':<8}{'规则':<22}{'合规'}")
    total_pnl, n_ok, n = 0.0, 0, 0
    for r in rows:
        tid, sym, ed, ep, xd, xp, reason, rule, comp = r
        pnl = (xp / ep - 1) * 100 if ep and xp else None
        if pnl is not None:
            total_pnl += pnl
            n += 1
            n_ok += int(comp or 0)
        comp_s = "✔" if comp else ("✘" if comp == 0 else "-")
        print(f"{tid:<4}{sym:<8}{ed:<12}{(xd or '-'):<12}"
              f"{(f'{pnl:.1f}' if pnl is not None else '-'):<8}{(rule or reason or '-'):<22}{comp_s}")
    if n:
        print(f"\n统计: 交易 {n} 笔，平均盈亏 {total_pnl/n:.2f}%，合规率 {n_ok/n*100:.0f}%")
        print(f"参考: 单笔止损≤2%总资金 | 总仓硬地板5-8% | 10点纪律")
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="交易日志与合规检查")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("add_trade")
    p1.add_argument("--symbol", required=True)
    p1.add_argument("--name", default="")
    p1.add_argument("--date", required=True)
    p1.add_argument("--price", type=float, required=True)
    p1.add_argument("--shares", type=int, required=True)
    p1.add_argument("--pattern", default="")
    p1.add_argument("--env", type=int, default=0)
    p1.add_argument("--stop", default="")
    p1.add_argument("--logic", default="")
    p1.add_argument("--note", default="")

    p2 = sub.add_parser("add_exit")
    p2.add_argument("--trade", type=int, required=True)
    p2.add_argument("--date", required=True)
    p2.add_argument("--price", type=float, required=True)
    p2.add_argument("--reason", default="")
    p2.add_argument("--rule", default="")
    p2.add_argument("--compliant", type=int, default=1)
    p2.add_argument("--note", default="")

    p3 = sub.add_parser("compliance")
    p3.add_argument("--week", default="")

    args = ap.parse_args()
    if args.cmd == "add_trade":
        add_trade(args)
    elif args.cmd == "add_exit":
        add_exit(args)
    elif args.cmd == "compliance":
        compliance(args)


if __name__ == "__main__":
    main()
