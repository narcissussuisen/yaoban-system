# -*- coding: utf-8 -*-
"""战法池标定修复回归锚（2026-09-14 用户裁定）。

本文件钉三件事，每条都对应一个**已实测的缺陷**，且都能复现修前形态：

① `zt_huicai` **全池 0 命中**的根因 —— `config/parameters.toml` 在 2026-09-11 把
   `volume_shrink_ratio` 设为 **0.0**（语义明写「0 = 不启用缩量过滤器」），但
   `detect_zt_huicai` 仍无条件套用 `vol_ratio[i] < shrink`
   ⇒ 判据退化成 `vol_ratio < 0.0`，而 `vol_shrink_ratio` 恒 ≥ 0（`v5/v5_prev`）
   ⇒ **该检测器 100% 返回全 False**（战法池 `by_pattern` 里 zt_huicai 命中 0）。

② **池层剔除 ST / 退市风险**（实测池里有 `000078 ST海王`、`000909 *ST数源`）。
   ⚠️ 同时钉住「名称缺失**不**排除」—— 收窄不得靠静默丢票。

③ **剔除「仅反包命中」**（`patterns == ['qu_shi_fanbao']`）—— 用户实测的**零损失收窄**
   （池 2308→1720，选手 4 只仍存活 3/4）。⚠️ 同时钉住「反包 + 其它战法共振**必须保留**」，
   别把收敛写成"见反包就杀"。

另附：`plan_daily.build_pattern_artifact` 与 `build_pattern_pool.py` **共用同一写入口**
（`core.pattern_pool.write_pattern_pool`），schema 不得漂移 —— scan 侧
`load_pattern_pool` 的读取契约依赖它。
"""
from __future__ import annotations
import ast
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'portfolio'))
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT.parent / 'py_libs'))

from core import strategies as S            # noqa: E402
from core import indicators as ind          # noqa: E402
from core.pattern_pool import build_pattern_pool, write_pattern_pool  # noqa: E402

ASOF = '2026-08-31'
DAY = '2026-09-01'


def _zt_pullback_df(n_flat: int = 30, zt_idx: int = 30, total: int = 40) -> pd.DataFrame:
    """构造「涨停 + 回踩不破涨停日最低价 + 站上 MA5/MA20」的日线。

    目的不是拟合真实行情，而是让 `detect_zt_huicai` 的**三条判据全部成立**，
    这样唯一的失败原因就只剩「缩量过滤器被误启用」。
    """
    close = [10.0] * total
    high = [10.05] * total
    low = [9.95] * total
    open_ = [10.0] * total
    vol = [1_000_000.0] * total
    # 涨停柱：10.0 → 11.0（+10%），实体低点 10.5
    close[zt_idx], open_[zt_idx] = 11.0, 10.1
    high[zt_idx], low[zt_idx] = 11.0, 10.5
    vol[zt_idx] = 2_000_000.0
    # 回踩：收盘小阴小阳，最低价 10.6 > 10.5（不破涨停板实体低点）
    for k in range(zt_idx + 1, total):
        close[k], open_[k] = 10.9, 10.95
        high[k], low[k] = 11.0, 10.6
    return pd.DataFrame({
        'date': pd.date_range('2025-01-01', periods=total, freq='D').strftime('%Y-%m-%d'),
        'open': open_, 'high': high, 'low': low, 'close': close, 'volume': vol,
    })


class ZtHuicaiShrinkFilterTests(unittest.TestCase):
    """① 缩量过滤器：`0 = 关闭` 的配置语义必须被尊重。"""

    def setUp(self):
        self.df = _zt_pullback_df()

    def test_config_actually_sets_zero(self):
        """前提自检：配置里必须是 0.0（否则本组用例的前提已变，需重写）。"""
        self.assertEqual(float(S.CFG_ZT.get('volume_shrink_ratio', -1)), 0.0)

    def test_detects_when_filter_disabled(self):
        mask = S.detect_zt_huicai(self.df)
        self.assertTrue(bool(mask.any()),
                        'zt_huicai 全 False ⇒ 缩量过滤器又被无条件套用（vol_ratio < 0.0 恒假）')
        self.assertTrue(bool(mask.iloc[33]), '回踩第 3 个交易日应被识别为信号')

    def test_shrink_ratio_is_never_negative(self):
        """根因锚：`vol_shrink_ratio` 恒 ≥ 0 ⇒ `< 0.0` 是永假式。"""
        r = ind.vol_shrink_ratio(self.df).dropna()
        self.assertTrue((r >= 0).all(), 'vol_shrink_ratio 出现负值，本测试的根因论证需重做')

    def test_positive_threshold_still_applies(self):
        """设 >0 时过滤器必须继续生效（消融回归还要用它，不能一刀切删掉）。"""
        with mock.patch.dict(S.CFG_ZT, {'volume_shrink_ratio': 0.01}):
            mask = S.detect_zt_huicai(self.df)
        self.assertFalse(bool(mask.any()), '阈值 >0 时缩量过滤器未生效')


