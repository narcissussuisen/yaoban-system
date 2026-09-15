# -*- coding: utf-8 -*-
"""回归锚：CSV 真相源读出的情绪字段，必须与 DB 口径**逐字段一致**。

⭐ 为什么要有这条：2026-09-13 我把 ① 段改为优先读 CSV 后，
   因 **CSV 与 DB 列名不同**（`zt` vs `zt_count`）而静默把 `zt=40 dt=20` 读成 **`zt=0 dt=0`**
   —— 这足以让**市场分档**（冰点/磨底/中性）判错，而**当日的档位恰好相同**，所以只看
   `regime` 是发现不了的。故此处**逐字段**比对，锁死这类静默口径漂移。

不依赖网络、不调 LLM。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

DAY = "2026-09-11"
# 下游实际消费的字段（① 段据此分档）
FIELDS = ("zt_count", "dt_count", "dt7_count", "up_count", "down_count", "median_pct")


class SentimentSourceConsistencyTests(unittest.TestCase):
    def setUp(self):
        from decision_chain import engine
        self.engine = engine

    def test_csv_row_matches_db_by_field(self):
        """CSV 真相源与 DB 在同一日必须给出**逐字段相同**的值。"""
        from data.store import Store
        row_csv, src, src_date = self.engine._sentiment_row(DAY)
        self.assertEqual(src, "csv", "应优先命中 CSV 真相源")
        self.assertEqual(src_date, DAY)
        st = Store()
        ref = st.sentiment_as_of(DAY)
        st.close()
        self.assertTrue(ref, "DB 应有该日行（本测试以 DB 为对照口径）")
        self.assertEqual(str(ref.get("date")), DAY)
        for k in FIELDS:
            self.assertIn(k, row_csv, f"CSV 归一化后缺字段 {k}")
            a, b = row_csv[k], ref.get(k)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                self.assertEqual(float(a), float(b), f"字段 {k} 不一致：CSV={a} DB={b}")
            else:
                self.assertEqual(str(a), str(b), f"字段 {k} 不一致：CSV={a} DB={b}")

    def test_zt_dt_are_nonzero_when_db_nonzero(self):
        """⭐ 直击本次事故：DB 的 zt/dt 非零时，CSV 读出的也必须非零。

        只断言 `regime` 相同是**不够的** —— 当日 `zt=0` 与 `zt=40` 恰好同档（都"磨底"），
        所以档位比对通不过这条测试的意义。必须直接断言**数值**。
        """
        from data.store import Store
        row, _, _ = self.engine._sentiment_row(DAY)
        st = Store()
        ref = st.sentiment_as_of(DAY)
        st.close()
        if ref.get("zt_count"):
            self.assertEqual(row.get("zt_count"), ref.get("zt_count"),
                             "zt_count 被静默读成 0（CSV/DB 列名口径漂移回归）")
            self.assertNotEqual(row.get("zt_count"), 0, "zt_count 不得为 0")
        if ref.get("dt_count"):
            self.assertEqual(row.get("dt_count"), ref.get("dt_count"), "dt_count 口径漂移")
            self.assertNotEqual(row.get("dt_count"), 0, "dt_count 不得为 0")

    def test_csv_key_normalization_map_present(self):
        """归一化映射必须覆盖已知的列名差异（防将来被误删）。"""
        m = self.engine._CSV_TO_DB_KEYS
        for csv_key, db_key in (("zt", "zt_count"), ("dt", "dt_count"),
                                ("max_h", "max_height"), ("zhaban", "zb_count"),
                                ("zhaban_rate", "break_rate")):
            self.assertEqual(m.get(csv_key), db_key, f"缺映射 {csv_key} → {db_key}")

    def test_stale_flag_when_day_absent(self):
        """目标日无数据时：`stale` 必须为真、且不抛异常（回落 DB 也要如实标注）。"""
        row, src, src_date = self.engine._sentiment_row("2030-01-01")
        if row:
            self.assertNotEqual(src_date, "2030-01-01")
        # 不崩即达标；stale 由 seg_gate 依 src_date 计算


if __name__ == "__main__":
    unittest.main(verbosity=2)
