# -*- coding: utf-8 -*-
"""D11 brain 卖出裁量接线测试（2026-09-13）。

覆盖四层：
 ① `TRIGGER_SOP` 与**生产 reason** 的对齐（含真读 `persona/sop_v0.toml` 验证原语非空）；
 ② `brain.sell_verdict` 的 `sop_rule`/`sop_weak` 留痕，以及「brain 内部降级仍回 hold」未被篡改；
 ③ `tick_monitor.brain_sell_decide` 的判定网关 —— halve 基数为可卖持仓、真 hold 才跳过、
    **降级一律回落机械原量**（安全核心）、开关关闭为纯透传、halve 后不足 1 手；
 ④ 源码锚点 —— 判定先于执行、hold 用 continue、**不新增 return 4/5 终止路径**。

⭐ 全程**离线**：`llm.consult` 与 `facts.fact_pack` 均被 mock，不联网、不读行情数据。
运行：python -m unittest tests.test_brain_sell_wiring -v
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / 'src', ROOT / 'scripts', ROOT / 'portfolio'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import ledger                                            # noqa: E402
from decision_chain import brain                         # noqa: E402

# 生产**实际会发出**的卖出 reason 全集（依据 config/parameters.toml [sell.intraday]
# + tick_monitor 强制关闭项 ten_oclock/t_enabled/dragon_link_sell/sector_retreat_sell）
PROD_REASONS = ['stop_loss', 'vwap_halve', 'vwap_break_all', 'break_low', 'profit_take',
                'zhaban_sell', 'second_high', 'ma10_clear', 'ma5_halve', 'time_stop']

_PACK = {'holding': {'has_position': True, 'qty': 5000, 'cost': 10.903},
         'position': {'px_vs_ma10_pct': -3.35},
         'intraday': {'px_vs_vwap_pct': 0.1},
         'env': {'sentiment_temp': 41.4}}


def _verdict(action, status='ok', degraded=False, choice=None, reason='r'):
    """构造 brain.sell_verdict 的返回（默认 choice 与 action 一致）。"""
    return dict(action=action, choice=(action if choice is None else choice),
                status=status, degraded=degraded, reason=reason, facts=_PACK)


class TriggerSopMappingTests(unittest.TestCase):
    """① 映射对齐 —— 这是让 brain 不静默退化的前提。"""

    def test_production_reasons_all_present(self):
        missing = [r for r in PROD_REASONS if r not in brain.TRIGGER_SOP]
        self.assertEqual(missing, [], f'生产 reason 未登记映射: {missing}')

    def test_nine_reasons_resolve_nonempty_original_text(self):
        """真读 sop_v0.toml：确认映射不是"指向了不存在的规则"。"""
        for r in PROD_REASONS:
            rule, _weak = brain.sop_for(r)
            if not rule:
                continue
            stmt = (brain._load_sop(rule).get('stmt') or '').strip()
            self.assertTrue(stmt, f'{r} -> {rule} 取不到 SOP 原语（静默退化风险）')

    def test_vwap_break_all_is_the_only_gap(self):
        self.assertEqual(brain.TRIGGER_SOP['vwap_break_all'], '')
        self.assertEqual(brain._load_sop(''), {})
        no_rule = [r for r in PROD_REASONS if not brain.sop_for(r)[0]]
        self.assertEqual(no_rule, ['vwap_break_all'])

    def test_expected_verbatim_mappings(self):
        expect = {'stop_loss': 'GEN-HOLD-28', 'vwap_halve': 'GEN-HOLD-05',
                  'vwap_break_all': '', 'break_low': 'GEN-HOLD-32',
                  'profit_take': 'GEN-HOLD-07', 'zhaban_sell': 'GEN-HOLD-08',
                  'second_high': 'GEN-HOLD-09', 'ma10_clear': 'GEN-HOLD-01',
                  'ma5_halve': 'GEN-HOLD-01', 'time_stop': 'GEN-HOLD-16'}
        for k, v in expect.items():
            self.assertEqual(brain.TRIGGER_SOP.get(k), v, f'{k} 映射不符')

    def test_legacy_keys_retained(self):
        """legacy 键（尤其是 below_ma10 = 9/11 诺普信事故锚点）不得被清理。"""
        for k in ['ten_oclock', 'break_vwap', 'below_ma5', 'below_ma10', 'below_ma20',
                  'zhaban', 'structure_break', 'expectation_fulfilled']:
            self.assertIn(k, brain.TRIGGER_SOP)
        self.assertEqual(brain.TRIGGER_SOP['below_ma10'], 'GEN-HOLD-40')

    def test_sop_weak_set_is_exactly_vwap_pair(self):
        self.assertEqual(brain.SOP_WEAK, {'vwap_halve', 'vwap_break_all'})

    def test_unknown_reason_marked_weak(self):
        """未登记 reason 必须判为弱依据 —— 这就是 sop_missing 留痕，防 9/11 式静默退化复发。"""
        self.assertEqual(brain.sop_for('__no_such_trigger__'), ('', True))


class SellVerdictOfflineTests(unittest.TestCase):
    """② brain 层：留痕字段 + 内部降级语义不被改动。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(brain, 'OUT_DIR', pathlib.Path(self.tmp.name)),
                  mock.patch.object(brain._facts, 'fact_pack', return_value=_PACK)):
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _seen_consult(choice, status='ok', degraded=False):
        """返回 (patch_ctx, captured) —— captured['kw'] 记录 consult 的实参。"""
        captured = {}

        def fake(point, **kw):
            captured.update(point=point, **kw)
            return dict(status=status, choice=choice, score=0.5, reason='离线',
                        _degraded=degraded)

        return mock.patch.object(brain._llm, 'consult', side_effect=fake), captured

    def test_clear_all_when_ok(self):
        ctx, _ = self._seen_consult('clear_all')
        with ctx:
            v = brain.sell_verdict('002215', day='2026-09-11', trigger='ma10_clear',
                                   persist=False)
        self.assertEqual(v['action'], 'clear_all')
        self.assertEqual(v['status'], 'ok')
        self.assertEqual(v['sop_rule'], 'GEN-HOLD-01')
        self.assertFalse(v['sop_weak'])

    def test_hold_when_ok(self):
        ctx, _ = self._seen_consult('hold')
        with ctx:
            v = brain.sell_verdict('002215', day='2026-09-11', trigger='vwap_halve',
                                   persist=False)
        self.assertEqual(v['action'], 'hold')
        self.assertTrue(v['sop_weak'], 'vwap_halve 必须被标为弱依据')

    def test_degrade_still_holds_inside_brain(self):
        """钉住 brain 内部语义：降级 ⇒ hold。**改动只允许发生在调用方**（见 TickBrainGateTests）。"""
        ctx, _ = self._seen_consult('clear_all', status='degraded', degraded=True)
        with ctx:
            v = brain.sell_verdict('002215', day='2026-09-11', trigger='stop_loss',
                                   persist=False)
        self.assertEqual(v['action'], 'hold')
        self.assertTrue(v['degraded'])

    def test_choice_out_of_enum_falls_back_to_hold(self):
        ctx, _ = self._seen_consult('sell_all')
        with ctx:
            v = brain.sell_verdict('002215', day='2026-09-11', trigger='zhaban_sell',
                                   persist=False)
        self.assertEqual(v['action'], 'hold')

    def test_consult_exception_returns_hold_not_raise(self):
        with mock.patch.object(brain._llm, 'consult', side_effect=RuntimeError('boom')):
            v = brain.sell_verdict('002215', day='2026-09-11', trigger='break_low',
                                   persist=False)
        self.assertEqual(v['action'], 'hold')
        self.assertEqual(v['status'], 'error')

    def test_llm_disabled_still_records_sop_fields(self):
        v = brain.sell_verdict('002215', day='2026-09-11', trigger='time_stop',
                               use_llm=False, persist=False)
        self.assertEqual(v['status'], 'llm_disabled')
        self.assertEqual(v['sop_rule'], 'GEN-HOLD-16')
        self.assertFalse(v['sop_weak'])

    def test_persist_writes_sop_fields_to_jsonl(self):
        ctx, _ = self._seen_consult('halve')
        with ctx:
            brain.sell_verdict('002215', day='2026-09-11', trigger='vwap_break_all',
                               persist=True)
        fp = pathlib.Path(self.tmp.name) / 'brain_2026-09-11.jsonl'
        self.assertTrue(fp.exists())
        import json
        row = json.loads(fp.read_text(encoding='utf-8').strip().splitlines()[-1])
        self.assertEqual(row['sop_rule'], '')
        self.assertTrue(row['sop_weak'], 'vwap_break_all 必须留痕为弱依据')

    def test_snapshot_hash_invariant_to_time_and_excludes_ts(self):
        """锁粒度必须是 (标的×日×卖点类型)：不同 now 不得改变 snapshot_hash。"""
        seen = []
        with mock.patch.object(brain._llm, 'consult',
                               side_effect=lambda point, **kw: (
                                   seen.append(kw['snapshot_hash']),
                                   dict(status='ok', choice='hold', score=0.1,
                                        reason='x'))[1]):
            brain.sell_verdict('002215', day='2026-09-11', trigger='stop_loss',
                               now=dt.datetime(2026, 9, 14, 9, 35), persist=False)
            brain.sell_verdict('002215', day='2026-09-11', trigger='stop_loss',
                               now=dt.datetime(2026, 9, 14, 14, 0), persist=False)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0], seen[1], 'snapshot_hash 不得随调用时刻变化')
        self.assertEqual(seen[0], brain._snapshot_hash('002215', '2026-09-11', 'stop_loss'))
        self.assertNotEqual(seen[0], brain._snapshot_hash('002215', '2026-09-11', 'vwap_halve'))