def _df_flat(n=90, start='2026-01-01'):
    return pd.DataFrame({
        'date': pd.date_range(start, periods=n, freq='D').strftime('%Y-%m-%d'),
        'open': [10.0] * n, 'high': [10.05] * n, 'low': [9.95] * n,
        'close': [10.0] * n, 'volume': [1e6] * n,
    })


def _always_hit(df, sym=None):
    """所有票都在**信号窗口内最后一根**报信号（够进池即可，形态不做要求）。

    ⚠️ 2026-09-14：`DETECTORS` 的契约改为**双参 `(df, sym)`**（`zt_huicai` 需 sym 才能
    按板块判涨停），故本替身同步加 `sym=None`。若替身仍为单参，`build_pattern_pool:168`
    的双参调用会抛 TypeError，并被该函数的 `except Exception: continue` **静默吞掉**
    ⇒ 池变空、且不报错（本文件这 4 个用例就是靠这个才暴露出来的）。
    """
    s = pd.Series([False] * len(df), index=df.index)
    s.iloc[-1] = True
    return s


class PoolFilterTests(unittest.TestCase):
    """② 池层 ST 剔除；③ 仅反包剔除（含两条**不得误伤**的反向锚）。"""

    def _dmap(self, syms=('600000', '000078', '000909', '600001')):
        return {s: _df_flat() for s in syms}

    def test_st_and_star_excluded(self):
        names = {'600000': '浦发银行', '000078': 'ST海王', '000909': '*ST数源',
                 '600001': '邯郸钢铁'}
        with mock.patch('core.pattern_pool.DETECTORS', {'huigui': _always_hit}):
            pool, stats = build_pattern_pool(self._dmap(), asof=ASOF, names=names)
        self.assertEqual(stats['n_excluded_st'], 2, 'ST/*ST 未被池层剔除')
        syms = {r['sym'] for r in pool}
        self.assertNotIn('000078', syms)
        self.assertNotIn('000909', syms)

    def test_missing_name_is_not_excluded(self):
        """名称缺失只计数、不排除 —— 收窄不得靠静默丢票。"""
        names = {'600000': '浦发银行'}          # 其余三只无名称
        with mock.patch('core.pattern_pool.DETECTORS', {'huigui': _always_hit}):
            pool, stats = build_pattern_pool(self._dmap(), asof=ASOF, names=names)
        self.assertEqual(stats['n_no_name'], 3)
        self.assertEqual(len(pool), 4, '缺名票被误排除（静默丢票）')

    def _build_via_router(self, routes, exclude_fanbao_only=True):
        """按票构造「哪些战法命中」的精确场景。

        手法：把 sym 身份编码进**首根收盘价**（600000→10.0 / 600001→20.0），
        检测器据此决定该票是否报信号 —— 避免依赖 `df.attrs` 在
        `reset_index()` 后的传播行为（那是实验特性，不稳）。
        """
        px = {s: 10.0 * (i + 1) for i, s in enumerate(routes)}
        by_px = {px[s]: pats for s, pats in routes.items()}
        dmap = {s: _df_flat() for s in routes}
        for s, df in dmap.items():
            df.loc[:, 'close'] = px[s]
            df.loc[:, 'open'] = px[s]
            df.loc[:, 'low'] = px[s] * 0.995
            df.loc[:, 'high'] = px[s] * 1.005

        def _mk(pname):
            def _fn(df, sym=None):        # 双参契约，见 `_always_hit` 的注释
                s = pd.Series([False] * len(df), index=df.index)
                if pname in by_px.get(float(df['close'].iloc[0]), set()):
                    s.iloc[-1] = True
                return s
            return _fn

        det = {p: _mk(p) for p in ('huigui', 'zt_huicai', 'xianren', 'qu_shi_fanbao')}
        with mock.patch('core.pattern_pool.DETECTORS', det):
            return build_pattern_pool(dmap, asof=ASOF, names={},
                                      exclude_fanbao_only=exclude_fanbao_only)

    def test_fanbao_only_excluded_but_resonance_kept(self):
        """仅反包 ⇒ 剔除；反包 + 上升回档共振 ⇒ 必须保留。"""
        pool, stats = self._build_via_router({'600000': {'qu_shi_fanbao'},
                                              '600001': {'huigui', 'qu_shi_fanbao'}})
        self.assertEqual(stats['n_excluded_fanbao_only'], 1, '仅反包未被剔除')
        self.assertEqual([r['sym'] for r in pool], ['600001'],
                         '仅反包未剔除，或反包共振票被误杀（收敛写成了"见反包就杀"）')
        self.assertEqual(pool[0]['patterns'], ['huigui', 'qu_shi_fanbao'],
                         '共振信息丢失 ⇒ scan 侧排序权重（共振数）会失真')

    def test_keep_flag_restores_fanbao_only(self):
        """`--keep-fanbao-only` 必须能还原（修前 2308 只的口径要可复现）。"""
        pool, stats = self._build_via_router({'600000': {'qu_shi_fanbao'}},
                                             exclude_fanbao_only=False)
        self.assertEqual(stats['n_excluded_fanbao_only'], 0)
        self.assertEqual(len(pool), 1)

    def test_huigui_only_never_excluded(self):
        """非反包的单战法命中不得被误剔。"""
        pool, stats = self._build_via_router({'600000': {'huigui'}})
        self.assertEqual(len(pool), 1)
        self.assertEqual(stats['n_excluded_fanbao_only'], 0)


