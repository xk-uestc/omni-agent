import unittest
from dataclasses import dataclass, field
from backend.evidence_coverage import select_coverage_hits


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
            ('b', 'report', 9, '  Budget   period records. '),
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
            run('cost', [], 21)

    def test_catalogue_top_twenty_supported(self):
        result = run('cost', [(str(i), str(i), 20-i, 'Cost ' + str(i)) for i in range(20)], 20)
        self.assertEqual(len(result.hits), 20)

    def test_zero_score_unmatched_candidate_is_not_relevant(self):
        result = run('revenue', [('a', 'r', 0, 'Unrelated policy.')], 1)
        self.assertEqual(result.hits, [])
        self.assertEqual(result.audit['skipped'][0]['reason'], 'zero_score_no_query_match')

    def test_source_version_change_is_not_deduplicated(self):
        hits = [Hit('a', 10, {'document_id': 'report', 'source_sha256': 'v1'}),
                Hit('b', 9, {'document_id': 'report', 'source_sha256': 'v2'})]
        result = select_coverage_hits('cost', hits, {'a': 'Cost.', 'b': 'Cost.'}, lambda h: h.score, 2)
        self.assertEqual(len(result.hits), 2)

    def test_equal_text_on_different_pages_retains_scope(self):
        hits = [Hit('a', 10, {'document_id': 'report', 'source_sha256': 'v1', 'page_no': 1, 'source_locator': 'page:1:block:2'}),
                Hit('b', 9, {'document_id': 'report', 'source_sha256': 'v1', 'page_no': 2, 'source_locator': 'page:2:block:2'})]
        result = select_coverage_hits('cost', hits, {'a': 'Cost 100.', 'b': 'Cost 100.'}, lambda h: h.score, 2)
        self.assertEqual(len(result.hits), 2)

    def test_identical_split_native_anchor_deduplicates(self):
        hits = [Hit('a', 10, {'document_id': 'report', 'source_sha256': 'v1', 'page_no': 1, 'source_locator': 'page:1:block:2:part:1'}),
                Hit('b', 9, {'document_id': 'report', 'source_sha256': 'v1', 'page_no': 1, 'source_locator': 'page:1:block:2:part:2'})]
        result = select_coverage_hits('cost', hits, {'a': 'Cost 100.', 'b': 'Cost 100.'}, lambda h: h.score, 2)
        self.assertEqual(len(result.hits), 1)

    def test_semantic_symbols_and_case_are_never_deduplicated(self):
        pairs = [
            ('Net profit in this operating period is -100 units.', 'Net profit in this operating period is +100 units.'),
            ('Revenue equals cost - rebate for the reporting period.', 'Revenue equals cost + rebate for the reporting period.'),
            ('Yield in the operating period is 20%.', 'Yield in the operating period is 20.'),
            ('Cost under this contract must be <100.', 'Cost under this contract must be >100.'),
            ('Cost under this contract must be <=100.', 'Cost under this contract must be <100.'),
            ('Contract ABC-12 operating revenue is 100.', 'Contract ABC12 operating revenue is 100.'),
            ('Revenue applies to entity ABC-12.', 'Revenue applies to entity ABC_12.'),
            ('The specified interval is [1, 10].', 'The specified interval is (1, 10).'),
            ('The eligible quantity is 1,000 units.', 'The eligible quantity is 1.000 units.'),
            ('Distance in the period is 1 m.', 'Distance in the period is 1 M.'),
            ('Entity AB revenue is 100.', 'Entity ab revenue is 100.'),
            ('The eligible entity is A/B.', 'The eligible entity is AB.'),
        ]
        for a, b in pairs:
            with self.subTest(a=a, b=b):
                hits = [Hit('a', 10, {'document_id': 'report', 'source_sha256': 'v1', 'page_no': 1, 'source_locator': 'page:1:block:2:part:1'}),
                        Hit('b', 9, {'document_id': 'report', 'source_sha256': 'v1', 'page_no': 1, 'source_locator': 'page:1:block:2:part:2'})]
                result = select_coverage_hits('revenue cost entity period', hits, {'a': a, 'b': b}, lambda h: h.score, 2)
                self.assertEqual(len(result.hits), 2)
                self.assertEqual(result.audit['skipped'], [])

    def test_traditional_query_simplified_body_coverage_uses_literal_hash(self):
        import hashlib
        body = '  销售额包含退货。\n'
        result = run('銷售額包含退貨', [('a', 'report', 10, body)], 1)
        self.assertTrue({'销售', '售额', '退货'} <= set(result.audit['covered_lexical_facets']))
        self.assertEqual(result.audit['uncovered_lexical_facets'], [])
        self.assertEqual(result.audit['steps'][0]['body_sha256'], hashlib.sha256(body.encode('utf-8')).hexdigest())
        self.assertNotEqual(result.audit['steps'][0]['body_sha256'], hashlib.sha256(body.strip().encode('utf-8')).hexdigest())

    def test_simplified_query_traditional_body_is_not_rewritten_or_deduplicated(self):
        import hashlib
        raw = '銷售額包含退貨。'
        result = run('销售额包含退货', [('a', 'report', 10, raw), ('b', 'report', 9, '销售额包含退货。')], 2)
        self.assertEqual(len(result.hits), 2)
        self.assertEqual(result.audit['uncovered_lexical_facets'], [])
        step = next(step for step in result.audit['steps'] if step['chunk_id'] == 'a')
        self.assertEqual(step['body_sha256'], hashlib.sha256(raw.encode('utf-8')).hexdigest())