class TickBrainGateTests(unittest.TestCase):
    """③ 接线网关 —— 全部离线（sell_verdict 被打桩）。"""

    @staticmethod
    def _import_tick_monitor():
        fp = ROOT / 'scripts' / 'tick_monitor.py'
        spec = importlib.util.spec_from_file_location('tick_monitor_brain_under_test', fp)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def setUp(self):
        self.tm = self._import_tick_monitor()
        p = mock.patch.object(self.tm, 'BRAIN_SELL_ENABLED', True)
        p.start()
        self.addCleanup(p.stop)

    def _call(self, verdict=None, t1=1000, orig_qty=500, exc=None, trig='vwap_halve'):
        """驱动 brain_sell_decide；verdict/exc 用来打桩 brain.sell_verdict。"""
        if exc is not None:
            patcher = mock.patch.object(self.tm._brain, 'sell_verdict', side_effect=exc)
        else:
            patcher = mock.patch.object(self.tm._brain, 'sell_verdict',
                                        return_value=verdict or _verdict('clear_all'))
        with patcher as m:
            r = self.tm.brain_sell_decide('000001', trig, '2026-09-14', None, {},
                                          t1, orig_qty, 10.0, now=None)
        return r, m

    def test_halve_base_is_sellable_not_engine_qty(self):
        """决策 1：halve 基数 = 可卖持仓 t1（engine 给的 qty 对 vwap_halve 已是半仓，
        用它作基数会变 1/4 仓）。"""
        r, _ = self._call(_verdict('halve'), t1=1000, orig_qty=500)
        self.assertEqual(r['qty'], 500)          # 1000//2//100*100，而不是 500//2=250
        self.assertEqual(r['action'], 'halve')
        r2, _ = self._call(_verdict('halve'), t1=1000, orig_qty=1000)
        self.assertEqual(r2['qty'], 500, 'halve 结果必须与 engine 给的 qty 无关')

    def test_clear_all_uses_full_sellable(self):
        r, _ = self._call(_verdict('clear_all'), t1=1000, orig_qty=500)
        self.assertEqual(r['qty'], 1000)

    def test_genuine_hold_skips(self):
        r, _ = self._call(_verdict('hold'))
        self.assertTrue(r['hold'])
        self.assertIsNone(r['qty'])
        self.assertEqual(r['action'], 'hold')

    def test_degraded_falls_back_to_mechanical(self):
        """⭐ 安全核心：LLM 没真裁量成功时**不得**跳过卖出（否则 LLM 一挂 = 卖点全失效）。"""
        r, _ = self._call(_verdict('hold', status='degraded', degraded=True))
        self.assertFalse(r['hold'], '降级不得跳过该笔卖出')
        self.assertEqual(r['qty'], 500)          # = orig_qty 原量
        self.assertEqual(r['action'], 'mechanical')
        self.assertEqual(r['status'], 'degraded')

    def test_error_and_llm_disabled_fall_back(self):
        for st in ('error', 'llm_disabled', 'schema_failed'):
            r, _ = self._call(_verdict('hold', status=st, degraded=True))
            self.assertFalse(r['hold'], f'{st} 不得跳过卖出')
            self.assertEqual(r['qty'], 500)
            self.assertEqual(r['action'], 'mechanical')

    def test_sell_verdict_exception_falls_back(self):
        r, _ = self._call(exc=RuntimeError('boom'))
        self.assertFalse(r['hold'])
        self.assertEqual(r['qty'], 500)
        self.assertEqual(r['status'], 'error')

    def test_switch_off_is_pure_passthrough(self):
        with mock.patch.object(self.tm, 'BRAIN_SELL_ENABLED', False):
            with mock.patch.object(self.tm._brain, 'sell_verdict') as m:
                r = self.tm.brain_sell_decide('000001', 'stop_loss', '2026-09-14', None,
                                              {}, 1000, 500, 10.0, now=None)
        m.assert_not_called()
        self.assertEqual(r['qty'], 500)
        self.assertEqual(r['status'], 'disabled')
        self.assertFalse(r['sop_weak'])

    def test_halve_under_one_lot_yields_sub_lot(self):
        """halve 后不足 1 手 ⇒ 由调用方按 qty<100 落 alert_only，不得进执行抛错。"""
        r, _ = self._call(_verdict('halve'), t1=150, orig_qty=100)
        self.assertLess(r['qty'], 100)

    def test_sop_weak_propagated(self):
        r, _ = self._call(_verdict('hold'), trig='vwap_halve')
        self.assertTrue(r['sop_weak'])
        r2, _ = self._call(_verdict('hold'), trig='stop_loss')
        self.assertFalse(r2['sop_weak'])
        self.assertEqual(r2['sop'], 'GEN-HOLD-28')


