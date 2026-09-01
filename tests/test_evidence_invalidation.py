"""P0.5 证据失效表契约测试（蓝图 P0.5）.

覆盖:
  - evidence_invalidation.json schema 完整性
  - 每条 artifact_path 命中文件存在且头部含作废横幅（grep 型, 防横幅被删）
  - evidence_guard 路径硬拒: 命中抛错/未命中放行/绝对路径归一化
"""
from __future__ import annotations
import pathlib, sys, unittest
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
import evidence_guard


class EvidenceInvalidationSchemaTests(unittest.TestCase):
    def setUp(self):
        self.entries = evidence_guard.load_entries()

    def test_entries_have_required_fields(self):
        self.assertTrue(len(self.entries) >= 9, '首批至少 9 条（ev-001~ev-009）')
        for e in self.entries:
            for field in ('id', 'artifact_paths', 'stale_claim', 'invalid_reason', 'invalidated_at'):
                self.assertIn(field, e, f"{e.get('id')} 缺 {field}")

    def test_key_stale_numbers_are_registered(self):
        joined = json.dumps(self.entries, ensure_ascii=False) if (json := __import__('json')) else ''
        for token in ('+560', '+713', '+2097', '1.51'):
            self.assertIn(token, joined, f'陈旧数字 {token} 未登记')

    def test_every_artifact_path_exists_and_bannered(self):
        for e in self.entries:
            for ap in (e.get('artifact_paths') or []):
                p = ROOT / ap
                self.assertTrue(p.exists(), f"{e['id']} 路径不存在: {ap}")
                head = '\n'.join(p.read_text(encoding='utf-8').splitlines()[:6])
                self.assertIn(f'evidence_invalidation.json#{e["id"]}', head,
                              f"{ap} 头部缺 {e['id']} 作废横幅")

    def test_cohort_entry_documents_engine_bug_family(self):
        cohort = [e for e in self.entries if e.get('kind') == 'cohort']
        self.assertEqual(len(cohort), 1)
        joined = cohort[0]['stale_claim']
        for token in ('R3 打板基线', '动量池', '回踩池', 'P0-C', 'P0-D', 'P3 -3% 止损'):
            self.assertIn(token, joined)


class EvidenceGuardTests(unittest.TestCase):
    def test_hit_stale_portfolio_findings(self):
        hits = evidence_guard.check_paths(['outputs/portfolio_findings.md'])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['entry_id'], 'ev-001')

    def test_guard_raises_on_hit(self):
        with self.assertRaises(evidence_guard.EvidenceInvalidatedError):
            evidence_guard.guard_paths(['docs/loops/W2_DABAN5_BREAKTHROUGH.md', 'outputs/regime_switch_2025.md'])

    def test_guard_passes_clean_paths(self):
        evidence_guard.guard_paths(['docs/loops/EVIDENCE_INVALIDATION.md',
                                    'docs/loops/COMBO_FIXED_FINAL.md',
                                    'docs/reviews/r6p_round3_review.md'])
        self.assertEqual(evidence_guard.check_paths(['docs/loops/EVIDENCE_INVALIDATION.md']), [])

    def test_absolute_path_normalized_to_repo_relative(self):
        abs_path = str(ROOT / 'outputs' / 'portfolio_findings.md')
        hits = evidence_guard.check_paths([abs_path])
        self.assertEqual(len(hits), 1)

    def test_cohort_reference_paths_not_hard_rejected(self):
        # reference_paths 不参与硬拒（cohort 条目 guard_note 声明人工核验）
        evidence_guard.guard_paths(['docs/loops/P3_recheck_4y.md'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
