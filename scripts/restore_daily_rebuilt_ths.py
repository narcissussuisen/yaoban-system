"""daily_rebuilt 历史恢复（2026-09-10 数据事故后）。

事故: 腾讯降级回填误用"覆盖写" -> 5291 只标的 3.3 年历史被截成 1 行;
      同日情绪表被污染(zt=1654)。根因修复见 fetch_daily_minute_rebuild.py(合并语义)。
恢复源: a-stock-data 备用源速查「K线(全历史)」——d.10jqka.com.cn/v6/line/hs_{code}/01/last.js
  JSONP, data 字段 = "YYYYMMDD,open,high,low,close,volume(股),amount,turnover%;..." 140 行(≈7个月, 覆盖 2026 全年)。
  已核对: 300468 的 9/9 行 OHLCV+amount 与受损前 parquet 逐字段一致 -> 同为不复权口径、同为股。
策略: 每标的 1 次请求; 与既有行(含 9/10)按 date 合并去重后写回; 幂等——
      仅当现有行数 < 60 才恢复(完好文件直接跳过), 可反复重跑。
用法: python scripts/restore_daily_rebuilt_ths.py
"""
from __future__ import annotations
import json
import pathlib
import sys
import time
import urllib.request

import pandas as pd

OUT = pathlib.Path("F:/WorkBuddyItem/a股level2/daily_rebuilt")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
MIN_ROWS = 60


def fetch_ths(code: str):
    u = f"https://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js"
    req = urllib.request.Request(u, headers={"User-Agent": UA,
                                             "Referer": "https://stockpage.10jqka.com.cn/"})
    with urllib.request.urlopen(req, timeout=15) as r:
        txt = r.read().decode("utf-8", "ignore")
    raw = txt[txt.index("(") + 1: txt.rindex(")")]
    d = json.loads(raw)
    rows = []
    for item in str(d.get("data", "")).split(";"):
        p = item.split(",")
        if len(p) < 7:
            continue
        ts = p[0]
        if len(ts) != 8 or not ts.isdigit():
            continue
        date = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"
        rows.append([code, date, float(p[1]), float(p[2]), float(p[3]), float(p[4]),
                     float(p[5]), float(p[6])])
    if len(rows) < 5:
        return None
    return pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close",
                                       "volume", "amount"])


def main() -> int:
    files = sorted(OUT.glob("*.parquet"))
    print(f"扫描 {len(files)} 个 parquet", flush=True)
    n_fix = n_skip = n_fail = 0
    empty_streak = 0
    t0 = time.time()
    for i, fp in enumerate(files):
        sym = fp.stem
        try:
            old = pd.read_parquet(fp)
            old["date"] = old["date"].astype(str).str[:10]
        except Exception:
            old = None
        if old is not None and len(old) >= MIN_ROWS:
            n_skip += 1
            continue
        new = None
        for attempt in (1, 2):
            try:
                new = fetch_ths(sym)
            except Exception:
                new = None
            if new is not None:
                break
            time.sleep(0.5)
        if new is None:
            n_fail += 1
            empty_streak += 1
            if empty_streak >= 25:
                print(f"  同花顺连续失败 {empty_streak} 只, 冷却30s", flush=True)
                time.sleep(30)
                empty_streak = 0
            continue
        empty_streak = 0
        if old is not None and len(old):
            keep = old[~old["date"].isin(set(new["date"]))]
            merged = pd.concat([keep, new], ignore_index=True).sort_values("date")
            merged = merged[["symbol", "date", "open", "high", "low", "close", "volume", "amount"]]
        else:
            merged = new
        tmp = fp.with_name(fp.name + f".{i}.tmp")
        try:
            merged.to_parquet(tmp, index=False)
            tmp.replace(fp)
            n_fix += 1
        except Exception as exc:
            print(f"  WARN {sym} 写入失败 {type(exc).__name__}", file=sys.stderr, flush=True)
            n_fail += 1
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(files)} 修复={n_fix} 跳过={n_skip} 失败={n_fail} {time.time()-t0:.0f}s", flush=True)
        time.sleep(0.2)
    print(f"完成: 修复={n_fix} 跳过={n_skip} 失败={n_fail} 耗时 {time.time()-t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
