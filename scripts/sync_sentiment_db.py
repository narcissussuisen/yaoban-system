# -*- coding: utf-8 -*-
"""R2.5 · 把情绪 CSV 同步进 DB（`market_sentiment`）。

## 为什么要这个脚本
真相源是 `outputs/sentiment_full_<year>.csv`（由 `scripts/r5p_sentiment_build.py` 每日盘后重算），
而 `data/yaoban.db::market_sentiment` 是**消费侧快照**。实测（2026-09-12）：
  CSV 已更新到 **2026-09-11（169 行）**，但 DB 表**停在 2026-08-24（15 行）**
→ 缺口在 **DB 侧**，不需要重算历史，本脚本秒级补齐。

## 列名映射（易错，务必对照）
  CSV            →  market_sentiment
  zt             →  zt_count
  dt             →  dt_count
  zhaban         →  zb_count          （炸板数）
  zhaban_rate    →  break_rate        （炸板率 %）
  max_h          →  max_height        （最高连板）
  median_pct / up_count / down_count / dt7_count / is_bingdian  →  同名直传
  ladder         →  CSV 无此列 → 写 {}（该列原属 a-stock-data 源，r5p 口径不含）

## 安全
- **只 upsert，绝不 delete** —— 不碰 CSV，不删历史行。
- 幂等：可重复运行。
- `--dry-run` 只报告不写库。

用法: python scripts/sync_sentiment_db.py [--year 2026] [--dry-run]
"""
from __future__ import annotations

import argparse
import pathlib
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

import pandas as pd  # noqa: E402

from data.store import Store  # noqa: E402

CSV_TMPL = "sentiment_full_{year}.csv"


def _int(v):
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return int(v)
    except Exception:
        return None


def _float(v):
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except Exception:
        return None


def _bool(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default="2026")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    csv_path = BASE / "outputs" / CSV_TMPL.format(year=args.year)
    if not csv_path.exists():
        print(f"FAIL: 缺 {csv_path}（先跑 scripts/r5p_sentiment_build.py）")
        return 2
    df = pd.read_csv(csv_path)
    df["date"] = df["date"].astype(str)
    print(f"CSV {csv_path.name}: {len(df)} 行, {df['date'].min()} .. {df['date'].max()}")
    need = {"date", "zt", "dt", "zhaban", "zhaban_rate", "max_h"}
    miss = need - set(df.columns)
    if miss:
        print(f"FAIL: CSV 缺列 {sorted(miss)}")
        return 2

    st = Store()
    before = st.conn.execute(
        "SELECT COUNT(*), MAX(date) FROM market_sentiment").fetchone()
    print(f"DB  before: {before[0]} 行, max(date)={before[1]}")

    n_ok = n_skip = 0
    if args.dry_run:
        print(f"--dry-run：将 upsert {len(df)} 行（未写库）。CSV 末日期={df['date'].max()}；"
              f"DB 末日期={before[1]} → 待补 {len(df) - (before[0] or 0)} 行量级")
        st.close()
        return 0
    for _, r in df.iterrows():
        try:
            st.upsert_sentiment(
                date=str(r["date"]),
                zt_count=_int(r.get("zt")) or 0,
                zb_count=_int(r.get("zhaban")) or 0,
                dt_count=_int(r.get("dt")) or 0,
                break_rate=_float(r.get("zhaban_rate")) or 0.0,
                max_height=_int(r.get("max_h")) or 0,
                ladder={},                                   # CSV 无 ladder 列
                median_pct=_float(r.get("median_pct")),
                up_count=_int(r.get("up_count")),
                down_count=_int(r.get("down_count")),
                dt7_count=_int(r.get("dt7_count")),
                is_bingdian=_bool(r.get("is_bingdian")),
            )
            n_ok += 1
        except Exception as e:
            n_skip += 1
            if n_skip <= 5:
                print(f"  [warn] {r.get('date')}: {type(e).__name__}: {e}")

    after = st.conn.execute(
        "SELECT COUNT(*), MAX(date) FROM market_sentiment").fetchone()
    print(f"DB  after : {after[0]} 行, max(date)={after[1]}    (upsert {n_ok} / skip {n_skip})")
    # 抽样回读校验（新列必须落库）
    cur = st.conn.execute(
        "SELECT date, zt_count, dt_count, dt7_count, up_count, down_count, is_bingdian "
        "FROM market_sentiment ORDER BY date DESC LIMIT 3")
    print("最近 3 行 (date, zt, dt, dt7, up, down, bingdian):")
    for row in cur.fetchall():
        print("  ", row)
    cols = [d[1] for d in st.conn.execute("PRAGMA table_info(market_sentiment)")]
    print("表列:", cols)
    st.close()

    ok = after[0] >= before[0] and after[1] and after[1] >= str(df["date"].max())
    print(f"{'OK' if ok else 'FAIL'}: 行数不减且 max(date) 已推进到 CSV 末日期")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
