"""P1 数据回填脚本

用法（需要 PYTHONPATH 指向 py_libs 以导入 akshare）:
    $env:PYTHONPATH = "..\\py_libs"
    python scripts/backfill_data.py --indexes          # 回填指数日线（2019+）
    python scripts/backfill_data.py --stocks           # 回填案例股日线（2024+）
    python scripts/backfill_data.py --limit-pool 10    # 抓最近 10 个交易日涨停池（东财仅近期）
    python scripts/backfill_data.py --activity         # 活跃度当日快照
    python scripts/backfill_data.py --all              # 全部
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config import section  # noqa: E402
from data.store import Store  # noqa: E402

CFG = section("data")
ROOT = pathlib.Path(__file__).resolve().parent.parent


def case_stocks() -> list[dict]:
    p = ROOT / CFG.get("case_stocks_file", "tests/cases.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    seen = {}
    for c in data["cases"]:
        seen.setdefault(c["symbol"], c["name"])
    return [{"symbol": k, "name": v} for k, v in seen.items()]


def backfill_indexes(store: Store, start: str):
    from data import fetchers as F

    print("== 指数日线 ==")
    for sym in F.index_symbols():
        name = CFG.get("index_names", {}).get(sym, sym)
        df = F.fetch_index_daily(sym)
        df = df[df["date"] >= start] if not df.empty else df
        n = store.upsert_index_daily(df, sym)
        print(f"  {name}({sym}): +{n} 行")
        if not df.empty:
            print(f"    范围 {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")


def backfill_stocks(store: Store, start: str):
    from data import fetchers as F

    print("== 案例股日线 ==")
    for c in case_stocks():
        try:
            df = F.fetch_stock_daily(c["symbol"], start, "20261231")
            df = df[df["date"] >= start] if not df.empty else df
            n = store.upsert_stock_daily(df, c["symbol"])
            print(f"  {c['name']}({c['symbol']}): +{n} 行")
        except Exception as e:  # noqa: BLE001
            print(f"  {c['name']}({c['symbol']}): FAIL {type(e).__name__} {str(e)[:100]}")


def backfill_limit_pool(store: Store, days: int):
    from data import fetchers as F

    print(f"== 涨停池（最近 {days} 个交易日，东财仅近期数据）==")
    import akshare as ak  # noqa: F401 - 仅探测可用性

    df_idx = F.fetch_index_daily("sh000001")
    dates = sorted(df_idx["date"].unique())[-days:]
    for d in dates:
        try:
            rows = F.fetch_limit_up_pool(d)
            n = store.upsert_limit_up_pool(rows)
            print(f"  {d}: {n} 只")
        except Exception as e:  # noqa: BLE001
            print(f"  {d}: FAIL {type(e).__name__} {str(e)[:100]}")


def backfill_activity(store: Store):
    from data import fetchers as F

    print("== 市场活跃度快照 ==")
    a = F.fetch_market_activity()
    if not a:
        print("  FAIL 无数据")
        return
    import datetime

    today = datetime.date.today().isoformat()
    store.upsert_activity(
        date=today,
        up_count=int(a.get("上涨", 0) or 0),
        down_count=int(a.get("下跌", 0) or 0),
        limit_up=int(a.get("涨停", 0) or 0),
        limit_down=int(a.get("跌停", 0) or 0),
        turnover=float(a.get("成交额", 0) or 0),
    )
    print(f"  {today}: 涨{a.get('上涨')} 跌{a.get('下跌')} 涨停{a.get('涨停')} 跌停{a.get('跌停')}")


def universe_symbols() -> list[dict]:
    """读 config/universe.toml：mainline + video_picks"""
    import tomllib

    p = ROOT / "config" / "universe.toml"
    with open(p, "rb") as f:
        uni = tomllib.load(f)["universe"]
    names = {
        "300058": "蓝色光标", "300418": "昆仑万维", "300364": "中文在线", "688095": "福昕软件",
        "002230": "科大讯飞", "603881": "数据港", "000034": "神州数码", "002197": "证通电子",
        "688158": "优刻得", "002747": "埃斯顿", "002896": "中大力德", "688017": "绿的谐波",
        "300308": "中际旭创", "300502": "新易盛", "002916": "深南电路", "603986": "兆易创新",
        "002371": "北方华创", "603005": "晶方科技", "600584": "长电科技", "300408": "三环集团",
        "600111": "北方稀土", "600176": "中国巨石", "002428": "云南锗业", "000962": "东方钽业",
        "300059": "东方财富", "600030": "中信证券", "600900": "长江电力",
        "002407": "多氟多", "600885": "宏发股份", "000021": "深科技", "600379": "宝光股份",
        "002580": "圣阳股份", "002705": "新宝股份", "603316": "诚邦股份", "600722": "金牛化工",
        "603738": "泰晶科技", "000859": "国风新材", "002686": "亿利达", "600352": "浙江龙盛",
        "002642": "荣联科技", "000948": "南天信息", "300663": "科蓝软件", "002279": "久其软件",
        "002303": "美盈森", "600110": "诺德股份", "001206": "依依股份", "600596": "新安股份",
        "605020": "永和股份", "603918": "金桥信息", "002354": "天娱数科", "603823": "百合花",
        "001317": "三羊马", "002971": "和远气体", "002414": "高德红外", "002185": "华天科技",
        "600063": "皖维高新", "002119": "康强电子", "603078": "江化微", "000676": "智度股份",
        "002400": "省广集团",
    }
    out = []
    for sym in uni.get("mainline", []) + uni.get("video_picks", []):
        out.append({"symbol": sym, "name": names.get(sym, sym)})
    return out


def backfill_universe(store: Store):
    """扩充股票池：universe.toml 标的，起点 universe.stock_start"""
    from data import fetchers as F

    import tomllib

    print("== 股票池扩充（universe.toml）==")
    with open(ROOT / "config" / "universe.toml", "rb") as f:
        start = tomllib.load(f)["universe"].get("stock_start", "2022-01-01")
    for c in universe_symbols():
        try:
            df = F.fetch_stock_daily(c["symbol"], start, "20261231")
            n = store.upsert_stock_daily(df, c["symbol"])
            print(f"  {c['name']}({c['symbol']}): +{n} 行")
        except Exception as e:  # noqa: BLE001
            print(f"  {c['name']}({c['symbol']}): FAIL {type(e).__name__} {str(e)[:100]}")


def main():
    ap = argparse.ArgumentParser(description="妖板系统数据回填")
    ap.add_argument("--indexes", action="store_true")
    ap.add_argument("--stocks", action="store_true")
    ap.add_argument("--universe", action="store_true", help="按 config/universe.toml 扩充股票池（主线+视频个股）")
    ap.add_argument("--limit-pool", type=int, default=0, help="最近 N 个交易日涨停池")
    ap.add_argument("--activity", action="store_true")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    store = Store()
    try:
        if args.all or args.indexes:
            backfill_indexes(store, CFG.get("backfill_start", "2019-01-01"))
        if args.all or args.stocks:
            backfill_stocks(store, CFG.get("stock_start", "2024-01-01"))
        if args.all or args.universe:
            backfill_universe(store)
        if args.all or args.limit_pool:
            backfill_limit_pool(store, args.limit_pool or 10)
        if args.all or args.activity:
            backfill_activity(store)
        print("\n== 库统计 ==")
        print(store.stats())
    finally:
        store.close()


if __name__ == "__main__":
    main()
