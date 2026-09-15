from __future__ import annotations
import json
import pathlib
import re
import unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]

class DashboardNamesContractTests(unittest.TestCase):
    def test_plan_picks_carry_name(self):
        source = (ROOT / 'scripts' / 'plan_daily.py').read_text(encoding='utf-8')
        self.assertIn('"name": _names.get(sym', source)
    def test_monitor_alerts_carry_name(self):
        source = (ROOT / 'scripts' / 'monitor_intraday.py').read_text(encoding='utf-8')
        self.assertIn("def name_of(", source)
        self.assertIn("'name': name_of(sym)", source)
    def test_scan_momentum_carries_sector_name(self):
        source = (ROOT / 'scripts' / 'scan_and_confirm.py').read_text(encoding='utf-8')
        self.assertIn("'name': _SW_NAMES.get(k, '')", source)
    def test_sw_name_table_covers_board_codes(self):
        doc = json.loads((ROOT / 'data' / 'sw_l2_names.json').read_text(encoding='utf-8'))
        names = doc['names']
        momentum = json.loads((ROOT / 'outputs' / 'intraday' / 'board_momentum.json').read_text(encoding='utf-8'))['sectors']
        plan = json.loads((ROOT / 'outputs' / 'plans' / '2026-08-31_plan.json').read_text(encoding='utf-8'))
        codes = {s['l2'] for s in momentum}
        for m in plan.get('mainline', []):
            codes.add(m[0])
        for p in plan.get('picks', []):
            l2 = p.get('l2')
            if l2:
                codes.add(l2)
        codes.discard('')
        codes.discard('NA')
        self.assertEqual(sorted(c for c in codes if c not in names), [])
    def test_stock_name_table_present(self):
        """R2.8（2026-09-12）：断言改用**只含个股**的新表。

        旧表 `stock_names_full.json` 35086 条里 83% 是债/基金/指数，且深市 `000xxx` 个股名
        被**上证指数名**覆盖（`000004` 被写成「工业指数」，实为 *ST国华）→ 已降级为 raw 快照。
        新表 `stock_names_stocks.json` 为两段式 { _meta, names }。
        """
        doc = json.loads((ROOT / 'data' / 'stock_names_stocks.json').read_text(encoding='utf-8'))
        self.assertIn('_meta', doc, '新表必须是两段式（_meta + names）')
        self.assertIn('names', doc)
        names = doc['names']
        meta = doc['_meta']
        # 条数与 _meta 自洽；沪深 A 股量级（北交所未纳入，见 known_gaps）
        self.assertEqual(meta['equity_total'], len(names))
        self.assertTrue(5100 <= len(names) <= 5400, f'equity_total={len(names)} 越界')
        # 污染硬断言：名称里不得出现指数/债/基金/等权/成份类标记
        bad = {k: v for k, v in names.items()
               if any(m in v for m in ('指数', '债', '基金', 'ETF', 'LOF', '等权', '成份', '成分'))}
        self.assertEqual(bad, {}, f'新表混入非个股名：{list(bad.items())[:10]}')
        # 无空字节（旧表 1985 条带 \x00）
        self.assertTrue(all('\x00' not in v for v in names.values()))
        # 键必须全属 A 股代码段（与 scripts/build_stock_names_stocks.py::is_equity_code 对齐）
        pat = re.compile(r'^(?:60|68|000|001|002|003|300|301|92)\d+$')
        off = [k for k in names if not pat.match(k)]
        self.assertEqual(off, [], f'非 A 股段代码：{off[:10]}')
        # 个股优先硬断言：000004 必须是 *ST国华（不是「工业指数」）
        self.assertIn('ST', names.get('000004', ''), f"000004={names.get('000004')!r}")
        self.assertNotIn('指数', names.get('000004', ''))

    def test_old_name_table_is_demoted(self):
        """旧表保留为 raw 快照，且必须带 `_meta` 明确标注「不可用于名称/ST 判定」。"""
        doc = json.loads((ROOT / 'data' / 'stock_names_full.json').read_text(encoding='utf-8'))
        self.assertIn('_meta', doc, '旧表必须带 _meta 降级说明')
        self.assertEqual(doc['_meta'].get('status'), 'raw_snapshot')
        self.assertIn('stock_names_stocks.json', doc['_meta'].get('superseded_by', ''))
        self.assertIn('do_not_use_for', doc['_meta'])
    def test_vr_trader_resolves_names(self):
        source = (ROOT.parent / 'Vibe-Research' / 'orchestrator' / 'src' / 'vr_trader.ts').read_text(encoding='utf-8')
        for token in ("function stockNameOf", "industryNameOf(dataRoot", "name: typeof pick?.name", "name: typeof alert?.name"):
            self.assertIn(token, source)
    def test_portfolio_renders_name_first(self):
        """R0.10：契约「名称优先、代码回退」必须在新壳成立。

        原断言指向 Home.tsx，该页已被上游 v1.2.0 新壳替换（P0_CHANGELOG:471-473 记录待办）。
        迁移时核对发现**不是"位置变了"而是真实回归**：新壳曾渲染 `a.sym` / `s.l2`，
        把数据里本来就有的 `name` 丢掉了 → 看板不再显示股票名/板块名。
        故此处断言**恢复后的契约**（名称优先），并由 test_board_payloads_carry_name 守住数据侧。
        """
        source = (ROOT.parent / 'Vibe-Research' / 'desktop' / 'src' / 'verticals' / 'finance' / 'pages' / 'Portfolio.tsx').read_text(encoding='utf-8')
        self.assertIn("a.name || a.sym", source)     # 盘中触发提醒：名称优先
        self.assertIn("s.name || s.l2", source)      # 板块轮动：名称优先

    def test_board_payloads_carry_name(self):
        """数据侧必须提供 name —— 否则 UI 的「名称优先」会静默空回退成代码，看板看起来"正常"但实际已退化。"""
        mom = json.loads((ROOT / 'outputs' / 'intraday' / 'board_momentum.json').read_text(encoding='utf-8'))
        sectors = mom['sectors']
        self.assertTrue(sectors, 'board_momentum 无 sectors')
        for s in sectors:
            self.assertIn('name', s, f'板块缺 name: {s}')
        alerts_files = sorted((ROOT / 'outputs' / 'intraday').glob('alerts_*.json'))
        self.assertTrue(alerts_files, '缺少 alerts_*.json')
        rows = json.loads(alerts_files[-1].read_text(encoding='utf-8'))
        rows = rows.get('alerts') if isinstance(rows, dict) else rows
        self.assertTrue(rows, f'{alerts_files[-1].name} 无 alerts')
        for a in rows:
            self.assertIn('name', a, f'提醒缺 name: {a}')
if __name__ == '__main__':
    unittest.main(verbosity=2)
