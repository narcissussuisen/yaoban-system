from __future__ import annotations
import json
import pathlib
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
        names = json.loads((ROOT / 'data' / 'stock_names_full.json').read_text(encoding='utf-8'))
        self.assertIsInstance(names, dict)
        self.assertGreater(len(names), 4000)
    def test_vr_trader_resolves_names(self):
        source = (ROOT.parent / 'Vibe-Research' / 'orchestrator' / 'src' / 'vr_trader.ts').read_text(encoding='utf-8')
        for token in ("function stockNameOf", "industryNameOf(dataRoot", "name: typeof pick?.name", "name: typeof alert?.name"):
            self.assertIn(token, source)
    def test_home_renders_name_first(self):
        source = (ROOT.parent / 'Vibe-Research' / 'desktop' / 'src' / 'verticals' / 'finance' / 'pages' / 'Home.tsx').read_text(encoding='utf-8')
        self.assertIn("(a.name || a.sym)", source)
        self.assertIn("{s.name || s.l2}", source)
if __name__ == '__main__':
    unittest.main(verbosity=2)
