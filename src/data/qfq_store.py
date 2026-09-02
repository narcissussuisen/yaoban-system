"""QFQStore — F 盘分钟数据的 Store 兼容适配层（供 day_b_points / simulate_hold 无缝切换）

接口与 data.store.Store 对齐：
  get_minute(symbol, freq='1m', start=None, end=None) -> [(symbol, freq, ts, o,h,l,c,v,a)]
  get_stock(symbol, start=None, end=None) -> [(symbol, date, open, high, low, close, volume, amount)]
  close()
日线由分钟聚合（前复权口径与 yaoban.db 一致）；聚合结果按 symbol 缓存（LRU）。
"""
from __future__ import annotations

import functools
import pathlib

import pandas as pd
import pyarrow.parquet as pq

from data.qfq_minute import market_suffix

ROOT = pathlib.Path(r"F:/WorkBuddyItem/a股分钟线")
# P0-5(2026-08-31): 近端 qfq 日线补齐源(fetch_daily_incremental.py 维护, 含 volume/amount)
DAILY_DIR = pathlib.Path(r"F:/WorkBuddyItem/a股level2/daily")
# 2026-09-02 修复(gate 阻塞根因): TDX 60m 重建日线(fetch_daily_minute_rebuild.py 维护,
# post_close_chain 每日 16:30 写入)——r5p 情绪/r6p 候选此前只读 DAILY_DIR(停在 8/31),
# 导致 sentiment 永远滞后、infra 预检恒 FAIL、盘中 gate 恒拦截。
REBUILT_DIR = pathlib.Path(r"F:/WorkBuddyItem/a股level2/daily_rebuilt")


