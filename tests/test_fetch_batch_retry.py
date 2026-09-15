"""fetch_batch 批次重试护栏（2026-09-15 盘中补丁）。

事故背景：09:34:01 第 1 批（offset=0）URLError ⇒ 整批 400 只丢失（quotes=4289/4689），
`scan_and_confirm.py` 的 `batch_failures` 一票否决 ⇒ 整轮 rc=4。

本文件钉住的四条不变量：
① 快速失败（URLError）重试后成功 ⇒ 正常返回，**不抛**；
② 全部尝试失败 ⇒ 抛**原始异常类型**（上层留痕口径不变），且必须 fail-closed（不返回空 dict）；
③ **超时类不重试**（避免把一轮失败放大成整轮卡住）；
④ 成功路径解析结果与改动前一致（重试不引入解析行为变化）。
"""
from __future__ import annotations
import sys, pathlib, unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import market_scan as mkt  # noqa: E402


class _Resp:
    def __init__(self, body: bytes):
        self._b = body

    def read(self) -> bytes:
        return self._b


def _row(code='sz000001', name='平安银行', price='11.50', prev='11.40',
         vol='123456', amt='98765.4', turn='1.23', pe='5.67') -> str:
    parts = [''] * 40
    parts[0] = '51'
    parts[1] = name
    parts[2] = code[2:]
    parts[3] = price
    parts[4] = prev
    parts[6] = vol
    parts[37] = amt
    parts[38] = turn
    parts[39] = pe
    return 'v_' + code + '="' + '~'.join(parts) + '";'


BODY_OK = (_row() + _row('sz000002', '万科A', '8.00', '8.10')).encode('gbk')


class FetchBatchRetryTests(unittest.TestCase):
    def setUp(self):
        self.sleep = mock.patch.object(mkt.time, 'sleep').start()
        self.addCleanup(mock.patch.stopall)

    def _patch(self, side_effect):
        return mock.patch.object(mkt.urllib.request, 'urlopen', side_effect=side_effect)

    def test_retry_then_success(self):
        """① 前两次 URLError、第三次成功 ⇒ 返回数据且不抛。"""
        side = [OSError('URLError'), OSError('URLError'), _Resp(BODY_OK)]
        with self._patch(side) as up:
            out = mkt.fetch_batch(['sz000001', 'sz000002'])
        self.assertEqual(up.call_count, 3)
        self.assertIn('000001', out)
        self.assertEqual(self.sleep.call_count, 2)

    def test_all_attempts_fail_raises_original_type(self):
        """② 全部失败 ⇒ 抛原异常（非新异常），且绝不静默返回空 dict。"""
        with self._patch([OSError('URLError')] * mkt.FETCH_ATTEMPTS) as up:
            with self.assertRaises(OSError):
                mkt.fetch_batch(['sz000001'])
        self.assertEqual(up.call_count, mkt.FETCH_ATTEMPTS)

    def test_backoff_is_linear(self):
        """退避序列 = 0.3 / 0.6（第 n 次重试前 sleep BACKOFF * n）。"""
        with self._patch([OSError('URLError')] * mkt.FETCH_ATTEMPTS):
            with self.assertRaises(OSError):
                mkt.fetch_batch(['sz000001'])
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list],
                         [mkt.FETCH_BACKOFF, mkt.FETCH_BACKOFF * 2])

    def test_no_retry_on_success(self):
        with self._patch([_Resp(BODY_OK)]) as up:
            mkt.fetch_batch(['sz000001'])
        self.assertEqual(up.call_count, 1)
        self.assertEqual(self.sleep.call_count, 0)

    def test_timeout_is_not_retried(self):
        """③ 裸超时只尝试一次 —— 重试会放大整轮耗时。"""
        with self._patch([TimeoutError('timed out')]) as up:
            with self.assertRaises(TimeoutError):
                mkt.fetch_batch(['sz000001'])
        self.assertEqual(up.call_count, 1)
        self.assertEqual(self.sleep.call_count, 0)

    def test_urlerror_wrapping_timeout_is_not_retried(self):
        """③ urllib 常把底层超时包进 URLError.reason —— 同样不重试。"""
        exc = mkt.urllib.error.URLError(TimeoutError('timed out'))
        with self._patch([exc]) as up:
            with self.assertRaises(mkt.urllib.error.URLError):
                mkt.fetch_batch(['sz000001'])
        self.assertEqual(up.call_count, 1)

    def test_urlerror_wrapping_conn_error_is_retried(self):
        """对照：URLError 包连接层错误（非超时）仍应重试。"""
        exc = mkt.urllib.error.URLError(ConnectionResetError('reset'))
        with self._patch([exc, _Resp(BODY_OK)]) as up:
            out = mkt.fetch_batch(['sz000001'])
        self.assertEqual(up.call_count, 2)
        self.assertIn('000001', out)

    def test_parse_unchanged_on_success(self):
        """④ 解析口径与改动前一致（重试不得改变结果字段）。"""
        with self._patch([_Resp(BODY_OK)]):
            out = mkt.fetch_batch(['sz000001', 'sz000002'])
        self.assertEqual(out['000001']['name'], '平安银行')
        self.assertEqual(out['000001']['px'], 11.50)
        self.assertAlmostEqual(out['000001']['chg'], (11.50 / 11.40 - 1) * 100, places=6)
        self.assertEqual(out['000001']['vol'], 123456.0)
        self.assertEqual(out['000001']['amt'], 98765.4)
        self.assertEqual(out['000001']['turn'], 1.23)
        self.assertEqual(out['000002']['name'], '万科A')

    def test_empty_body_returns_empty_dict_without_retry(self):
        """空响应体是「该批无数据」，不是异常 ⇒ 不重试、返回 {}（保持原行为）。"""
        with self._patch([_Resp(b'')]) as up:
            out = mkt.fetch_batch(['sz000001'])
        self.assertEqual(out, {})
        self.assertEqual(up.call_count, 1)

    def test_attempts_config_is_sane(self):
        """配置棘轮：重试次数与退避保持「短重试」量级，防止误调成数十秒级。"""
        self.assertGreaterEqual(mkt.FETCH_ATTEMPTS, 2)
        self.assertLessEqual(mkt.FETCH_ATTEMPTS, 4)
        self.assertLessEqual(mkt.FETCH_BACKOFF * (mkt.FETCH_ATTEMPTS - 1), 1.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
