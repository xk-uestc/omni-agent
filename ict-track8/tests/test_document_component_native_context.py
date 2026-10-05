"""The final composition review needs original table scope, not inferred years."""
from copy import deepcopy
import fitz
import pytest

from backend.document_parts_answer import _review_component
from backend.knowledge_store import KnowledgeStore, SourceIntegrityError
from backend.native_text_tables import extract_native_text_tables


def prepare(tmp_path, *, long_page=False):
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 50), 'The following attendance relates to financial year 2032.', fontsize=10)
        page.insert_text((40, 90), 'Name', fontsize=10)
        page.insert_text((270, 90), 'Attendance', fontsize=10)
        for i, (name, count) in enumerate([('Casey Reed', '3/4'), ('Robin Hill', '4/4'), ('Avery Lane', '2/4')]):
            page.insert_text((40, 120 + i * 25), name, fontsize=10)
            page.insert_text((285, 120 + i * 25), count, fontsize=10)
        if long_page:
            for i in range(70):
                page.insert_text((40, 240 + i * 7), 'Unrelated conditions remain binding and must not be removed.' * 2, fontsize=4)
        raw = pdf.tobytes()
    store = KnowledgeStore(tmp_path / 'knowledge')
    store.ingest(raw, document_id='attendance', title='Attendance', modality='pdf', filename='attendance.pdf')
    doc = next(d for d in store.list_documents() if d['document_id'] == 'attendance')
    manifest = extract_native_text_tables(raw, page_no=1, expected_source_sha256=doc['sha256'])
    fact = next(f for f in manifest['facts'] if f['row_header'] == 'Casey Reed')
    result = {'answer_mode': 'native_table_model_reviewed', 'answer': '75.00%',
              'citations': [{'metadata': {'document_id': 'attendance', 'source_sha256': doc['sha256'],
                                         'page_no': 1, 'fact': fact}}]}
    return store, result


def test_original_external_year_reaches_review_without_relabeling_cell(tmp_path):
    store, child = prepare(tmp_path)
    result = _review_component(1, 'What percentage did Casey Reed attend?', child, store=store)
    context = next(iter(result['native_page_contexts'].values()))
    assert 'financial year 2032' in context['complete_native_page_text']
    assert 'Robin Hill' in context['complete_native_page_text']
    fact = result['evidence'][0]['native_fact']
    assert fact['period'] is None  # Never infer a cell year from external text.
    assert result['evidence'][0]['native_page_context_id'] in result['native_page_contexts']
    assert context['text_sha256'] and context['source_sha256']


def test_repeated_same_page_facts_keep_one_original_context(tmp_path):
    store, child = prepare(tmp_path)
    child['citations'].append(deepcopy(child['citations'][0]))
    result = _review_component(1, 'Question', child, store=store)
    assert len(result['native_page_contexts']) == 1


def test_forged_native_cell_cannot_borrow_real_page_context(tmp_path):
    store, child = prepare(tmp_path)
    child['citations'][0]['metadata']['fact']['raw_value'] = '4/4'
    with pytest.raises(ValueError, match='component_native_fact_context_mismatch'):
        _review_component(1, 'Question', child, store=store)


def test_changed_original_cannot_reach_final_review(tmp_path):
    store, child = prepare(tmp_path)
    doc = next(d for d in store.list_documents() if d['document_id'] == 'attendance')
    store.verify_source('attendance', expected_sha256=doc['sha256']).write_bytes(b'changed source')
    with pytest.raises(SourceIntegrityError):
        _review_component(1, 'Question', child, store=store)


def test_over_budget_original_page_never_replaced_by_short_cell(tmp_path):
    store, child = prepare(tmp_path, long_page=True)
    with pytest.raises(ValueError, match='component_native_context_budget'):
        _review_component(1, 'Question', child, store=store)
