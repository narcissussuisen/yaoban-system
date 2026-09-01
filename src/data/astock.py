"""a-stock-data 数据链路适配层（实盘认证模式数据源）

源自 https://github.com/simonlin1212/a-stock-data SKILL.md V3.7.1（Apache-2.0），
按本项目需要裁剪为：打板层四池（涨停/炸板/跌停/昨涨停）+ 打板情绪温度计 + 腾讯实时报价。
与 src/data/fetchers.py（akshare 研究模式）互补：
  - 研究模式：akshare（东财限流时自动回退新浪/腾讯），用于回测/历史数据
  - 实盘认证模式：本模块（mootdx/腾讯不封 IP 优先；东财 push2ex 内置限流），
    用于每日盘后涨停池/连板梯队/情绪面采集（环境打分五维的关键数据源）

要点（沿用 SKILL.md 的坑位提示）:
  - 涨停池四池 date 必须传交易日（YYYYMMDD），非交易日 data=null
  - price/limit_price 已 ÷1000；金额单位均为元；first_seal/last_seal 为 HH:MM:SS
  - 东财请求走 em_get() 统一节流（1s 间隔 + 随机抖动），防封 IP
"""
from __future__ import annotations

import random
import time
import urllib.request
from typing import Optional

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ---------- 东财统一请求（节流 + session 复用） ----------
EM_MIN_INTERVAL = 1.0
_em_session = requests.Session()
_em_last_call = [0.0]


def em_get(url: str, params: Optional[dict] = None, headers: Optional[dict] = None,
           timeout: int = 15, **kwargs):
    """东财统一请求入口：自动节流 + 复用 session + 默认 UA（源自 SKILL.md Prerequisites）"""
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))
    try:
        return _em_session.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
    finally:
        _em_last_call[0] = time.time()


# ---------- 打板层：东财四池（SKILL.md §8.1） ----------
ZTB_UT = "7eea3edcaed734bea9cbfc24409ed989"


def _fmt_zt_time(t) -> str:
    s = str(t).zfill(6)
    return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"


def _em_zt_api(endpoint: str, sort: str, date: str) -> list[dict]:
    url = f"https://push2ex.eastmoney.com/{endpoint}"
    params = {"ut": ZTB_UT, "dpt": "wz.ztzt", "Pageindex": 0,
              "pagesize": 10000, "sort": sort, "date": date}
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        return (r.json().get("data") or {}).get("pool") or []
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 涨停板池 {endpoint} 请求失败: {e}")
        return []


def em_zt_pool(date: str) -> list[dict]:
    """涨停池。date=YYYYMMDD。返回 code/name/price/pct/amount/float_cap/turnover/
    limit_days(连板数)/first_seal/last_seal/seal_fund(封板资金,元)/break_times(炸板次数)/industry"""
    out = []
    for p in _em_zt_api("getTopicZTPool", "fbt:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "pct": round(p["zdp"], 2), "amount": p["amount"], "float_cap": p["ltsz"],
                    "turnover": round(p["hs"], 2), "limit_days": p["lbc"],
                    "first_seal": _fmt_zt_time(p["fbt"]), "last_seal": _fmt_zt_time(p["lbt"]),
                    "seal_fund": p["fund"], "break_times": p["zbc"], "industry": p.get("hybk", "")})
    return out


def em_zb_pool(date: str) -> list[dict]:
    """炸板池。返回 code/name/price/limit_price/pct/turnover/first_seal/break_times/industry"""
    out = []
    for p in _em_zt_api("getTopicZBPool", "fbt:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "limit_price": p["ztp"] / 1000, "pct": round(p["zdp"], 2),
                    "turnover": round(p["hs"], 2), "first_seal": _fmt_zt_time(p["fbt"]),
                    "break_times": p["zbc"], "industry": p.get("hybk", "")})
    return out


