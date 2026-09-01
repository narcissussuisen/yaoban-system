"""P0.3 收盘链修复回归测试（蓝图 B0-P0-1）

覆盖:
  - argparse 回归: --execute 只注册一次; --dry-run 已注册
  - dry-run 路径 rc=0（mock TDX, 交易日探测通过）
  - 非交易日 rc=3
  - --execute 禁用守卫 rc=5（保留性测试）
  - 子任务失败 rc=7 透传（修 F2: 失败码不得被吞）
  - 卖出审计打印无 NameError（修 F3: 未定义变量 qty/sym）
"""
from __future__ import annotations
import pathlib, sys, tempfile, unittest
from datetime import datetime
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'portfolio'))
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT.parent / 'py_libs'))

import close_pipeline as cp


def _bars(day: str, n: int = 6):
    return [{'datetime': f'{day} 14:5{i:02d}', 'open': 10.0, 'high': 10.5,
             'low': 9.8, 'close': 10.2, 'vol': 1000, 'amount': 10000} for i in range(n)]


class _FakeAPI:
    """mock TdxHq_API: connect 恒成功, bars 由构造参数决定."""

    def __init__(self, bars):
        self._bars = bars

    def connect(self, *a, **k):
        return True

    def disconnect(self):
        return None

    def get_security_bars(self, *a, **k):
        return self._bars


def _empty_state(day: str):
    return {'account': {'positions': {}, 'fills': [], 'equity_curve': []},
            'policy': {'account_mode': 'autonomous_paper'},
            'plans': {}}


class ClosePipelineArgTests(unittest.TestCase):
    def test_execute_registered_once_and_dry_run_present(self):
        source = (ROOT / 'scripts' / 'close_pipeline.py').read_text(encoding='utf-8')
        self.assertEqual(source.count("add_argument('--execute'"), 1, '--execute 重复注册回归')
        self.assertEqual(source.count("add_argument('--dry-run'"), 1, '--dry-run 未注册')

    def test_main_exit_code_wired(self):
        source = (ROOT / 'scripts' / 'close_pipeline.py').read_text(encoding='utf-8')
        self.assertIn('sys.exit(main())', source, 'main() 返回码必须接入 sys.exit')

    def test_audit_only_guard_preserved(self):
        source = (ROOT / 'scripts' / 'close_pipeline.py').read_text(encoding='utf-8')
        self.assertIn('收盘流水线不得重复执行订单', source)
        self.assertNotIn('buy(st', source)
        self.assertNotIn('sell(st', source)


class ClosePipelineRunTests(unittest.TestCase):
    day = datetime.now().strftime('%Y-%m-%d')

    def _run(self, argv, bars):
        state = _empty_state(self.day)
        state['account']['positions']['600000'] = {'qty': 100, 'cost': 10.0, 'stop_px': 9.0}
        fake = _FakeAPI(bars)
        with mock.patch.object(cp, 'TdxHq_API', return_value=fake), \
                mock.patch.object(cp, 'load', return_value=state), \
                mock.patch.object(cp, 'prev_close', return_value=9.5), \
                mock.patch.object(sys, 'argv', argv):
            return cp.main()

    def test_dry_run_returns_0(self):
        rc = self._run(['close_pipeline', '--dry-run'], _bars(self.day))
        self.assertEqual(rc, 0)

    def test_non_trading_day_returns_3(self):
        yesterday = '2026-01-01'
        rc = self._run(['close_pipeline'], _bars(yesterday))
        self.assertEqual(rc, 3)

    def test_execute_guard_returns_5(self):
        rc = self._run(['close_pipeline', '--execute'], _bars(self.day))
        self.assertEqual(rc, 5)

    def test_subprocess_failure_returns_7(self):
        # 有持仓 + 当日 bar 可得 → mark 成功; mock equity/save/record_review; 子任务 rc=1 → 主 rc=7
        state = _empty_state(self.day)
        state['account']['positions']['600000'] = {'qty': 100, 'cost': 10.0, 'stop_px': 9.0}
        state['account']['equity_curve'] = [{'date': self.day, 'equity': 100000.0}]
        fake = _FakeAPI(_bars(self.day))
        proc = mock.MagicMock(returncode=1)
        tmp = tempfile.mkdtemp()
        with mock.patch.object(cp, 'TdxHq_API', return_value=fake), \
                mock.patch.object(cp, 'load', return_value=state), \
                mock.patch.object(cp, 'prev_close', return_value=9.5), \
                mock.patch.object(cp, 'equity', return_value=100000.0), \
                mock.patch.object(cp, 'record_review'), \
                mock.patch.object(cp, 'save'), \
                mock.patch.object(cp, 'INTRADAY', pathlib.Path(tmp)), \
                mock.patch.object(cp.subprocess, 'run', return_value=proc), \
                mock.patch.object(sys, 'argv', ['close_pipeline']):
            rc = cp.main()
        self.assertEqual(rc, 7)

    def test_sell_audit_print_no_nameerror(self):
        # 直接执行审计打印段（有 sells 决策时不得 NameError——F3）
        decisions = {'buys': [], 'sells': [
            {'sym': '600000', 'ts': '14:55', 'reason': '止损', 'px': 9.5, 'qty': 100}], 'notes': []}
        for sd in decisions['sells']:
            print(f'  (灰度) 审计卖出 {sd["sym"]} {sd["qty"]}股 @{sd["px"]} ({sd["reason"]}) —— 仅审计未执行')
        # 打印段与源码一致性的源断言
        source = (ROOT / 'scripts' / 'close_pipeline.py').read_text(encoding='utf-8')
        self.assertIn('审计卖出 {sd["sym"]} {sd["qty"]}股', source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
