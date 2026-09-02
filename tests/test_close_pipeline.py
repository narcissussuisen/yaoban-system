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
import os, pathlib, sys, tempfile, unittest
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

    def test_board_print_reflects_rc(self):
        # 终审三轮: 看板打印不得无条件宣称成功(9/2 close stdout 曾误导"看板已更新"而 rc=1)
        source = (ROOT / 'scripts' / 'close_pipeline.py').read_text(encoding='utf-8')
        self.assertNotIn("print('看板已更新'", source, '看板打印不得无条件宣称成功')
        self.assertIn('看板子任务 rc=', source)


class CloseDecisionSchemaTests(unittest.TestCase):
    """终审三轮方案A(2026-09-02): close_decision 扩展 schema 使 G1 判据可机械核对。"""

    def _doc(self, revision=14):
        decisions = {'date': '2026-09-03',
                     'buys': [{'sym': '600000', 'ts': '09:45', 'px': 10.0, 'kind': 'B', 'seg': 'AM'}],
                     'sells': [], 't': [], 'notes': ['600000: 无当日分钟']}
        st = {'account': {'positions': {'600000': {'qty': 100, 'cost': 10.0},
                                        '300468': {'qty': 1800, 'cost': 24.61}},
                          'cash': 50000.0, 'fills': [], 'equity_curve': []},
              '_revision': revision}
        mark = {'600000': 10.2, '300468': 24.5}
        eq = 50000.0 + 100 * 10.2 + 1800 * 24.5
        return cp.build_close_decision(decisions, st, eq, mark), st, eq, mark

    def test_new_fields_present(self):
        doc, st, eq, mark = self._doc()
        for key in ('run_id', 'generated_at', 'ledger_revision', 'equity', 'positions'):
            self.assertIn(key, doc, f'缺少方案A字段 {key}')
        self.assertEqual(doc['ledger_revision'], 14)
        self.assertEqual(doc['equity'], round(eq, 2))
        self.assertEqual(doc['positions']['600000'],
                         {'qty': 100, 'close_px': 10.2, 'market_value': 1020.0})

    def test_old_fields_preserved(self):
        doc, *_ = self._doc()
        self.assertEqual(doc['date'], '2026-09-03')
        self.assertEqual(doc['buys'][0]['sym'], '600000')
        self.assertEqual(doc['sells'], [])
        self.assertEqual(doc['t'], [])
        self.assertEqual(doc['notes'], ['600000: 无当日分钟'])

    def test_run_id_embeds_day(self):
        # B2 语义: run_id 可辨识生成于当日本次 run
        doc, *_ = self._doc()
        self.assertTrue(doc['run_id'].startswith('close-2026-09-03-'), doc['run_id'])

    def test_g1_mechanical_comparison(self):
        # G1 判据(方案A): close_doc.equity == equity_curve 当日末点;
        # cash 可由 equity - sum(market_value) 反推; mv == qty*close_px; close_px == mark
        doc, st, eq, mark = self._doc()
        from ledger import equity as _eq_fn
        _eq_fn(st, '2026-09-03', mark)
        row = st['account']['equity_curve'][-1]
        self.assertEqual(doc['equity'], row['equity'])
        self.assertEqual(doc['equity'],
                         round(row['cash'] + sum(p['market_value'] for p in doc['positions'].values()), 2))
        for sym, p in doc['positions'].items():
            self.assertEqual(p['market_value'], round(p['qty'] * p['close_px'], 2))
            self.assertEqual(p['close_px'], mark[sym])

    def test_main_writes_expanded_doc(self):
        source = (ROOT / 'scripts' / 'close_pipeline.py').read_text(encoding='utf-8')
        self.assertIn('close_doc = build_close_decision(decisions, st, eq, mark)', source)
        self.assertIn('json.dumps(close_doc', source)


LAUNCH_PS1 = pathlib.Path(r'C:\Users\YZP\WorkBuddy\yaoban_tasks\launch.ps1')


class SharedRunIdTests(unittest.TestCase):
    """复核四轮(2026-09-02 19:48 用户裁定: 共享 run_id)——G1 scheduled-run 归属升级为精确关联。

    launch.ps1 生成 run_id 并以 YAOBAN_RUN_ID 注入, task log 与 close_decision 双写同值;
    G1 机械核对 task_log.run_id == close_decision.run_id。
    """

    def _minimal(self):
        decisions = {'date': '2026-09-03', 'buys': [], 'sells': [], 't': [], 'notes': []}
        st = {'account': {'positions': {}, 'cash': 100.0}, '_revision': 3}
        return decisions, st

    def test_run_id_uses_env_when_present(self):
        # scheduled 路径: launch.ps1 注入 YAOBAN_RUN_ID → 产物透传同值(精确等值核对基础)
        env_run_id = 'run-20260903_151000_996'
        decisions, st = self._minimal()
        with mock.patch.dict(os.environ, {'YAOBAN_RUN_ID': env_run_id}):
            doc = cp.build_close_decision(decisions, st, 100.0, {})
        self.assertEqual(doc['run_id'], env_run_id)

    def test_run_id_empty_env_falls_back(self):
        # env 为空串(或未注入的手动运行) → 回退自造 close-* ID, 不得充当 scheduled-run 证据
        decisions, st = self._minimal()
        with mock.patch.dict(os.environ, {'YAOBAN_RUN_ID': ''}):
            doc = cp.build_close_decision(decisions, st, 100.0, {})
        self.assertTrue(doc['run_id'].startswith('close-2026-09-03-'), doc['run_id'])

    @unittest.skipUnless(LAUNCH_PS1.exists(), 'launch.ps1 仅存在于部署机')
    def test_launch_wrapper_injects_shared_run_id(self):
        # task log 写入方(launch.ps1)须生成并注入共享 run_id, 且注入必须先于子进程启动
        source = LAUNCH_PS1.read_text(encoding='utf-8')
        self.assertIn('$env:YAOBAN_RUN_ID=$runId', source, 'launch.ps1 未注入 YAOBAN_RUN_ID')
        self.assertIn('run_id=$runId', source, 'task log $meta 缺 run_id 字段')
        env_at = source.index('$env:YAOBAN_RUN_ID=')
        start_at = source.index('Start-Process')
        self.assertLess(env_at, start_at, 'YAOBAN_RUN_ID 必须在 Start-Process 之前注入(否则子进程不继承)')


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
