"""回填打板情绪数据（a-stock-data 数据链路）：涨停/炸板/跌停池 + 情绪温度计 → market_sentiment 表

用途: 环境打分完整五维（涨停家数/连板高度/赚钱效应）的数据源，解决 H4 数据缺口。
东财 push2ex 四池支持历史交易日（date=YYYYMMDD，非交易日返回空）。

用法:
    python scripts/backfill_sentiment.py --days 60     # 回填最近 60 个交易日（默认 30）
    python scripts/backfill_sentiment.py --date 20260821   # 指定单日
"""
from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from data.store import Store  # noqa: E402
from data import astock as A  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="回填打板情绪数据")
    ap.add_argument("--days", type=int, default=30, help="回填最近 N 个交易日（用指数日历）")
    ap.add_argument("--date", default="", help="指定单日 YYYYMMDD（优先于 --days）")
    args = ap.parse_args()

    store = Store()
    if args.date:
        dates = [args.date]
    else:
        idx = store.get_index("sh000001")
        dates = [r[1].replace("-", "") for r in idx[-args.days:]]

    ok = 0
    for d in dates:
        s = A.limit_up_sentiment(d)
        if s["zt_count"] == 0 and s["dt_count"] == 0:
            print(f"  {d}: 无数据（非交易日/接口空），跳过")
            continue
        # 统一存 'YYYY-MM-DD'（与 index/stock 表一致）
        d_std = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        store.upsert_sentiment(d_std, s["zt_count"], s["zb_count"], s["dt_count"],
                               s["break_rate"], s["max_height"], s["ladder"])
        print(f"  {d_std}: 涨停{s['zt_count']} 炸板{s['zb_count']}({s['break_rate']}%) "
              f"跌停{s['dt_count']} 最高{s['max_height']}板 梯队{s['ladder']}")
        ok += 1
    print(f"\n完成: 入库 {ok} 个交易日")
    rows = store.get_sentiment()
    print(f"market_sentiment 现有 {len(rows)} 条")
    store.close()


if __name__ == "__main__":
    main()
