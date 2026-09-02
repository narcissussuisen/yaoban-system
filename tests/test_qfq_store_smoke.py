"""A3(批次 A, P0 计划 v2.1 §3.2): QFQStore 三源接力日线冒烟测试(三层断言)。

第 1 层·硬断言(常驻, 永不过期):
  000001/600584 两只, get_stock() 日期严格递增(蕴含无重复);
  末日期 >= DAILY_DIR 与 REBUILT_DIR 两个源各自末日期的最大值
  ——证明 rebuilt 接力生效不丢段。

第 2 层·新鲜度断言(按墙钟自动推导期望, 无环境变量开关):
  用 ZoneInfo('Asia/Shanghai') 墙钟推导"最近已收盘交易日" d_expect
  (仓库当前无交易日历快照文件, 采用平日回退保守推导——节假日只会造成
  假 pending 不会假 PASS, 方向安全; 日历快照接入排期 G2 语义):
    d_end == d_expect          -> PASS
    0 < 滞后 <= 3 个自然日      -> 记录 expected_data_lag, 该日验收 pending,
                                  不得计入有效观察日(输出机器可读行供批次 D grep)
    滞后 > 3 个自然日           -> FAIL(阻断)
  禁止用 SKIP 把数据不足的观察日计入通过。

第 3 层·生产验收(一次性命令, 不入 tests/, 见 §3.2):
  每晚 16:30 链跑完后:
  python -X utf8 -c "import sys;sys.path.insert(0,'src');from data.qfq_store \
    import QFQStore;d=QFQStore('2026').get_stock('000001');print(d[-1][1])"
  期望输出当日日期(写入批次 D 核对表)。
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import pandas as pd  # noqa: E402

from data.qfq_store import DAILY_DIR, REBUILT_DIR, QFQStore  # noqa: E402

SYMBOLS = ('000001', '600584')
LAG_FAIL_NATURAL_DAYS = 3  # 滞后超过 3 个自然日 -> FAIL


def _src_end(fp: pathlib.Path) -> str:
    """单个源 parquet 的末日期(ISO); 文件不存在返回 ''。"""
    if not fp.exists():
        return ''
    try:
        df = pd.read_parquet(fp, columns=['date'])
        return str(df['date'].max())[:10]
    except Exception:
        return ''


def _expected_latest_closed(now_sh: datetime) -> date:
    """最近已收盘交易日(保守推导): 收盘(15:00)前取前一日; 周末回退到平日。

    节假日局限: 长假期间会把假日当交易日期望, 后果只是假 pending(方向安全),
    不会假 PASS; 交易日历快照接入后由日历替代(排期见 G2)。
    """
    d = now_sh.date()
    if now_sh.time() < time(15, 0):
        d -= timedelta(days=1)
    while d.weekday() >= 5:  # 5=周六 6=周日
        d -= timedelta(days=1)
    return d


@unittest.skipUnless(DAILY_DIR.exists() or REBUILT_DIR.exists(),
                     'F 盘日线源目录不可见(非本机生产环境)')
class QFQStoreSmokeTests(unittest.TestCase):
    """三层断言的常驻冒烟测试。"""

    @classmethod
    def setUpClass(cls):
        cls.store = QFQStore('2026')

    def _source_ends(self, sym: str) -> str:
        ends = [e for e in (_src_end(DAILY_DIR / f'{sym}.parquet'),
                            _src_end(REBUILT_DIR / f'{sym}.parquet')) if e]
        self.assertTrue(ends, f'{sym}: DAILY_DIR/REBUILT_DIR 均无可读源')
        return max(ends)

    def test_layer1_hard_relay_no_gap_no_dup(self):
        """第 1 层: 日期严格递增 + 末日期 >= 源末日期(rebuilt 接力不丢段)。"""
        for sym in SYMBOLS:
            with self.subTest(sym=sym):
                rows = self.store.get_stock(sym)
                self.assertTrue(rows, f'{sym}: get_stock() 返回空')
                dates = [r[1] for r in rows]
                for a, b in zip(dates, dates[1:]):
                    self.assertLess(a, b, f'{sym}: 日期非严格递增({a} -> {b})')
                src_end = self._source_ends(sym)
                self.assertGreaterEqual(
                    dates[-1], src_end,
                    f'{sym}: 接力末日期 {dates[-1]} < 源末日期 {src_end}(丢段)')

    def test_layer2_freshness(self):
        """第 2 层: 墙钟推导 d_expect; 滞后 1-3 天=pending, >3 天=FAIL。"""
        now_sh = datetime.now(ZoneInfo('Asia/Shanghai'))
        d_expect = _expected_latest_closed(now_sh)
        for sym in SYMBOLS:
            with self.subTest(sym=sym):
                rows = self.store.get_stock(sym)
                self.assertTrue(rows, f'{sym}: get_stock() 返回空')
                d_end = date.fromisoformat(rows[-1][1])
                lag = (d_expect - d_end).days
                if lag == 0:
                    continue  # PASS
                self.assertLessEqual(
                    lag, LAG_FAIL_NATURAL_DAYS,
                    f'{sym}: 数据滞后 {lag} 个自然日(d_end={d_end}, '
                    f'd_expect={d_expect})超过 {LAG_FAIL_NATURAL_DAYS} 天, 阻断')
                # 0 < lag <= 3: 机器可读 pending 记录(批次 D 核对表 grep 此行)
                print(f'[expected_data_lag] sym={sym} d_end={d_end} '
                      f'd_expect={d_expect} lag={lag}d -> 当日验收 pending, '
                      f'不得计入有效观察日')

    def test_layer1_source_dirs_visible(self):
        """文档性断言: 测试运行前提(至少一个源目录可见)已在 skipUnless 保证,
        此处显式记录两源可见性供排查。"""
        print(f'[qfq_smoke] DAILY_DIR={DAILY_DIR} exists={DAILY_DIR.exists()} | '
              f'REBUILT_DIR={REBUILT_DIR} exists={REBUILT_DIR.exists()}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
