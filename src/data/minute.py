"""H7.1 分钟数据层：pytdx（通达信）主源 + 腾讯备胎 → DataFrame

实测结论（2026-08-24，服务器 115.238.56.198:7709 浙商证券）:
  - 1分钟：约 2 万根 ≈ 3.7 个月（2026-04-20 ~ 08-24），可 start 偏移连续翻页
  - 5分钟：约 2.4 万根 ≈ 1.7 年（2024-11 ~ 08-24）
  - 指数分钟线可用（get_index_bars）
  - 备源 123.125.108.14:7709 可达（证券数量正常）
  - 腾讯 ifzq.gtimg.cn 分钟 K（m1/m5/m15/m30/m60，≤320 根，零鉴权）作备胎；
    ⚠️ 字段坑：第 7 字段是换手率基点不是成交额（见 vendor_astock_skill.md 备用源速查）

用法:
    from data.minute import fetch_minute_kline, tencent_minute
    df = fetch_minute_kline("600584", freq="5m", since="2026-05-01")
"""
from __future__ import annotations

import json
import time
import urllib.request

import pandas as pd

PYTDX_SERVERS = [
    ("115.238.56.198", 7709),  # 主：浙商证券（实测 1/5 分钟深度最大且连续）
    ("123.125.108.14", 7709),  # 备：实测可达，协议正常
]
FREQ_CAT = {"1m": 8, "5m": 0, "15m": 1, "30m": 2, "60m": 3}  # pytdx category
TENCENT_FREQ = {"1m": "m1", "5m": "m5", "15m": "m15", "30m": "m30", "60m": "m60"}
COLS = ["ts", "open", "high", "low", "close", "volume", "amount"]


def market_of(symbol: str) -> int:
    """通达信市场号：沪=1（6/9/5 开头），深=0（0/3/2 开头，4/8 北交所按深处理）"""
    return 1 if symbol[0] in ("6", "9", "5") else 0


def fetch_minute_kline(symbol: str, freq: str = "5m", since: str = "2026-01-01",
                       max_start: int = 40000, pause: float = 0.12) -> pd.DataFrame:
    """pytdx 主源分段拉取（800 根/段），从最新往前翻直到覆盖 since。

    返回 DataFrame[ts, open, high, low, close, volume, amount]，ts='YYYY-MM-DD HH:MM'。
    服务器 failover：主源失败自动切备源。
    """
    cat = FREQ_CAT.get(freq, 0)
    market = market_of(symbol)
    rows: list = []
    for host, port in PYTDX_SERVERS:
        try:
            from pytdx.hq import TdxHq_API

            api = TdxHq_API(heartbeat=False)
            if not api.connect(host, port, time_out=8):
                continue
            try:
                start = 0
                while start <= max_start:
                    bars = api.get_security_bars(cat, market, symbol, start, 800)
                    if not bars:
                        break
                    rows.extend(bars)
                    if bars[0]["datetime"][:10] <= since:
                        break
                    start += 800
                    time.sleep(pause)
            finally:
                api.disconnect()
            if rows:
                break
        except Exception:  # noqa: BLE001 - 换备源
            rows = []
            continue
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(rows)
    df = df.rename(columns={"datetime": "ts", "vol": "volume"})
    if "amount" not in df.columns:
        df["amount"] = 0.0
    df["ts"] = df["ts"].astype(str)
    df = df[df["ts"] >= since].copy()
    df = df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    for c in ("open", "high", "low", "close", "volume", "amount"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[COLS]


def tencent_minute(symbol: str, freq: str = "5m", n: int = 320) -> pd.DataFrame:
    """腾讯分钟 K 备胎（零鉴权，≤320 根）。第 7 字段为换手率基点，已丢弃。"""
    pre = "sh" if market_of(symbol) == 1 else "sz"
    tq = TENCENT_FREQ.get(freq, "m5")
    url = f"https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={pre}{symbol},{tq},,{n}"
    req = urllib.request.Request(url, headers={"Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    kline = data.get("data", {}).get(f"{pre}{symbol}", {}).get(tq, []) or []
    rows = []
    for it in kline:
        # [时间YYYYMMDDHHMM, 开, 收, 高, 低, 量(手), {}, 换手率基点]
        if len(it) < 6:
            continue
        ts = f"{it[0][:4]}-{it[0][4:6]}-{it[0][6:8]} {it[0][8:10]}:{it[0][10:12]}"
        # 腾讯量为手 → ×100 统一为股（与 pytdx 口径一致）
        rows.append([ts, float(it[1]), float(it[3]), float(it[4]), float(it[2]),
                     float(it[5]) * 100.0, 0.0])
    df = pd.DataFrame(rows, columns=COLS)
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def verify_vs_daily(df_min: pd.DataFrame, daily_rows: list, symbol: str,
                    tolerance: float = 0.02) -> list[str]:
    """分钟线按日聚合 vs 日线对照（open/close/high/low/volume），返回偏差说明。

    daily_rows: store.get_stock(symbol) 的原始行（symbol,date,open,high,low,close,volume,amount）。
    """
    if df_min is None or df_min.empty:
        return [f"{symbol}: 无分钟数据"]
    d = df_min.copy()
    d["date"] = d["ts"].str[:10]
    agg = d.groupby("date").agg(
        open=("open", "first"), close=("close", "last"),
        high=("high", "max"), low=("low", "min"), volume=("volume", "sum")).reset_index()
    daily = {r[1]: r for r in daily_rows}
    issues = []
    for _, r in agg.iterrows():
        dr = daily.get(r["date"])
        if dr is None:
            continue
        for col, idx in (("open", 2), ("close", 5), ("high", 3), ("low", 4), ("volume", 6)):
            dv = float(dr[idx]) if dr[idx] is not None else 0.0
            mv = float(r[col]) if pd.notna(r[col]) else 0.0
            if not dv:
                continue
            if col == "volume":
                # 单位差异容忍：stock_daily 的 volume 因 akshare 多源回退，可能是手或股
                # （分钟统一为股）。接受 1x / 100x / 1/100x 三种比值。
                ratio = mv / dv
                ok = (0.98 <= ratio <= 1.02) or (98 <= ratio <= 102) or (0.0098 <= ratio <= 0.0102)
            else:
                ok = abs(mv / dv - 1) <= tolerance
            if not ok:
                issues.append(f"{symbol} {r['date']} {col}: 分钟聚合 {mv:.2f} vs 日线 {dv:.2f}")
    return issues


if __name__ == "__main__":
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "600584"
    for f in ("5m", "1m"):
        df = fetch_minute_kline(sym, freq=f, since="2026-05-01")
        print(f"[{f}] n={len(df)} {df['ts'].iloc[0] if len(df) else '-'} ~ "
              f"{df['ts'].iloc[-1] if len(df) else '-'}")
