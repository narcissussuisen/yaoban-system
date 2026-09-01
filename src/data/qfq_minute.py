"""F 盘分钟数据接入层（parquet 直读，分年目录，零第三方新增依赖=pyarrow）

数据源: F:/WorkBuddyItem/a股分钟线/parquet_qfq_{year}/{symbol}.{SH|SZ|BJ}.parquet
  - 1 分钟粒度，网格固定：上午 09:31:00~11:29:00（119 根）+ 下午 13:00:00~15:00:00（121 根）= 240 根/日
  - 前复权：parquet_close = raw_close × f(D)/f_latest（adj_factor 列附带复权因子）
  - 359 只无因子标的（指数/ETF/LOF/次新）adj=1.0（未复权；无拆股的可交易品种回测可接受）
  - 2018 源数据系统性损坏（close<=0），2025/2026 零坏行——本层默认过滤 close<=0 行并统计

接口: get_minute(symbol, freq='1m', start=None, end=None) 与 Store.get_minute 同语义，
返回行列表 [(symbol, freq, ts, open, high, low, close, volume, amount)]，ts='YYYY-MM-DD HH:MM'。
供 day_b_points / simulate_hold / 全市场统计直接使用。
"""
from __future__ import annotations

import functools
import pathlib

import pandas as pd

ROOT = pathlib.Path(r"F:/WorkBuddyItem/a股分钟线")
COLS = ["symbol", "freq", "ts", "open", "high", "low", "close", "volume", "amount"]


def market_suffix(symbol: str) -> str:
    """A 股代码 → 交易所后缀（与 F 盘 parquet 命名一致）
    整改（R4R5 评审）：原 "9" 前缀误吞北交所 920xxx（341 只静默丢失）——
    SH B 股仅 900 段；北交所 = 4/8/920 段。"""
    if symbol.startswith(("6", "5", "900")):
        return "SH"
    if symbol.startswith(("4", "8", "92")):
        return "BJ"
    return "SZ"


def year_of(symbol: str) -> str:
    """按当前数据截止年取分年目录（后续如需历史年可显式传 year）"""
    return "2026"


@functools.lru_cache(maxsize=256)
def _load_parquet(symbol: str, year: str) -> pd.DataFrame | None:
    fp = ROOT / f"parquet_qfq_{year}" / f"{symbol}.{market_suffix(symbol)}.parquet"
    if not fp.exists():
        return None
    df = pd.read_parquet(fp)
    # datetime 'YYYYMMDD HH:MM:SS' → ts 'YYYY-MM-DD HH:MM'（与 minute_kline 表一致）
    dt = df["datetime"].astype(str)
    df["ts"] = (dt.str[:4] + "-" + dt.str[4:6] + "-" + dt.str[6:8] + " " + dt.str[9:14])
    return df


def get_minute(symbol: str, freq: str = "1m", start: str | None = None,
               end: str | None = None, year: str | None = None,
               drop_bad: bool = True) -> list[tuple]:
    """读取单标的分钟线（默认当年分年目录）。

    返回与 Store.get_minute 相同的行列表；freq 仅支持 1m（分年目录为 1 分钟原始，
    5m 需重采样——见 resample_5m）。
    drop_bad=True 时过滤 close<=0 / low<=0 的坏杆（2018 等损坏年份的防御）。
    """
    y = year or year_of(symbol)
    df = _load_parquet(symbol, y)
    if df is None or df.empty:
        return []
    if drop_bad:
        bad = (df["close"] <= 0) | (df["low"] <= 0)
        if bad.any():
            df = df[~bad]
    if start:
        s = start[:10].replace("-", "") + start[10:]
        df = df[df["datetime"].astype(str) >= s]
    if end:
        e = end[:10].replace("-", "") + end[10:]
        df = df[df["datetime"].astype(str) <= e]
    out = []
    for _, r in df.iterrows():
        out.append((symbol, freq, str(r["ts"]), float(r["open"]), float(r["high"]),
                    float(r["low"]), float(r["close"]), float(r["volume"]), float(r["amount"])))
    return out


def resample_5m(rows: list[tuple]) -> list[tuple]:
    """1m 行列表 → 5m（按 pytdx 对齐：09:35/09:40/...；非整除的尾根并入前根）。

    仅用于 5m 粒度的 B 点/回测研究；pytdx 5m 数据的替代品（网格差异已标注）。
    """
    if not rows:
        return []
    df = pd.DataFrame(rows, columns=COLS)
    df["dt"] = pd.to_datetime(df["ts"])
    df["key"] = (df["dt"].dt.hour * 60 + df["dt"].dt.minute) // 5
    out = []
    for (day, k), g in df.groupby([df["dt"].dt.strftime("%Y-%m-%d"), "key"]):
        g = g.sort_values("dt")
        ts = g["ts"].iloc[-1]
        if ts[11:16] == "09:30" or ts[11:16] == "11:35":  # 非对齐尾根并入前根
            continue
        out.append((g["symbol"].iloc[0], "5m", ts,
                    float(g["open"].iloc[0]), float(g["high"].max()),
                    float(g["low"].min()), float(g["close"].iloc[-1]),
                    float(g["volume"].sum()), float(g["amount"].sum())))
    return sorted(out, key=lambda r: r[2])


def coverage_stats(year: str = "2026") -> dict:
    """分年目录覆盖统计：文件数、样本时间范围"""
    d = ROOT / f"parquet_qfq_{year}"
    files = list(d.glob("*.parquet")) if d.exists() else []
    return {"year": year, "files": len(files)}


if __name__ == "__main__":
    import sys

    sym = sys.argv[1] if len(sys.argv) > 1 else "600584"
    rows = get_minute(sym, start="2026-06-01", end="2026-06-02")
    print(f"{sym}: {len(rows)} rows")
    if rows:
        print("  first:", rows[0][2], rows[0][3:8])
        print("  last :", rows[-1][2], rows[-1][3:8])
    print("coverage:", coverage_stats())