def _df_with_zt(zt_on_last: bool = True, total: int = 90, sym_close_from: float = 10.0) -> pd.DataFrame:
    """构造「尾根涨停(+10%) / 不涨停」的日线（主板 10cm 口径）。"""
    close = [sym_close_from] * total
    open_ = [sym_close_from] * total
    high = [sym_close_from * 1.005] * total
    low = [sym_close_from * 0.995] * total
    vol = [1e6] * total
    if zt_on_last:
        close[-1] = round(close[-2] * 1.10, 2)      # +10% 涨停
        open_[-1] = sym_close_from
        high[-1] = close[-1]
        low[-1] = sym_close_from
        vol[-1] = 2e6
    return pd.DataFrame({
        'date': pd.date_range('2026-01-01', periods=total, freq='D').strftime('%Y-%m-%d'),
        'open': open_, 'high': high, 'low': low, 'close': close, 'volume': vol,
    })


class ZtWatchTests(unittest.TestCase):
    """④ zt_watch「涨停次日观察」（2026-09-15 新增，用户裁定「今天实盘前上线」）。

    机制依据：选手 32 只候选池样本中 14/32 在 asof 当日或前 1–2 日涨停（武汉凡谷
    9/8 涨停 → 9/9 进候选池；博敏 9/11 涨停 → 9/14 被买入）⇒ T−1 涨停 → 次日晨间
    列入观察。`zt_huicai` 抓不到这类票（它要求涨停在信号日之前）⇒ 池比选手晚一天。
    """

    NAMES = {'600000': '浦发银行'}

    def test_asof_limit_up_enters_pool_as_zt_watch(self):
        dmap = {'600000': _df_with_zt(zt_on_last=True)}
        with mock.patch('core.pattern_pool.DETECTORS', {}):     # 关掉全部检测器，只测 zt_watch
            pool, stats = build_pattern_pool(dmap, asof=ASOF, names=self.NAMES)
        self.assertEqual(len(pool), 1)
        r = pool[0]
        self.assertEqual(r['pattern'], 'zt_watch')
        self.assertEqual(r['pattern_cn'], '涨停次日观察')
        # sig_date = 涨停日本身（数据末根），不是 asof 字符串（asof 可能晚于数据末根）
        self.assertEqual(r['sig_date'], '2026-03-31')
        self.assertEqual(r['bars_since_sig'], 0)          # 信号就在数据末根 = 涨停当日
        self.assertEqual(r['patterns'], ['zt_watch'])
        self.assertEqual(stats['n_zt_watch_added'], 1)
        self.assertEqual(stats['by_pattern'].get('zt_watch'), 1)

    def test_no_limit_up_no_entry(self):
        dmap = {'600000': _df_with_zt(zt_on_last=False)}
        with mock.patch('core.pattern_pool.DETECTORS', {}):
            pool, stats = build_pattern_pool(dmap, asof=ASOF, names=self.NAMES)
        self.assertEqual(pool, [])
        self.assertEqual(stats['n_zt_watch_added'], 0)

    def test_zt_watch_flag_off(self):
        dmap = {'600000': _df_with_zt(zt_on_last=True)}
        with mock.patch('core.pattern_pool.DETECTORS', {}):
            pool, stats = build_pattern_pool(dmap, asof=ASOF, names=self.NAMES, zt_watch=False)
        self.assertEqual(pool, [])
        self.assertEqual(stats['n_zt_watch_added'], 0)

    def test_no_duplicate_when_detector_already_hit(self):
        """已被其他战法命中的票**不得**再加 zt_watch 条目（防双计/排序失真）。"""
        dmap = {'600000': _df_with_zt(zt_on_last=True)}
        with mock.patch('core.pattern_pool.DETECTORS', {'huigui': _always_hit}):
            pool, stats = build_pattern_pool(dmap, asof=ASOF, names=self.NAMES)
        self.assertEqual(len(pool), 1)                    # 只一条
        self.assertEqual(pool[0]['pattern'], 'huigui')    # 原条目保持不变
        self.assertEqual(stats['n_zt_watch_added'], 0)

    def test_20cm_board_threshold(self):
        """20cm 票 +10% **不是**涨停 ⇒ 不得进 zt_watch（板块分档必须生效）。"""
        dmap = {'300124': _df_with_zt(zt_on_last=True)}   # +10%，但 300 前缀 = 20cm
        with mock.patch('core.pattern_pool.DETECTORS', {}):
            pool, stats = build_pattern_pool(dmap, asof=ASOF, names={'300124': '测试股'})
        self.assertEqual(pool, [])
        self.assertEqual(stats['n_zt_watch_added'], 0)

    def test_20cm_board_real_limit_up(self):
        """20cm 票 +20% **是**涨停 ⇒ 进 zt_watch。"""
        df = _df_with_zt(zt_on_last=False)
        df.loc[df.index[-1], 'close'] = round(float(df['close'].iloc[-2]) * 1.20, 2)
        dmap = {'300124': df}
        with mock.patch('core.pattern_pool.DETECTORS', {}):
            pool, stats = build_pattern_pool(dmap, asof=ASOF, names={'300124': '测试股'})
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]['pattern'], 'zt_watch')


