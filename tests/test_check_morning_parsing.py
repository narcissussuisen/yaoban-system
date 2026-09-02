"""A2(批次 A, P0 计划 v2.1 §3.1): check_morning.py 晨检解析单测。

覆盖:
- task_info() 对 zh-CN 中文键(schtasks GBK 解码后字符串)的解析行为
- subprocess.run 调用参数必须含 encoding='gbk' 与 errors='replace'(A1 直接证据)
- norm_date / result_zero 纯函数
- today_shanghai() 时区固定(A1b, ZoneInfo('Asia/Shanghai') 不依赖本机时区)
- 回归锚: 无 encoding 时 GBK 字节被替换符化, 中文键解析为空(9/2 晨检假阴性根因)

措辞依据用户终审(2026-09-02 13:39): mock 返回 UTF-8 解码后的字符串(模拟
text=True + encoding='gbk' 解码产物), 字节级端到端解码由 A4 生产验证承担。
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from datetime import datetime
from subprocess import CompletedProcess
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_morning as cm  # noqa: E402

# zh-CN schtasks /FO LIST /V 输出的中文键样例(GBK 解码后的文本形态)
CN_KEYS = ('上次运行时间: 2026/9/2 8:45:45\r\n'
           '上次结果: 0x0\r\n'
           '计划任务状态: 正在运行\r\n'
           '下次运行时间: 2026/9/3 8:45:00\r\n')
EN_KEYS = ('Last Run Time: 2026/9/2 8:50:50\r\n'
           'Last Result: 0\r\n')


def _cp(stdout: str) -> CompletedProcess:
    return CompletedProcess(args=[], returncode=0, stdout=stdout, stderr='')


class TaskInfoParsingTests(unittest.TestCase):
    def test_chinese_keys_parsed_from_gbk_decoded_text(self):
        """中文键解析 + A1 调用参数证据(encoding='gbk' / errors='replace')。"""
        with mock.patch.object(cm.subprocess, 'run', return_value=_cp(CN_KEYS)) as m:
            info = cm.task_info('YaobanPreflight')
        self.assertEqual(info['Last Run Time'], '2026/9/2 8:45:45')
        self.assertEqual(info['Last Result'], '0x0')
        self.assertEqual(info['_lr_date'], '2026-09-02')
        self.assertEqual(info['Scheduled Task State'], '正在运行')
        self.assertEqual(info['Next Run Time'], '2026/9/3 8:45:00')
        # A1 直接证据: subprocess.run 必须显式 GBK 解码
        kw = m.call_args.kwargs
        self.assertEqual(kw.get('encoding'), 'gbk')
        self.assertEqual(kw.get('errors'), 'replace')
        self.assertTrue(kw.get('capture_output'))
        self.assertTrue(kw.get('text'))

    def test_english_keys_still_supported(self):
        with mock.patch.object(cm.subprocess, 'run', return_value=_cp(EN_KEYS)):
            info = cm.task_info('X')
        self.assertEqual(info['Last Run Time'], '2026/9/2 8:50:50')
        self.assertEqual(info['_lr_date'], '2026-09-02')

    def test_regression_anchor_mojibake_keys_parse_empty(self):
        """回归锚(文档性): 旧行为无 encoding 时 GBK 字节按 UTF-8 解码成替换符,
        中文键不可匹配 → last_run 恒空——即 9/2 08:58 晨检假阴性的根因现场。"""
        bad = CN_KEYS.encode('gbk').decode('utf-8', errors='replace')
        with mock.patch.object(cm.subprocess, 'run', return_value=_cp(bad)):
            info = cm.task_info('YaobanPreflight')
        self.assertNotIn('Last Run Time', info)
        self.assertNotIn('Last Result', info)


class NormDateResultZeroTests(unittest.TestCase):
    def test_norm_date(self):
        self.assertEqual(cm.norm_date('2026/9/2 8:45:45'), '2026-09-02')
        self.assertEqual(cm.norm_date('2026/12/31'), '2026-12-31')
        self.assertEqual(cm.norm_date('2026/9/2'), '2026-09-02')
        self.assertEqual(cm.norm_date('garbage'), 'garbage')

    def test_result_zero(self):
        self.assertTrue(cm.result_zero('0'))
        self.assertTrue(cm.result_zero('0x0'))
        self.assertTrue(cm.result_zero(0))
        self.assertFalse(cm.result_zero('0x15'))   # 21 = gate 拦截码
        self.assertFalse(cm.result_zero(''))
        self.assertFalse(cm.result_zero(None))


class TimezoneTests(unittest.TestCase):
    """A1b(E12): 默认日期取值必须显式 Asia/Shanghai, 不依赖本机时区。"""

    def test_today_shanghai_source_anchor(self):
        src = (ROOT / 'scripts' / 'check_morning.py').read_text(encoding='utf-8')
        self.assertIn('Asia/Shanghai', src)
        self.assertIn('def today_shanghai', src)
        self.assertIn('day = args.date or today_shanghai()', src)
        self.assertNotIn('datetime.date.today()', src)

    def test_today_shanghai_matches_independent_clock(self):
        expected = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
        self.assertEqual(cm.today_shanghai(), expected)


if __name__ == '__main__':
    unittest.main(verbosity=2)