def em_dt_pool(date: str) -> list[dict]:
    """跌停池。返回 code/name/price/pct/turnover/seal_fund/last_seal/dt_days/open_times/industry"""
    out = []
    for p in _em_zt_api("getTopicDTPool", "fund:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2),
                    "seal_fund": p["fund"], "last_seal": _fmt_zt_time(p["lbt"]),
                    "dt_days": p.get("days"), "open_times": p.get("oc"),
                    "industry": p.get("hybk", "")})
    return out


def em_yzt_pool(date: str) -> list[dict]:
    """昨日涨停池（昨涨停今表现，算晋级率/赚钱效应）。date=当日"""
    out = []
    for p in _em_zt_api("getYesterdayZTPool", "zs:desc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2),
                    "y_first_seal": _fmt_zt_time(p["yfbt"]), "y_limit_days": p["ylbc"],
                    "industry": p.get("hybk", "")})
    return out


def limit_up_sentiment(date: str) -> dict:
    """打板情绪温度计（SKILL.md §8.3）：连板梯队 + 炸板率 + 涨跌停对比。
    对应手册 §1.2 情绪周期与 §1.1 环境打分的涨停家数/连板高度维度。"""
    zt, zb, dt = em_zt_pool(date), em_zb_pool(date), em_dt_pool(date)
    ladder: dict[int, int] = {}
    for s in zt:
        ladder[s["limit_days"]] = ladder.get(s["limit_days"], 0) + 1
    zt_n, zb_n = len(zt), len(zb)
    return {"date": date, "zt_count": zt_n, "zb_count": zb_n, "dt_count": len(dt),
            "break_rate": round(zb_n / (zt_n + zb_n) * 100, 1) if (zt_n + zb_n) else 0.0,
            "max_height": max((s["limit_days"] for s in zt), default=0),
            "ladder": dict(sorted(ladder.items()))}


# ---------- 行情层：腾讯实时报价（SKILL.md §1.2，不封 IP） ----------
_SH_INDEX = {"000300", "000905", "000016", "000688", "000852", "000010"}


def tencent_quote(codes: list[str]) -> dict[str, dict]:
    """腾讯财经实时行情：{code: {name, price, pe_ttm, pb, mcap, turnover, limit_up, limit_down, ...}}
    指数/ETF/个股均支持；GBK 编码；不封 IP。"""
    prefixed, key_of = [], {}
    for c in codes:
        low = c.lower()
        if low.startswith(("sh", "sz", "bj")):
            p = low
        elif c.startswith("92"):
            p = f"bj{c}"
        elif c in _SH_INDEX or c.startswith(("5", "6", "9")):
            p = f"sh{c}"
        elif c.startswith(("4", "8")):
            p = f"bj{c}"
        else:
            p = f"sz{c}"
        prefixed.append(p)
        key_of[p] = c

    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode("gbk")

    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 50:
            continue
        result[key_of.get(key, key)] = {
            "name": vals[1], "code": vals[2], "price": float(vals[3] or 0),
            "prev_close": float(vals[4] or 0), "open": float(vals[5] or 0),
            "volume": float(vals[6] or 0), "turnover": float(vals[38] or 0),
            "pe_ttm": float(vals[39] or 0), "pb": float(vals[46] or 0),
            "mcap": float(vals[45] or 0), "float_mcap": float(vals[44] or 0),
            "limit_up": float(vals[47] or 0), "limit_down": float(vals[48] or 0),
        }
    return result


if __name__ == "__main__":
    import sys

    d = sys.argv[1] if len(sys.argv) > 1 else ""
    import datetime

    d = d or datetime.date.today().strftime("%Y%m%d")
    s = limit_up_sentiment(d)
    print(f"涨停{s['zt_count']} 炸板{s['zb_count']}(炸板率{s['break_rate']}%) "
          f"跌停{s['dt_count']} 最高{s['max_height']}连板 梯队{s['ladder']}")
    q = tencent_quote(["603110", "000001"])
    for k, v in q.items():
        print(f"  {k} {v['name']} {v['price']} PE={v['pe_ttm']} 涨停价={v['limit_up']}")
