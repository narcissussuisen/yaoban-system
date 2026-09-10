"""腾讯备胎分钟线/实时行情（2026-09-10 接入, TDX 全挂时降级用）。

端点来源: a-stock-data V3.8 §备用源速查「K线(分钟)」行——
  ifzq.gtimg.cn/appstock/app/kline/mkline?param={pre}{code},m5,,320
  (m1/m5/m15/m30/m60, ≤320 根, 需 Referer: https://gu.qq.com/)
字段: [时间, 开, 收, 高, 低, 量(手), {}, 换手率基点] —— 注意开收高低顺序与 TDX 不同;
  第 7 个字段不是成交额是换手率基点; 成交额自算 量(手)*100*均价。
实时报价: qt.gtimg.cn/q=... (GBK 编码, f[2]=代码 f[3]=现价)

2026-09-10 P2 加固（限流保护, a-stock-data 记载 mkline 连续 5000+ 次后返回空=限流非封IP）:
  - 进程内 TTL 缓存: m1 20s / m5 60s / m15-m30 120s / m60 300s / quote 2s;
    tick 守护 5 秒轮询 m5 而实际数据 5 分钟才更新, 缓存把 mkline 请求量降一个数量级;
  - 空响应/异常自动重试一次(0.4s 退避), 仍失败才向调用方返回 None(消费方按数据缺口处理);
  - 全模块请求节流(最小间隔 0.12s + 抖动), 扑灭 scan/monitor/tick 并发突发。

与 TDX 输出的对齐约定(tick_monitor/scan/monitor_intraday/close_pipeline 共用):
  volume 统一为股(手*100), amount 为近似成交额, 列名与 TDX rows 完全一致:
  [ts, open, high, low, close, volume, amount]
"""
from __future__ import annotations
import json
import random
import ssl
import sys
import threading
import time
import urllib.request

import pandas as pd

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

_TTL = {"m1": 20.0, "m5": 60.0, "m15": 120.0, "m30": 120.0, "m60": 300.0}
_MIN_INTERVAL = 0.12
_cache = {}
_lock = threading.Lock()
_last_call = [0.0]
_empty_streak = [0]


def _cache_get(key):
    with _lock:
        item = _cache.get(key)
        if item and item[0] > time.time():
            return item[1]
    return None


def _cache_put(key, value, ttl):
    with _lock:
        _cache[key] = (time.time() + ttl, value)
        if len(_cache) > 2000:
            now = time.time()
            for k in [k for k, v in _cache.items() if v[0] <= now]:
                _cache.pop(k, None)


def _throttle():
    with _lock:
        wait = _MIN_INTERVAL - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.03))
        _last_call[0] = time.time()


def _pre(sym: str) -> str:
    return ("sh" if sym.startswith(("6", "9", "5")) else "sz") + sym


def _fetch_mkline(sym: str, period: str, n: int) -> list:
    _throttle()
    u = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={_pre(sym)},{period},,{n}"
    req = urllib.request.Request(u, headers={"User-Agent": _UA, "Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(req, timeout=10, context=_CTX) as r:
        d = json.loads(r.read().decode("utf-8"))
    node = d["data"][_pre(sym)]
    return node.get(period) or node.get("m5") or []


def min_bars(sym: str, period: str = "m5", n: int = 320) -> list:
    """腾讯 mkline 分钟线原始数组（含缓存/重试/节流; 最新一根为在途 bar）。"""
    key = ("mk", sym, period, n)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    arr = []
    for attempt in (1, 2):
        try:
            arr = _fetch_mkline(sym, period, n)
        except Exception as exc:
            if attempt == 2:
                print(f"[tencent] mkline {sym} {period} 失败: {type(exc).__name__}", file=sys.stderr, flush=True)
                arr = []
                break
            time.sleep(0.4)
            continue
        if arr:
            break
        if attempt == 1:
            time.sleep(0.4)
    if arr:
        _empty_streak[0] = 0
        _cache_put(key, arr, _TTL.get(period, 60.0))
    else:
        _empty_streak[0] += 1
        if _empty_streak[0] % 20 == 1:
            print(f"[tencent] mkline 连续空响应 {_empty_streak[0]} 次(疑限流), 缓存 {len(_cache)} 项",
                  file=sys.stderr, flush=True)
    return arr


def min_df(sym: str, day: str, period: str = "m5"):
    """当日分钟线 DataFrame, 列与 TDX pull_minutes 输出完全一致; <5 根返回 None。"""
    rows = []
    for b in min_bars(sym, period):
        raw = str(b[0])
        # 腾讯时间戳无分隔符(202609101013) -> TDX 口径(2026-09-10 10:13)
        if len(raw) == 12 and raw.isdigit():
            ts = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]} {raw[8:10]}:{raw[10:12]}"
        else:
            ts = raw
        if not ts.startswith(day):
            continue
        o, c, h, l = float(b[1]), float(b[2]), float(b[3]), float(b[4])
        v = float(b[5]) * 100  # 手 -> 股
        rows.append([ts, o, h, l, c, v, round(v * (o + c + h + l) / 4, 2)])
    if len(rows) < 5:
        return None
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume", "amount"])


def quote(syms: list) -> dict:
    """qt.gtimg.cn 实时报价: {sym: 现价 float}; 单票失败不入字典; 2 秒缓存。"""
    out = {}
    missing = []
    for s in syms:
        hit = _cache_get(("q", s))
        if hit is not None:
            out[s] = hit
        else:
            missing.append(s)
    if not missing:
        return out
    _throttle()
    try:
        u = "https://qt.gtimg.cn/q=" + ",".join(_pre(s) for s in missing)
        req = urllib.request.Request(u, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=10, context=_CTX) as r:
            t = r.read().decode("gbk", "ignore")
    except Exception as exc:
        print(f"[tencent] quote 失败: {type(exc).__name__}", file=sys.stderr, flush=True)
        return out
    for line in t.strip().split(chr(10)):
        if "=" not in line:
            continue
        f = line.split("=", 1)[1].strip().strip(";").strip(chr(34)).split("~")
        if len(f) > 33:
            try:
                px = float(f[3])
                out[f[2]] = px
                _cache_put(("q", f[2]), px, 2.0)
            except Exception:
                pass
    return out


def health_probe(symbol: str = "000001") -> bool:
    """备胎源验活（preflight 用）: 能取到当日 m1 分钟线即认为可用(绕过缓存)。"""
    try:
        arr = _fetch_mkline(symbol, "m1", 320)
        return bool(arr) and len(arr) >= 5
    except Exception:
        return False
