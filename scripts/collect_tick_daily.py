"""R7 盘口/逐笔实时采集管线（mootdx 通达信，每日 15:30 后运行）

采集当日全市场逐笔成交（3s 粒度，含买卖方向）到 parquet 存档，
为承接力/封单/托单/大单流向模式的未来回测积累数据（约 1 年后可回测）。
同时输出当日封板质量快照（涨停标的收盘封单）供每日报告。

存档: F:/WorkBuddyItem/a股level2/tick/{YYYYMMDD}/{symbol}.parquet（列: time, price, vol, num, buyorsell）
      F:/WorkBuddyItem/a股level2/quotes/{YYYYMMDD}.parquet（列: symbol, bid1-5, ask1-5, 封单等）
用法: python -B scripts/collect_tick_daily.py --date 20260826 [--symbols ...] [--limit N]
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from pytdx.hq import TdxHq_API  # noqa: E402

ROOT = pathlib.Path(r"F:/WorkBuddyItem/a股level2")
SERVERS = [("115.238.56.198", 7709), ("115.238.90.165", 7709)]
MARKET = {"6": 1, "9": 1, "5": 1, "4": 0, "8": 0, "0": 0, "3": 0, "2": 0}


def market_of(sym: str) -> int:
    return 1 if sym[0] in ("6", "9", "5") else 0


def fetch_tick(api, sym: str, date: str) -> list[dict]:
    """当日逐笔（分页拉全）"""
    out = []
    start = 0
    while True:
        tr = api.get_transaction_data(market_of(sym), sym, start, 2000)
        if not tr:
            break
        out.extend(tr)
        if len(tr) < 2000:
            break
        start += 2000
        time.sleep(0.05)
    return out


def fetch_quote(api, syms: list[str]) -> list[dict]:
    """五档盘口批量（每次 80 只）"""
    out = []
    for i in range(0, len(syms), 80):
        batch = [(market_of(s), s) for s in syms[i:i + 80]]
        q = api.get_security_quotes(batch)
        if q:
            out.extend(q)
        time.sleep(0.05)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--symbols", default="", help="逗号分隔；缺省=全市场（从 F 盘分钟线标的列表取）")
    ap.add_argument("--limit", type=int, default=0, help="调试：只采前 N 只")
    args = ap.parse_args()
    import datetime
    date = args.date or datetime.date.today().strftime("%Y%m%d")

    if args.symbols:
        syms = [s.strip().zfill(6) for s in args.symbols.split(",") if s.strip()]
    else:
        # 精选模式（默认）：TDX 实时全市场行情扫描当日涨停/触及标的（不依赖 F 盘日线，
        # 约 200-400 只/天，扫描 5 秒 + 逐笔 30-60 分钟）
        def market_of(s):
            return 1 if s[0] in ("6", "9", "5") else 0
        def limit_pct_of(s):
            if s.startswith(("300", "301", "688", "689")):
                return 0.20
            if s.startswith(("4", "8")):
                return 0.30
            return 0.10
        syms = []
        api_tmp = TdxHq_API(heartbeat=False)
        connected = False
        for host, port in SERVERS:
            if api_tmp.connect(host, port, time_out=8):
                connected = True
                break
        if connected:
            for mkt in (0, 1):
                try:
                    count = api_tmp.get_security_count(mkt)
                except Exception:
                    continue
                for start in range(0, count, 1000):
                    try:
                        lst = api_tmp.get_security_list(mkt, start)
                    except Exception:
                        continue
                    if not lst:
                        continue
                    for it in lst:
                        code = it["code"]
                        if code[0] == "6" and code[1] in ("0", "1", "2", "3", "5", "6", "8", "9"):
                            syms.append(code)
                        elif code[0] in ("0", "3") and code not in ("399001", "399006"):
                            syms.append(code)
            zt = []
            for i in range(0, len(syms), 80):
                batch = [(market_of(s), s) for s in syms[i:i + 80]]
                q = api_tmp.get_security_quotes(batch)
                if q:
                    for it in q:
                        code = it["code"]
                        px = it["price"]
                        pre = it["last_close"]
                        if px <= 0 or pre <= 0:
                            continue
                        lp = round(pre * (1 + limit_pct_of(code)), 2)
                        if px >= lp - 0.005 or it["high"] >= lp - 0.01:
                            zt.append(code)
                time.sleep(0.02)
            api_tmp.disconnect()
            syms = zt
            print(f"TDX 实时扫描：{len(syms)} 只涨停/触及", flush=True)
        else:
            print("TDX 扫描连接失败，无标的", flush=True)
    if args.limit:
        syms = syms[:args.limit]

    tick_dir = ROOT / "tick" / date
    q_dir = ROOT / "quotes"
    tick_dir.mkdir(parents=True, exist_ok=True)
    q_dir.mkdir(parents=True, exist_ok=True)

    api = TdxHq_API(heartbeat=False)
    connected = False
    for host, port in SERVERS:
        if api.connect(host, port, time_out=8):
            connected = True
            print(f"已连接 {host}:{port}")
            break
    if not connected:
        print("连接失败")
        return

    n_ok = n_empty = 0
    quote_rows = []
    for k, sym in enumerate(syms):
        try:
            tr = fetch_tick(api, sym, date)
            if tr:
                df = pd.DataFrame(tr)
                df.to_parquet(tick_dir / f"{sym}.parquet")
                n_ok += 1
            else:
                n_empty += 1
        except Exception as e:  # noqa: BLE001
            print(f"  {sym} ERR {e}")
        if (k + 1) % 200 == 0:
            print(f"  {k + 1}/{len(syms)}（有效 {n_ok} 空 {n_empty}）", flush=True)
    # 涨停标的五档（封单快照）
    try:
        q = fetch_quote(api, syms)
        quote_rows = q
        pd.DataFrame(q).to_parquet(q_dir / f"{date}.parquet")
    except Exception as e:  # noqa: BLE001
        print(f"quotes ERR {e}")
    # 当日日线回填（P1 次日收益前置：F 盘日线滞后，TDX 收盘后拉当日 K 线存档）
    # 存: F:/WorkBuddyItem/a股level2/daily/{sym}.parquet（追加当日行）
    try:
        d_dir = ROOT / "daily"
        d_dir.mkdir(parents=True, exist_ok=True)
        d8 = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        n_daily = 0
        for sym in syms:
            bars = api.get_security_bars(9, market_of(sym), sym, 0, 1)  # 9=日K
            if bars:
                b = bars[0]
                row = pd.DataFrame([{"symbol": sym, "date": d8,
                                     "open": b["open"], "high": b["high"],
                                     "low": b["low"], "close": b["close"],
                                     "volume": b["vol"], "amount": b["amount"]}])
                p = d_dir / f"{sym}.parquet"
                if p.exists():
                    old_df = pd.read_parquet(p)
                    old_df = old_df[old_df["date"] != d8]
                    row = pd.concat([old_df, row], ignore_index=True)
                row.to_parquet(p)
                n_daily += 1
            time.sleep(0.02)
        print(f"日线回填: {n_daily} 只", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"daily backfill ERR {e}")
    api.disconnect()
    print(f"完成: {n_ok} 只有逐笔，{len(quote_rows)} 只盘口；目录 {tick_dir}")


if __name__ == "__main__":
    main()