class QFQStore:
    def __init__(self, year: str = "2026", cache_daily: int = 4096, cache_minute: int = 8):
        self.year = year
        self.dir = ROOT / f"parquet_qfq_{year}"
        self._daily_cache: dict[str, list[tuple]] = {}
        self._daily_lru: list[str] = []
        self._cache_daily = cache_daily
        self._minute_cache: dict[str, list[tuple]] = {}
        self._minute_lru: list[str] = []
        self._cache_minute = cache_minute

    # ---------- 分钟 ----------
    def get_minute(self, symbol: str, freq: str = "1m",
                   start: str | None = None, end: str | None = None) -> list[tuple]:
        if symbol not in self._minute_cache:
            fp = self.dir / f"{symbol}.{market_suffix(symbol)}.parquet"
            if not fp.exists():
                return []
            cols = ["datetime", "open", "high", "low", "close", "volume", "amount"]
            try:
                t = pq.read_table(fp, columns=cols)
            except Exception:
                return []
            df = t.to_pandas()
            df = df[(df["close"] > 0) & (df["low"] > 0)]
            dt = df["datetime"].astype(str)
            df["ts"] = dt.str[:4] + "-" + dt.str[4:6] + "-" + dt.str[6:8] + " " + dt.str[9:14]
            # 快速构建行列表（itertuples 比 iterrows 快一个数量级）
            out = []
            for r in df.itertuples(index=False):
                out.append((symbol, "1m", str(r.ts), float(r.open), float(r.high),
                            float(r.low), float(r.close), float(r.volume), float(r.amount)))
            self._minute_cache[symbol] = out
            self._minute_lru.append(symbol)
            if len(self._minute_lru) > self._cache_minute:
                old = self._minute_lru.pop(0)
                self._minute_cache.pop(old, None)
        rows = self._minute_cache[symbol]
        if start or end:
            # start/end 为日期（'YYYY-MM-DD'）时按日过滤（与 Store.get_minute 语义一致）
            s10 = start[:10] if start else None
            e10 = end[:10] if end else None
            out = []
            for r in rows:
                d = r[2][:10]
                if s10 and d < s10:
                    continue
                if e10 and d > e10:
                    continue
                out.append(r)
            return out
        return rows

    # ---------- 日线（分钟聚合 + LRU 缓存） ----------
    def get_stock(self, symbol: str, start: str | None = None,
                  end: str | None = None) -> list[tuple]:
        key = symbol
        if key not in self._daily_cache:
            self._daily_cache[key] = self._agg_daily(symbol)
            self._daily_lru.append(key)
            if len(self._daily_lru) > self._cache_daily:
                old = self._daily_lru.pop(0)
                self._daily_cache.pop(old, None)
        rows = self._daily_cache[key]
        if start:
            rows = [r for r in rows if r[1] >= start]
        if end:
            rows = [r for r in rows if r[1] <= end]
        return rows

    def _agg_daily(self, symbol: str) -> list[tuple]:
        out = self._agg_daily_minute(symbol)
        # P0-5(2026-08-31): 分钟线缺口(8/22+)由 daily/(qfq日线) 按动态分界补充
        # 2026-09-02 修复: 再由 daily_rebuilt/(TDX 重建) 补最末段——两源按日期边界接力,
        # 保证 rebuild 之后 r5p/r6p 当晚即可读到当日(否则情绪表滞后 → gate 恒拦截)
        # 口径(实测 2026-09-02, 000001 对比): daily/ volume 为手(×100 归一股);
        # daily_rebuilt/ volume 已为股(pytdx 60m b['vol'] 直传, 勿再乘)
        for src_dir, vol_mult in ((DAILY_DIR, 100.0), (REBUILT_DIR, 1.0)):
            last = out[-1][1] if out else ''
            fp2 = src_dir / f'{symbol}.parquet'
            if not fp2.exists():
                continue
            try:
                d = pd.read_parquet(fp2, columns=['date', 'open', 'high', 'low', 'close', 'volume', 'amount'])
                for _, row in d.iterrows():
                    ds = str(row['date'])[:10]
                    if ds > last:
                        out.append((symbol, ds, float(row['open']), float(row['high']),
                                    float(row['low']), float(row['close']),
                                    float(row['volume']) * vol_mult, float(row['amount'])))
            except Exception:
                pass
        return out

    def _agg_daily_minute(self, symbol: str) -> list[tuple]:
        fp = self.dir / f"{symbol}.{market_suffix(symbol)}.parquet"
        if not fp.exists():
            return []
        try:
            t = pq.read_table(fp, columns=["datetime", "open", "high", "low", "close",
                                           "volume", "amount"])
        except Exception:
            return []
        df = t.to_pandas()
        df = df[df["close"] > 0]
        if df.empty:
            return []
        dt = df["datetime"].astype(str)
        df["date"] = dt.str[:8]
        g = df.groupby("date")
        agg = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                    close=("close", "last"), volume=("volume", "sum"), amount=("amount", "sum"))
        out = []
        for date, r in agg.iterrows():
            dstr = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            out.append((symbol, dstr, float(r["open"]), float(r["high"]), float(r["low"]),
                        float(r["close"]), float(r["volume"]), float(r["amount"])))
        return out

    def close(self):
        pass

    def symbols(self) -> list[str]:
        if not self.dir.exists():
            return []
        return sorted(f.name.split(".")[0] for f in self.dir.glob("*.parquet"))


# ---------- 并行日线聚合工具（全市场信号检测用） ----------

def _agg_worker(args):
    year, sym = args
    try:
        st = QFQStore(year)
        rows = st._agg_daily(sym)
        return sym, rows
    except Exception as e:
        print(f"[worker FAIL] {sym}: {type(e).__name__} {e}", flush=True)
        return sym, []


def build_daily_map(year: str, symbols: list[str], workers: int = 6) -> dict[str, list[tuple]]:
    """并行聚合全市场日线 → {sym: rows}（内存常驻，供信号检测）。
    用线程池（pyarrow 读 parquet 释放 GIL；避免 Windows spawn 子进程环境问题）。"""
    from concurrent.futures import ThreadPoolExecutor
    out: dict[str, list[tuple]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (sym, rows) in enumerate(ex.map(_agg_worker, [(year, s) for s in symbols])):
            out[sym] = rows
            if (i + 1) % 1000 == 0:
                print(f"  聚合 {i + 1}/{len(symbols)}", flush=True)
    return out


if __name__ == "__main__":
    import sys
    st = QFQStore("2026")
    sym = sys.argv[1] if len(sys.argv) > 1 else "600584"
    m = st.get_minute(sym, start="2026-06-01", end="2026-06-02")
    d = st.get_stock(sym)
    print(f"{sym}: minute {len(m)} rows | daily {len(d)} rows ({d[0][1]} ~ {d[-1][1]})")
    print("daily head:", d[0])
    st.close()