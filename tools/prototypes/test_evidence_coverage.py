import unittest
from dataclasses import dataclass, field
from evidence_coverage import select_coverage_hits


@dataclass
class Hit:
    document_id: str
    score: float
    metadata: dict = field(default_factory=dict)


def run(query, rows, top_k=3):
    hits = [Hit(chunk, score, {'document_id': source, 'source_sha256': 'v1'})
            for chunk, source, score, body in rows]
    return select_coverage_hits(query, hits, {r[0]: r[3] for r in rows}, lambda h: (h.score, 0), top_k)


class CoverageTests(unittest.TestCase):
    def test_third_same_source_facet_beats_redundant_distractor(self):
        result = run('Agency assistance 2026 including donated commodities', [
            ('a', 'report', 10, 'Agency assistance 2026.'),
            ('b', 'report', 9, 'Agency assistance paid by the government.'),
            ('c', 'report', 8, 'Including donated commodities.'),
            ('d', 'other', 8.5, 'Agency assistance helps the economy.'),
        ])
        self.assertIn('c', [h.document_id for h in result.hits])
        self.assertEqual(result.audit['semantic_sufficiency'], 'not_evaluated')

    def test_three_complementary_same_source_items_not_capped(self):
        result = run('period budget formula diapers', [
            ('a', 'report', 10, 'Period of the service.'),
            ('b', 'report', 9, 'Budget of the project.'),
            ('c', 'report', 8, 'Formula and diapers allocations.'),
            ('d', 'other', 8.5, 'Period repeated elsewhere.'),
        ])
        self.assertEqual({h.document_id for h in result.hits}, {'a', 'b', 'c'})
        self.assertEqual(result.audit['source_counts']['report'], 3)

    def test_actual_body_duplicates_backfilled(self):
        result = run('budget period revenue', [
            ('a', 'report', 10, 'Budget period records.'),
            ('b', 'report', 9, '  BUDGET   period records. '),
            ('c', 'report', 8, 'Revenue records.'),
        ], 2)
        self.assertEqual([h.document_id for h in result.hits], ['a', 'c'])
        self.assertEqual(result.audit['skipped'][0]['duplicate_of'], 'a')

    def test_different_time_values_not_deduplicated(self):
        result = run('Compare revenue 2023 2024', [
            ('a', 'report', 10, 'Revenue for 2023 was 100 units.'),
            ('b', 'report', 9, 'Revenue for 2024 was 100 units.'),
        ], 2)
        self.assertEqual(len(result.hits), 2)
        self.assertTrue({'2023', '2024'} <= set(result.audit['covered_lexical_facets']))

    def test_near_duplicate_negation_is_not_collapsed(self):
        shared = 'Revenue of the department for the operating period is 100 units. ' * 8
        result = run('revenue excluding fees', [
            ('a', 'report', 10, shared + 'Including fees.'),
            ('b', 'report', 9, shared + 'Excluding fees.'),
        ], 2)
        self.assertEqual(len(result.hits), 2)
        self.assertEqual(result.audit['skipped'], [])

    def test_single_fact_keeps_strong_relevance(self):
        result = run('Who is the lead attorney?', [
            ('a', 'docket', 10, 'Lead attorney: Example Person.'),
            ('b', 'manual', 1, 'Attorney regulations.'),
        ], 1)
        self.assertEqual(result.hits[0].document_id, 'a')

    def test_low_score_new_facet_cannot_displace_strong_hit(self):
        result = run('cost timing exclusions', [
            ('a', 'report', 10, 'Cost is explained here.'),
            ('b', 'report', 9, 'Cost accounting detail.'),
            ('c', 'other', .01, 'Timing exclusions.'),
        ], 2)
        self.assertEqual([h.document_id for h in result.hits], ['a', 'b'])

    def test_budget_shortfall_is_audited(self):
        result = run('cost timing exclusions', [
            ('a', 'report', 10, 'Cost.'),
            ('b', 'report', 9, 'Timing.'),
            ('c', 'report', 8, 'Exclusions.'),
        ], 2)
        self.assertTrue(result.audit['selection_budget_exhausted'])
        self.assertEqual(result.audit['available_but_unselected_facets'], ['exclusions'])

    def test_missing_body_not_replaced_by_snippet(self):
        hit = Hit('a', 10)
        result = select_coverage_hits('cost', [hit], {}, lambda h: h.score)
        self.assertEqual(result.hits, [])
        self.assertEqual(result.audit['skipped'][0]['reason'], 'body_missing')

    def test_bound_and_chinese_facets(self):
        result = run('2025年预算包含捐赠', [('a', 'r', 1, '2025年预算包含捐赠。')], 1)
        self.assertIn('包含', result.audit['covered_lexical_facets'])
        self.assertIn('2025', result.audit['covered_lexical_facets'])
        with self.assertRaises(ValueError):
            run('cost', [], 9)


if __name__ == '__main__':
    unittest.main()
