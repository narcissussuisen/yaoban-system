"""C8(计划批次C8, warn 级): qfq_store 源重叠校验 + 诊断接口测试。

构造含重叠日期的临时目录(mock DAILY_DIR/REBUILT_DIR 路径), 验证:
- data_conflict 九字段结构化记录(source_a/source_b/overlap_date/field/value_a/
  value_b/source_hash_a/source_hash_b/status)——不得只有 WARN 文本(终审二轮 10);
- volume 单位换算比对(daily=手×100, rebuilt=股), 换算后一致不误报;
- 冲突日 eligible_observation_day=false, 观察资格 pending;
- 新鲜度诊断(§3.2 四条铁律: 资格由诊断独立判定, 不由测试 exit 兜底):
  ok(lag=0) / pending(expected_data_lag 1-3 天、no_data) / fail(stale_over_3d)
  / data_conflict。
"""
from __future__ import annotations
import pathlib, sys, tempfile, unittest
from datetime import date as _date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import data.qfq_store as qs  # noqa: E402


def _write_parquet(fp: pathlib.Path, rows: list[dict]):
    fp.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(fp, index=False)


class QfqOverlapTests(unittest.TestCase):
    SYM = '999999'  # 非真实代码: F 盘分钟 parquet 必不存在, 分钟聚合段为空, 行全部来自两补齐源

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self._tmp.name)
        self._orig = (qs.DAILY_DIR, qs.REBUILT_DIR)
        qs.DAILY_DIR = tmp / 'daily'
        qs.REBUILT_DIR = tmp / 'daily_rebuilt'
        self.store = qs.QFQStore('2026')

    def tearDown(self):
        qs.DAILY_DIR, qs.REBUILT_DIR = self._orig
        self._tmp.cleanup()

    def _d_expect(self) -> str:
        return qs._expected_latest_closed(
            datetime.now(ZoneInfo('Asia/Shanghai'))).isoformat()

    def test_overlap_conflict_nine_fields(self):
        # 9/2 两源重叠且 close 不一致(10.6 vs 10.61) → 1 条九字段冲突记录
        _write_parquet(qs.DAILY_DIR / f'{self.SYM}.parquet', [
            {'date': '2026-09-01', 'open': 10.0, 'high': 10.5, 'low': 9.9, 'close': 10.2,
             'volume': 1000, 'amount': 1020000.0},
            {'date': '2026-09-02', 'open': 10.2, 'high': 10.8, 'low': 10.1, 'close': 10.6,
             'volume': 1200, 'amount': 1272000.0},
        ])
        _write_parquet(qs.REBUILT_DIR / f'{self.SYM}.parquet', [
            {'date': '2026-09-02', 'open': 10.2, 'high': 10.8, 'low': 10.1, 'close': 10.61,
             'volume': 120000, 'amount': 1272000.0},
            {'date': '2026-09-03', 'open': 10.6, 'high': 10.9, 'low': 10.5, 'close': 10.8,
             'volume': 90000, 'amount': 972000.0},
        ])
        out = self.store.get_stock_with_meta(self.SYM)
        rows, diag = out['rows'], out['diagnostics']
        # rows 接力: 9/1(daily)+9/2(daily 先到胜出)+9/3(rebuilt); 日期严格递增
        self.assertEqual([r[1] for r in rows], ['2026-09-01', '2026-09-02', '2026-09-03'])
        self.assertEqual(rows[1][5], 10.6)  # 9/2 close 取 daily 值(日期边界接力, 先到源胜出)
        # 冲突: 仅 close 一处(volume 1200手×100=120000股 换算后一致, 其余字段全等)
        self.assertEqual(len(diag['data_conflicts']), 1)
        c = diag['data_conflicts'][0]
        for k in ('source_a', 'source_b', 'overlap_date', 'field', 'value_a', 'value_b',
                  'source_hash_a', 'source_hash_b', 'status'):
            self.assertIn(k, c, f'九字段缺失: {k}')
        self.assertEqual(c['overlap_date'], '2026-09-02')
        self.assertEqual(c['field'], 'close')
        self.assertEqual(c['value_a'], 10.6)
        self.assertEqual(c['value_b'], 10.61)
        self.assertEqual(c['status'], 'data_conflict')
        self.assertEqual(c['source_a'], str(qs.DAILY_DIR / f'{self.SYM}.parquet'))
        self.assertEqual(c['source_b'], str(qs.REBUILT_DIR / f'{self.SYM}.parquet'))
        self.assertEqual(len(c['source_hash_a']), 64)
        self.assertEqual(len(c['source_hash_b']), 64)
        # 冲突日观察资格: pending / eligible=false
        self.assertEqual(diag['status'], 'data_conflict')
        self.assertEqual(diag['reason'], 'source_overlap_conflict')
        self.assertFalse(diag['eligible_observation_day'])
        # 兼容诊断接口: last_diagnostics 同步可读
        self.assertEqual(self.store.last_diagnostics, diag)

    def test_volume_unit_conversion_no_false_conflict(self):
        # daily 1000 手 vs rebuilt 100000 股 → 换算后一致, 无 volume 误报
        _write_parquet(qs.DAILY_DIR / f'{self.SYM}.parquet', [
            {'date': '2026-09-01', 'open': 10.0, 'high': 10.5, 'low': 9.9, 'close': 10.2,
             'volume': 1000, 'amount': 1020000.0},
        ])
        _write_parquet(qs.REBUILT_DIR / f'{self.SYM}.parquet', [
            {'date': '2026-09-01', 'open': 10.0, 'high': 10.5, 'low': 9.9, 'close': 10.2,
             'volume': 100000, 'amount': 1020000.0},
        ])
        out = self.store.get_stock_with_meta(self.SYM)
        self.assertEqual(out['diagnostics']['data_conflicts'], [])

    def test_fresh_ok_when_d_end_equals_expect(self):
        d = self._d_expect()
        _write_parquet(qs.DAILY_DIR / f'{self.SYM}.parquet', [
            {'date': '2026-09-01', 'open': 10.0, 'high': 10.5, 'low': 9.9, 'close': 10.2,
             'volume': 1000, 'amount': 1020000.0},
        ])
        _write_parquet(qs.REBUILT_DIR / f'{self.SYM}.parquet', [
            {'date': d, 'open': 10.0, 'high': 10.5, 'low': 9.9, 'close': 10.2,
             'volume': 100000, 'amount': 1020000.0},
        ])
        diag = self.store.get_stock_with_meta(self.SYM)['diagnostics']
        self.assertEqual(diag['status'], 'ok')
        self.assertTrue(diag['eligible_observation_day'])
        self.assertEqual(diag['d_end'], d)
        self.assertEqual(diag['lag_days'], 0)

    def test_fresh_pending_when_lag_1_to_3(self):
        d_end = (_date.fromisoformat(self._d_expect()) - timedelta(days=2)).isoformat()
        _write_parquet(qs.REBUILT_DIR / f'{self.SYM}.parquet', [
            {'date': d_end, 'open': 10.0, 'high': 10.5, 'low': 9.9, 'close': 10.2,
             'volume': 100000, 'amount': 1020000.0},
        ])
        diag = self.store.get_stock_with_meta(self.SYM)['diagnostics']
        self.assertEqual(diag['status'], 'pending')
        self.assertEqual(diag['reason'], 'expected_data_lag')
        self.assertEqual(diag['lag_days'], 2)
        self.assertFalse(diag['eligible_observation_day'])

    def test_fresh_fail_when_stale_over_3(self):
        d_end = (_date.fromisoformat(self._d_expect()) - timedelta(days=4)).isoformat()
        _write_parquet(qs.REBUILT_DIR / f'{self.SYM}.parquet', [
            {'date': d_end, 'open': 10.0, 'high': 10.5, 'low': 9.9, 'close': 10.2,
             'volume': 100000, 'amount': 1020000.0},
        ])
        diag = self.store.get_stock_with_meta(self.SYM)['diagnostics']
        self.assertEqual(diag['status'], 'fail')
        self.assertEqual(diag['reason'], 'stale_over_3d')
        self.assertFalse(diag['eligible_observation_day'])

    def test_no_data_pending(self):
        diag = self.store.get_stock_with_meta(self.SYM)['diagnostics']
        self.assertEqual(diag['status'], 'pending')
        self.assertEqual(diag['reason'], 'no_data')
        self.assertFalse(diag['eligible_observation_day'])
        self.assertIsNone(diag['d_end'])

    def test_get_stock_interface_unchanged(self):
        # C8 硬约束: get_stock() 原 list 接口零变化(不改变现有消费者)
        _write_parquet(qs.REBUILT_DIR / f'{self.SYM}.parquet', [
            {'date': '2026-09-01', 'open': 10.0, 'high': 10.5, 'low': 9.9, 'close': 10.2,
             'volume': 100000, 'amount': 1020000.0},
        ])
        rows = self.store.get_stock(self.SYM)
        self.assertIsInstance(rows, list)
        self.assertEqual(len(rows[0]), 8)  # (sym, date, o, h, l, c, v, a)


if __name__ == '__main__':
    unittest.main(verbosity=2)