class WriteEntryPointTests(unittest.TestCase):
    """schema 单点：两个生产入口共用 `write_pattern_pool`，扫描侧契约不漂移。"""

    def test_scan_contract_fields_present(self):
        with tempfile.TemporaryDirectory() as td:
            fp = write_pattern_pool(DAY, ASOF, 4, ('huigui',), [], {'n_pool': 0}, {},
                                    out_dir=pathlib.Path(td))
            doc = json.loads(fp.read_text(encoding='utf-8'))
            for k in ('day', 'asof', 'lookback', 'patterns', 'stats', 'pool', 'name_map',
                      'built_at', 'source'):
                self.assertIn(k, doc, f'战法池产物缺字段 {k} ⇒ scan 侧读取契约破裂')
            self.assertEqual(doc['day'], DAY)
            self.assertEqual(doc['asof'], ASOF)

    def test_atomic_replace_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            write_pattern_pool(DAY, ASOF, 4, ('huigui',), [], {}, {}, out_dir=d)
            self.assertEqual([f.name for f in d.iterdir()],
                             [f'{DAY}_pattern_pool.json'], '残留 tmp 文件（半截 JSON 风险）')

    def test_both_entries_share_writer(self):
        """源码契约：两个建池入口都必须走同一写入口（schema 不得漂移）。

        ⚠️ 2026-09-15：独立脚本 `scripts/build_pattern_pool.py` 已归档到
        `scripts/_legacy/`（2026-09-14 裁定把建池并入 `plan_daily.py`，避免重复付
        ~900s 全市场遍历 ⇒ 一次载入出两份产物）。本用例同步改路径，并**显式断言
        文件存在** —— 否则将来再次移动时用例会静默跳过，契约失效而不自知。
        """
        entries = ('scripts/_legacy/build_pattern_pool.py', 'scripts/plan_daily.py')
        for rel in entries:
            p = ROOT / rel
            self.assertTrue(p.exists(), f'{rel} 不存在 ⇒ 契约用例失去对象，请同步本用例')
            src = p.read_text(encoding='utf-8')
            self.assertIn('write_pattern_pool', src,
                          f'{rel} 未使用共享写入口 ⇒ schema 会漂移')

    def test_plan_daily_artifact_guards_asof(self):
        """`build_pattern_artifact` 必须自己拦 asof>=day（防前视，不依赖调用方）。"""
        sys.path.insert(0, str(ROOT / 'scripts'))
        import importlib
        pd_mod = importlib.import_module('plan_daily')
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(RuntimeError):
                pd_mod.build_pattern_artifact({}, DAY, DAY, out_dir=pathlib.Path(td))
            with self.assertRaises(RuntimeError):
                pd_mod.build_pattern_artifact({}, DAY, '2026-09-02', out_dir=pathlib.Path(td))


if __name__ == '__main__':
    unittest.main(verbosity=2)
