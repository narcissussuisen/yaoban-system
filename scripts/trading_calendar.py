"""A 股交易日历权威(唯一真源)——全线任务的交易日守卫依据(2026-09-10 用户裁定)。

职责:
  refresh  拉取并缓存交易日历(baostock query_trade_dates, 零鉴权 TCP)
  check    判定某日是否交易日, 供计划任务守卫消费(退出码契约见下)

退出码(check): 0=交易日 / 3=非交易日 / 4=未知(源不可用且无缓存)

缓存: outputs/calendar/trade_dates_<year>.json
  {"year","source","fetched_at","start","end","days":{"2026-09-11":1,"2026-09-12":0,...}}

与用户裁定的三条约束:
  1. 非交易日调用方静默退出——零推送、零门禁文件;
  2. 日历不可用调用方 fail-open(按交易日继续)并推一次告警——宁多跑不误停;
  3. 缓存新鲜度 STALE_DAYS=30, 过期先尝试在线刷新, 刷新失败沿用旧缓存并标注 stale。

用法:
  python scripts/trading_calendar.py refresh [--year 2026] [--start D] [--end D]
  python scripts/trading_calendar.py check [--date 2026-09-11] [--json]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import date as _date
from datetime import datetime, timedelta

BASE = pathlib.Path(__file__).resolve().parent.parent
CAL_DIR = BASE / "outputs" / "calendar"
SOURCE = "baostock:query_trade_dates"
STALE_DAYS = 30


def cache_path(year: int) -> pathlib.Path:
    return CAL_DIR / ("trade_dates_" + str(year) + ".json")


def load_cache(year: int):
    p = cache_path(year)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    if not isinstance(d.get("days"), dict) or not d["days"]:
        return None
    return d


def cache_age_days(data) -> float:
    try:
        t = datetime.strptime(str(data.get("fetched_at"))[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return 1e9
    return (datetime.now() - t).total_seconds() / 86400.0


def fetch_live(start: str, end: str) -> dict:
    """baostock 查询并返回 {date: 0/1}; 失败抛异常。"""
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError("baostock login failed " + str(lg.error_code) + " " + str(lg.error_msg))
    try:
        rs = bs.query_trade_dates(start_date=start, end_date=end)
        if rs.error_code != "0":
            raise RuntimeError("baostock query failed " + str(rs.error_code) + " " + str(rs.error_msg))
        days = {}
        while rs.next():
            row = rs.get_row_data()
            days[str(row[0])] = int(row[1])
    finally:
        try:
            bs.logout()
        except Exception:
            pass
    if not days:
        raise RuntimeError("baostock returned empty calendar")
    return days


def refresh(year: int, start: str = "", end: str = "") -> dict:
    s = start or (str(year) + "-01-01")
    e = end or (str(year) + "-12-31")
    days = fetch_live(s, e)
    payload = {"year": year, "source": SOURCE,
               "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "start": s, "end": e, "days": days}
    CAL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = cache_path(year).with_name(cache_path(year).name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(cache_path(year))
    return payload


def _parse(day: str) -> _date:
    return datetime.strptime(day, "%Y-%m-%d").date()


def classify(day: str, allow_live: bool = True) -> dict:
    """返回 {"state": trading|non_trading|unknown, "source", "stale", "detail"}。"""
    y = _parse(day).year
    data = load_cache(y)
    stale = bool(data) and cache_age_days(data) > STALE_DAYS
    covered = bool(data) and str(data.get("start")) <= day <= str(data.get("end"))
    if (data is None or stale or not covered) and allow_live:
        try:
            refresh(y)
            data = load_cache(y)
            stale = False
            covered = bool(data) and str(data.get("start")) <= day <= str(data.get("end"))
        except Exception as exc:
            if data is None:
                return {"state": "unknown", "source": SOURCE, "stale": True,
                        "detail": "live refresh failed: " + type(exc).__name__ + ": " + str(exc)[:160]}
    if data is None:
        return {"state": "unknown", "source": SOURCE, "stale": True, "detail": "no cache"}
    v = data["days"].get(day)
    if v is None:
        return {"state": "unknown", "source": SOURCE, "stale": stale,
                "detail": "date outside cached coverage " + str(data.get("start")) + ".." + str(data.get("end"))}
    return {"state": "trading" if int(v) == 1 else "non_trading", "source": SOURCE,
            "stale": stale, "detail": "cache fetched_at=" + str(data.get("fetched_at"))}


def is_trading_day(day: str, allow_live: bool = False) -> bool:
    """供其他脚本内联使用: 仅缓存判定, 未知按交易日处理(fail-open), 不抛异常。"""
    try:
        return classify(day, allow_live=allow_live)["state"] != "non_trading"
    except Exception:
        return True


def prev_trading_day(day: str) -> str:
    """缓存内最近一个早于 day 的交易日; 无缓存则退化为自然日前一天。"""
    d = _parse(day)
    data = load_cache(d.year) or load_cache(d.year - 1)
    if data:
        cands = [k for k, v in data["days"].items() if int(v) == 1 and k < day]
        if cands:
            return max(cands)
    return (d - timedelta(days=1)).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh")
    r.add_argument("--year", type=int, default=0)
    r.add_argument("--start", default="")
    r.add_argument("--end", default="")
    c = sub.add_parser("check")
    c.add_argument("--date", default="")
    c.add_argument("--json", action="store_true")
    c.add_argument("--prev", action="store_true", help="同时输出上一交易日")
    a = ap.parse_args()
    if a.cmd == "refresh":
        year = a.year or datetime.now().year
        try:
            p = refresh(year, a.start, a.end)
        except Exception as exc:
            print("refresh failed: " + type(exc).__name__ + ": " + str(exc)[:200], file=sys.stderr)
            return 4
        n = sum(1 for v in p["days"].values() if int(v) == 1)
        print(json.dumps({"ok": True, "year": year, "days": len(p["days"]), "trading_days": n,
                          "cache": str(cache_path(year))}, ensure_ascii=False))
        return 0
    day = a.date or datetime.now().strftime("%Y-%m-%d")
    res = classify(day)
    out = {"date": day, "state": res["state"], "source": res["source"],
           "stale": res["stale"], "detail": res["detail"]}
    if a.prev:
        out["prev_trading_day"] = prev_trading_day(day)
    print(json.dumps(out, ensure_ascii=False))
    return {"trading": 0, "non_trading": 3, "unknown": 4}[res["state"]]


if __name__ == "__main__":
    sys.exit(main())
