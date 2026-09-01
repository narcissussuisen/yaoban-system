"""R7a Level2 精选转换：流式解压 7z → 精选标的 parquet（行情/逐笔委托/逐笔成交）

精选范围：三天内 涨停/炸板池 + 形态池候选 + B 点样本 + 关注标的
用法（托管 venv python，含 py7zr）:
  C:/Users/YZP/.workbuddy/binaries/python/envs/default/Scripts/python.exe scripts/convert_l2_selected.py
"""
from __future__ import annotations

import pathlib
import sys
import time

import py7zr
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

L2_ROOT = pathlib.Path(r"F:/WorkBuddyItem/a股 level2")
OUT = L2_ROOT / "parquet"
DATES = ["20260821", "20260824", "20260825"]

COLS_HQ = ["万得代码", "交易所代码", "自然日", "时间", "成交价", "成交量", "成交额", "成交笔数",
           "BS标志", "当日累计成交量", "最高价", "最低价", "开盘价", "前收盘",
           "申卖价1", "申卖量1", "申买价1", "申买量1", "申卖价2", "申卖量2", "申买价2", "申买量2",
           "申卖价3", "申卖量3", "申买价3", "申买量3", "申卖价4", "申卖量4", "申买价4", "申买量4",
           "申卖价5", "申卖量5", "申买价5", "申买量5", "叫卖总量", "叫买总量"]
COLS_CJ = ["万得代码", "交易所代码", "自然日", "时间", "成交编号", "成交代码", "委托代码",
           "BS标志", "成交价格", "成交数量", "叫卖序号", "叫买序号"]
COLS_WT = ["万得代码", "交易所代码", "自然日", "时间", "委托编号", "交易所委托号",
           "委托类型", "委托代码", "委托价格", "委托数量"]


def main():
    selected = set()
    selected.update(["688347", "161129", "002606", "600584", "002156", "603738", "603316",
                     "002176", "300057", "600030", "600352", "600379", "603078", "003026", "002355"])
    raw = pathlib.Path(r"C:/Users/YZP/WorkBuddy/Claw/方法论与研究文档/yaoban-system/outputs/pullback_b2_2026_raw.csv")
    if raw.exists():
        df = pd.read_csv(raw)
        aug = df[df["ed"].str.startswith("2026-08")]
        selected.update(str(s) for s in aug["sym"].tolist() if isinstance(s, str))
    sys.path.insert(0, r"C:/Users/YZP/WorkBuddy/Claw/方法论与研究文档/yaoban-system/src")
    from data.qfq_store import QFQStore
    from core.sell import limit_pct_of
    q = QFQStore("2026")
    syms = [s for s in q.symbols() if not s.startswith(("399", "5", "15", "16"))]
    for d8 in ("2026-08-21", "2026-08-24", "2026-08-25"):
        for sym in syms:
            rows = q.get_stock(sym)
            if not rows:
                continue
            dates = [r[1] for r in rows]
            if d8 not in dates:
                continue
            i = dates.index(d8)
            if i < 1:
                continue
            r = rows[i]
            lpx = round(float(rows[i - 1][5]) * (1 + limit_pct_of(sym)), 2)
            if float(r[4]) >= lpx - 0.005 or float(r[3]) >= lpx - 0.01:
                selected.add(sym)
    selected = sorted({s for s in selected if isinstance(s, str)})
    print(f"精选标的: {len(selected)} 只", flush=True)

    for date in DATES:
        zf = L2_ROOT / "202608" / f"{date}.7z"
        if not zf.exists():
            print(f"缺 {zf.name}，跳过")
            continue
        out_dir = OUT / date
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = L2_ROOT / f"_tmp_{date}"
        tmp.mkdir(parents=True, exist_ok=True)
        targets = set()
        for sym in selected:
            for ex in ("SZ", "SH", "BJ"):
                for kind in ("行情", "逐笔委托", "逐笔成交"):
                    targets.add(f"{date}/{sym}.{ex}/{kind}.csv")
        t0 = time.time()
        n_ok = 0
        from concurrent.futures import ThreadPoolExecutor
        with py7zr.SevenZipFile(zf, "r") as z:
            files = [n for n in z.getnames() if n in targets]
            print(f"{date}: 目标成员 {len(files)}/{len(targets)}", flush=True)
            BATCH = 15
            done = 0
            for b in range(0, len(files), BATCH):
                batch = files[b:b + BATCH]
                # 线程超时保护：extract 卡住则跳过该批（shutdown(wait=False) 防止等待卡线程）
                ex = ThreadPoolExecutor(max_workers=1)
                fut = ex.submit(z.extract, path=tmp, targets=batch)
                try:
                    fut.result(timeout=40)
                except Exception as e:  # noqa: BLE001
                    print(f"  batch skip {b}: {str(e)[:60]}", flush=True)
                    ex.shutdown(wait=False, cancel_futures=True)
                    continue
                ex.shutdown(wait=False, cancel_futures=True)
                for rel in batch:
                    src = tmp / rel
                    if not src.exists():
                        continue
                    sym = rel.split("/")[1].split(".")[0]
                    kind = rel.split("/")[2][:-4]
                    try:
                        if kind == "行情":
                            df = pd.read_csv(src, encoding="gb18030", low_memory=False,
                                             usecols=lambda c: c in COLS_HQ)
                            df = df[[c for c in COLS_HQ if c in df.columns]]
                        elif kind == "逐笔委托":
                            df = pd.read_csv(src, encoding="gb18030", low_memory=False,
                                             usecols=lambda c: c in COLS_WT)
                            df = df[[c for c in COLS_WT if c in df.columns]]
                        else:
                            df = pd.read_csv(src, encoding="gb18030", low_memory=False,
                                             usecols=lambda c: c in COLS_CJ)
                            df = df[[c for c in COLS_CJ if c in df.columns]]
                        pq.write_table(pa.Table.from_pandas(df, preserve_index=False),
                                       out_dir / f"{sym}_{kind}.parquet")
                        n_ok += 1
                    except Exception as e:  # noqa: BLE001
                        print(f"  {rel} ERR {str(e)[:80]}")
                    done += 1
                if done % 90 == 0:
                    print(f"  {date} 进度 {done}/{len(files)} (n_ok={n_ok}, {time.time()-t0:.0f}s)", flush=True)
        print(f"{date} 完成: {n_ok} 个 parquet，耗时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    print("ALL DONE")


if __name__ == "__main__":
    main()