class TickBrainContractTests(unittest.TestCase):
    """④ 源码锚点 —— 钉住"不打死 daemon"与"锁粒度不被破坏"。"""

    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / 'scripts' / 'tick_monitor.py').read_text(encoding='utf-8')
        cls.code = '\n'.join(ln for ln in cls.src.splitlines()
                             if not ln.lstrip().startswith('#'))

    def test_brain_imported(self):
        self.assertIn('from decision_chain import brain as _brain', self.src)

    def test_decide_called_before_execution(self):
        self.assertLess(self.src.index('brain_sell_decide(sym,trig'),
                        self.src.index('return execute_tick_risk_sell('))

    def test_hold_uses_continue_and_is_traced(self):
        self.assertIn("if b['hold']:", self.src)
        self.assertIn("action':'brain_hold'", self.src)
        self.assertIn('flush=True);continue', self.src)

    def test_no_new_terminating_return_codes(self):
        """可执行代码里 return 4 / return 5 必须各仅 1 处（注释不计）。"""
        self.assertEqual(len(re.findall(r'\breturn 4\b', self.code)), 1)
        self.assertEqual(len(re.findall(r'\breturn 5\b', self.code)), 1)

    def test_qty_capped_by_fresh_sellable(self):
        self.assertIn('min(qty,sellable_qty(st,sym,day))', self.src)
        self.assertNotIn('min(qty,int(t1))', self.src)

    def test_trigger_passed_verbatim_not_concatenated_with_ts(self):
        """brain 锁粒度不含 ts；拼接 fts 会导致每根 bar 真调一次 LLM。"""
        self.assertIn('brain_sell_decide(sym,trig,day,df,st,t1,qty,', self.src)
        self.assertNotIn("trigger=f'{trig}", self.src)
        self.assertNotIn('trig+fts', self.src)

    def test_kill_switch_defaults_to_off(self):
        m = re.search(r'BRAIN_SELL_ENABLED\s*=\s*os\.environ\.get\([^\n]*', self.src)
        self.assertIsNotNone(m)
        self.assertIn("== '1'", m.group(0), '开关应为白名单式启用（未设 = 关闭，先观察）')


if __name__ == '__main__':
    unittest.main(verbosity=2)
