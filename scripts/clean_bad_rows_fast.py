"""clean_bad_rows_fast.py — 向量化版原地清洗（替代原 clean_bad_rows.py）

优化点：
  1) 元数据快扫（row group 统计）自动跳过已清洗文件（close min>0 不再命中）→ 天然断点续传
  2) fix_table 全向量化（numpy），消除 130 万行 Python 逐行循环
  3) 原子重写 .tmp + os.replace 不变；进度写 stdout + heartbeat 文件
  4) 可选并行（--workers N，默认 1；磁盘快时可开 4）

用法:
  python scripts/clean_bad_rows_fast.py                # 快扫+续传清洗
  python scripts/clean_bad_rows_fast.py --workers 4
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = pathlib.Path(r"F:/WorkBuddyItem/a股分钟线")
OUT = ROOT / "parquet_qfq_2003_2026"
META = OUT / "meta.json"
HB = ROOT / "clean_heartbeat.txt"
COLS = ("datetime", "trade_date", "trade_time", "open", "high", "low",
        "close", "volume", "amount", "adj_factor")


def hb(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(HB, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def close_min(fp: pathlib.Path):
    try:
        pf = pq.ParquetFile(fp)
    except Exception:
        return None
    try:
        cidx = pf.schema_arrow.names.index("close")
    except ValueError:
        return None
    cmin = None
    for rg in range(pf.metadata.num_row_groups):
        st = pf.metadata.row_group(rg).column(cidx).statistics
        if st is not None and st.min is not None:
            cmin = st.min if cmin is None else min(cmin, st.min)
    return cmin


def fix_table_vec(t: pa.Table):
    """向量化：整日损坏删除 + 散布坏杆 ffill（OHLC 前值，vol/amt=0）。返回 (cols_dict, n_drop, n_ffill) 或 None"""
    n = t.num_rows
    if n == 0:
        return None, 0, 0
    td = t.column("trade_date").to_numpy(zero_copy_only=False)
    o = t.column("open").to_numpy(zero_copy_only=False)
    h = t.column("high").to_numpy(zero_copy_only=False)
    l = t.column("low").to_numpy(zero_copy_only=False)
    c = t.column("close").to_numpy(zero_copy_only=False)
    v = t.column("volume").to_numpy(zero_copy_only=False)
    a = t.column("amount").to_numpy(zero_copy_only=False)
    bad = c <= 0
    if not bad.any():
        return None, 0, 0
    # 整日损坏：该日全部行坏 → 删除
    _, inv = np.unique(td, return_inverse=True)
    day_cnt = np.bincount(inv)
    bad_cnt = np.bincount(inv, weights=bad.astype(np.int64))
    all_bad_day = day_cnt == bad_cnt
    drop = all_bad_day[inv]
    keep = ~drop
    n_drop = int(drop.sum())
    # 散布坏杆（保留行内）：取前一个有效行 ffill；无前值取后一个有效行
    good = ~bad & keep
    idx_good = np.where(good)[0]
    pos = np.searchsorted(idx_good, np.arange(n))
    prev_good = np.where(pos > 0, idx_good[np.maximum(pos - 1, 0)], -1)
    no_prev = bad & keep & (prev_good < 0)
    fill_src = np.where(no_prev, idx_good[0] if len(idx_good) else -1, prev_good)
    n_ffill = int((bad & keep).sum())
    # 对坏行取填充源行的值；vol/amt 置 0
    src = np.where(bad & keep, fill_src, np.arange(n))
    res = {
        "datetime": t.column("datetime").to_numpy(zero_copy_only=False)[src],
        "trade_date": td[src],
        "trade_time": t.column("trade_time").to_numpy(zero_copy_only=False)[src],
        "open": np.where(bad & keep, o[src], o),
        "high": np.where(bad & keep, h[src], h),
        "low": np.where(bad & keep, l[src], l),
        "close": np.where(bad & keep, c[src], c),
        "volume": np.where(bad & keep, 0.0, v),
        "amount": np.where(bad & keep, 0.0, a),
        "adj_factor": t.column("adj_factor").to_numpy(zero_copy_only=False)[src],
    }
    # keep 过滤
    res = {k: arr[keep] for k, arr in res.items()}
    return res, n_drop, n_ffill


def process_one(fp: pathlib.Path, schema) -> tuple:
    """处理单个文件；返回 (sym, changed, n_drop, n_ffill, n_rows) 或 None（无需处理）"""
    sym = fp.name[:-8]
    t0 = time.time()
    t = pq.read_table(fp)
    res, nd, nf = fix_table_vec(t)
    if res is None:
        return (sym, False, 0, 0, t.num_rows, time.time() - t0)
    new_tbl = pa.table({name: pa.array(res[name], type=schema.field(name).type)
                        for name in schema.names}, schema=schema)
    tmp = str(fp) + ".tmp"
    pq.write_table(new_tbl, tmp)
    os.replace(tmp, fp)
    return (sym, True, nd, nf, new_tbl.num_rows, time.time() - t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--scan-workers", type=int, default=3)
    args = ap.parse_args()
    hb(f"启动（处理 workers={args.workers}，快扫 workers={args.scan_workers}），元数据快扫找命中…")
    files = sorted(OUT.glob("*.parquet"))
    t_scan = time.time()
    if args.scan_workers > 1:
        import multiprocessing as mp
        with mp.Pool(args.scan_workers) as pool:
            cms = pool.map(close_min, files)
    else:
        cms = [close_min(fp) for fp in files]
    hits = [fp for fp, cm in zip(files, cms) if cm is not None and cm <= 0]
    hb(f"快扫 {len(files)} 个文件耗时 {(time.time()-t_scan):.0f}s，剩余命中 {len(hits)} 个（已清洗的自动跳过）")
    if not hits:
        hb("无需处理，CLEAN_DONE")
        return
    schema = pq.read_schema(hits[0])
    tot_drop = tot_ffill = tot_changed = 0
    t_start = time.time()
    if args.workers > 1:
        import multiprocessing as mp
        with mp.Pool(args.workers) as pool:
            results = pool.starmap(process_one, [(fp, schema) for fp in hits])
    else:
        results = [process_one(fp, schema) for fp in hits]
    for sym, changed, nd, nf, nrows, dt in results:
        tot_changed += int(changed)
        tot_drop += nd
        tot_ffill += nf
    # meta 更新（命中文件的 n_bars/n_days）
    try:
        meta = json.load(open(META, encoding="utf-8"))
        syms_meta = meta.get("symbols", {})
        for fp, (sym, changed, nd, nf, nrows, _) in zip(hits, results):
            if changed and sym in syms_meta:
                syms_meta[sym]["n_bars"] = int(nrows)
                pf = pq.ParquetFile(fp)
                syms_meta[sym]["n_days"] = pf.metadata.num_row_groups
        json.dump(meta, open(META, "w"), ensure_ascii=False)
    except Exception as e:
        hb(f"meta 更新失败（不影响数据）：{e}")
    hb(f"完成：改动 {tot_changed}/{len(hits)} 文件，删 {tot_drop} 根，填充 {tot_ffill} 根，"
       f"耗时 {(time.time()-t_start)/60:.1f} 分钟")
    hb("CLEAN_DONE")


if __name__ == "__main__":
    main()