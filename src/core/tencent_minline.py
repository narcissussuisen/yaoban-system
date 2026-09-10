"""腾讯备胎分钟线/实时行情（2026-09-10 接入, TDX 全挂时降级用）。

端点来源: a-stock-data V3.8 §备用源速查「K线(分钟)」行——
  ifzq.gtimg.cn/appstock/app/kline/mkline?param={pre}{code},m5,,320
  (m1/m5/m15/m30/m60, ≤320 根, 需 Referer: https://gu.qq.com/)
字段: [时间, 开, 收, 高, 低, 量(手), {}, 换手率基点] —— 注意开收高低顺序与 TDX 不同;
  第 7 个字段不是成交额是换手率基点; 成交额自算 量(手)*100*均价。
实时报价: qt.gtimg.cn/q=... (GBK 编码, f[2]=代码 f[3]=现价)

与 TDX 输出的对齐约定(消费方 tick_monitor/scan/monitor_intraday 共用):
  volume 统一为股(手*100), amount 为近似成交额, 列名与 TDX rows 完全一致:
  [ts, open, high, low, close, volume, amount]
"""
from __future__ import annotations
import json
import ssl
import urllib.request

import pandas as pd

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def _pre(sym: str) -> str:
    return ("sh" if sym.startswith(("6", "9", "5")) else "sz") + sym


def min_bars(sym: str, period: str = "m5", n: int = 320) -> list:
    """腾讯 mkline 分钟线原始数组（含历史, 最新一根为在途 bar）。"""
    u = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={_pre(sym)},{period},,{n}"
    req = urllib.request.Request(u, headers={"User-Agent": _UA, "Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(req, timeout=10, context=_CTX) as r:
        d = json.loads(r.read().decode("utf-8"))
    node = d["data"][_pre(sym)]
    return node.get(period) or node.get("m5") or []


def min_df(sym: str, day: str, period: str = "m5"):
    """当日 5 分钟线 DataFrame, 列与 TDX pull_minutes 输出完全一致; <5 根返回 None。"""
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
    """qt.gtimg.cn 实时报价: {sym: 现价 float}; 单票失败不入字典。"""
    u = "https://qt.gtimg.cn/q=" + ",".join(_pre(s) for s in syms)
    req = urllib.request.Request(u, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=10, context=_CTX) as r:
        t = r.read().decode("gbk", "ignore")
    out = {}
    for line in t.strip().split(chr(10)):
        if "=" not in line:
            continue
        f = line.split("=", 1)[1].strip().strip(";").strip(chr(34)).split("~")
        if len(f) > 33:
            try:
                out[f[2]] = float(f[3])
            except Exception:
                pass
    return out


def health_probe(symbol: str = "000001") -> bool:
    """备胎源验活（preflight 用）: 能取到当日 m1 分钟线即认为可用。"""
    try:
        arr = min_bars(symbol, "m1", 320)
        return bool(arr) and len(arr) >= 5
    except Exception:
        return False
