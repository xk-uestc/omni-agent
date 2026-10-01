from backend.cross_source import DocumentRecord
from backend.knowledge_store import _page_scope_coverage


def record(chunk, doc, page, text, title=''):
    return DocumentRecord(chunk, title, text, '/original', {'document_id': doc, 'page_no': page})


def test_page_coverage_recovers_scattered_entities_and_years_without_title_matches():
    records = [record('a', 'target', 1, 'FY 2024 support for Islandia'),
               record('b', 'target', 1, 'TOTAL ASSISTANCE: $12.50 m'),
               record('c', 'other', 1, 'Support and assistance in FY 2019', 'Islandia 2024'),
               record('d', 'target', 2, 'Unrelated notes')]
    coverage = _page_scope_coverage(records, 'What is total assistance for Islandia in FY 2024?')
    assert coverage[('target', 1)] == 1
    assert 0 < coverage[('other', 1)] < 1
    assert coverage[('target', 2)] == 0


def test_page_numbers_are_document_local_and_absent_query_terms_are_not_invented():
    records = [record('a', 'one', 1, 'Ada represents Example'),
               record('b', 'two', 1, 'Bert is lead attorney')]
    coverage = _page_scope_coverage(records, 'Who is lead attorney Ada?')
    assert all(value < 1 for value in coverage.values())
    assert coverage[('one', 1)] != coverage[('two', 1)]
    assert _page_scope_coverage(records, 'the is what') == {}