def test_store_selects_third_same_source_and_verifies_original(tmp_path, monkeypatch):
    from backend.knowledge_store import KnowledgeStore
    from backend.cross_source import DocumentRecord, DocumentHit, JsonDocumentRetriever
    store = KnowledgeStore(tmp_path)
    store.ingest(b'Period budget formula diapers.', document_id='report', title='Report', modality='txt', filename='report.txt')
    store.ingest(b'Period distractor.', document_id='other', title='Other', modality='txt', filename='other.txt')
    rows = [('a', 'report', 10, 'Period.'), ('b', 'report', 9, 'Budget.'),
            ('c', 'report', 8, 'Formula diapers.'), ('d', 'other', 8.5, 'Period.')]
    records = [DocumentRecord(chunk, source, body, 'document://' + source,
               {'document_id': source, 'source_sha256': store.document(source)['sha256']})
               for chunk, source, score, body in rows]
    hits = [DocumentHit(record.document_id, record.title, score, (), record.content, record.source_uri,
            {**record.metadata, 'bm25_raw': score}) for record, (_, _, score, _) in zip(records, rows)]
    monkeypatch.setattr(store, 'records', lambda: records)
    monkeypatch.setattr(JsonDocumentRetriever, 'search', lambda *args, **kwargs: hits)
    selected = store.search('period budget formula diapers', top_k=3)
    assert {h.document_id for h in selected} == {'a', 'b', 'c'}
    assert all(h.metadata['document_id'] == 'report' for h in selected)
    assert selected[2].metadata['evidence_selection']['semantic_sufficiency'] == 'not_evaluated'
    assert 'source_counts' not in selected[2].metadata['evidence_selection']
    original, _ = store.original('report')
    original.write_bytes(b'changed original')
    with __import__('pytest').raises(ValueError):
        store.search('period budget formula diapers', top_k=3)


def test_store_exact_identifier_filter_still_applies(tmp_path):
    from backend.knowledge_store import KnowledgeStore
    store = KnowledgeStore(tmp_path)
    store.ingest(b'Contract ZX-12 revenue 2025.', document_id='wanted', title='Contract', modality='txt', filename='a.txt')
    store.ingest(b'Contract ZX-13 revenue 2025.', document_id='wrong', title='Contract', modality='txt', filename='b.txt')
    hits = store.search('ZX-12 revenue 2025', top_k=20)
    assert hits and {h.metadata['document_id'] for h in hits} == {'wanted'}
    assert hits[0].metadata['identifier_anchors'] == ['ZX-12']
    assert hits[0].metadata['anchor_match_count'] == 1


if __name__ == '__main__':
    unittest.main()
