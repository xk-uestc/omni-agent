"""Full-page candidate disambiguation, source pinning and shared work budget."""
import hashlib
import threading

import fitz
import pytest

from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.responses_client import GenerationError
from backend.visual_routing import route_visual_table_question
from backend import visual_work_budget
from tests.test_visual_table_reader import grid_pdf, Client


def store_with_grid(tmp_path, client=None):
    client = client or Client()
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    record = store.ingest(grid_pdf(), document_id='grid', title='Regional grid', modality='pdf', filename='grid.pdf')
    return store, client, record


def test_main_answer_routes_once_and_preserves_citation_contract(tmp_path):
    store, client, record = store_with_grid(tmp_path)
    result = store.answer('What is West 2025 Actual?')
    assert result['status'] == 'ok' and result['answer_mode'] == 'visual_grid_routed'
    assert result['fact']['raw_value'] == '3578' and len(client.calls) == 1
    citation = result['citations'][0]
    assert citation['metadata']['document_id'] == 'grid'
    assert citation['metadata']['source_sha256'] == record['sha256']
    assert citation['metadata']['page_no'] == 1 and citation['metadata']['bbox_display_pt']
    assert result['trace'][0]['scope'] == 'retrieved_pdf_documents_all_pages_not_global_corpus'
    assert result['trace'][0]['model_requests_attempted'] == 1


def test_explicit_source_does_not_require_text_hit(tmp_path, monkeypatch):
    store, client, _ = store_with_grid(tmp_path)
    monkeypatch.setattr(store, 'search', lambda *a, **kw: [])
    result = store.answer('West 2025 Actual', document_id='grid')
    assert result['status'] == 'ok' and len(client.calls) == 1
    assert result['trace'][0]['scope'] == 'explicit_document_all_pages'


def test_duplicate_documents_do_not_choose_highest_rank(tmp_path):
    store, client, _ = store_with_grid(tmp_path)
    store.ingest(grid_pdf(), document_id='grid2', title='Second grid', modality='pdf', filename='grid2.pdf')
    result = store.answer('West 2025 Actual')
    assert result['status'] == 'incomplete' and result['answer'] is None
    assert len(result['source_options']) == 2 and not client.calls
    assert store.answer('West 2025 Actual', document_id='grid2')['document_id'] == 'grid2'


def multipage_pdf(duplicate=False):
    with fitz.open() as document:
        with fitz.open(stream=grid_pdf(), filetype='pdf') as grid:
            if duplicate:
                document.insert_pdf(grid)
            else:
                document.new_page(width=430, height=280).insert_text((50, 50), 'Intro page only')
            document.insert_pdf(grid)
        return document.tobytes()


@pytest.mark.parametrize('duplicate', [True, False])
def test_every_page_of_candidate_doc_is_inspected(tmp_path, duplicate):
    client = Client()
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    store.ingest(multipage_pdf(duplicate), document_id='pages', title='Grid pages', modality='pdf', filename='pages.pdf')
    result = store.answer('West 2025 Actual')
    if duplicate:
        assert result['status'] == 'incomplete' and not client.calls
        assert [item['page_no'] for item in result['source_options']] == [1, 2]
    else:
        assert result['status'] == 'ok' and result['citations'][0]['metadata']['page_no'] == 2
        assert len(client.calls) == 1


def test_unbound_filter_does_not_fall_back_to_text_generation(tmp_path):
    store, client, _ = store_with_grid(tmp_path)
    result = store.answer('West 2025 Actual if approved')
    assert result['status'] == 'incomplete' and not client.calls
    assert result['trace'][0]['scope_rejections'][0]['reason'] == 'single_cell_question_has_unbound_terms'


def test_nonwinning_candidate_revision_invalidates_uniqueness(tmp_path):
    store, client, _ = store_with_grid(tmp_path)
    with fitz.open() as document:
        # Labels match retrieval but there is no grid; its revision still
        # matters to the candidate-scope uniqueness decision.
        document.new_page().insert_text((40, 40), 'West 2025 Actual reference')
        raw = document.tobytes()
    store.ingest(raw, document_id='other', title='West 2025 Actual source', modality='pdf', filename='other.pdf')
    client.mutation = lambda _: store.ingest(raw + b'\n%changed', document_id='other', title='Other', modality='pdf', filename='other.pdf')
    with pytest.raises(SourceRevisionError):
        store.answer('West 2025 Actual')


def test_visual_model_failure_does_not_start_text_generation(tmp_path):
    class FailedClient(Client):
        def generate(self, *args, **kwargs):
            raise GenerationError('Unavailable')
    store, _, _ = store_with_grid(tmp_path, FailedClient())
    result = store.answer('West 2025 Actual')
    assert result['status'] == 'incomplete' and result['answer'] is None
    assert result['clarification_code'] == 'visual_model_unavailable'


def test_shared_budget_applies_to_main_and_direct_visual_calls(tmp_path, monkeypatch):
    store, _, record = store_with_grid(tmp_path)
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(visual_work_budget, 'VISUAL_WORK_SLOTS', slots)
    assert slots.acquire(blocking=False)
    for operation in [lambda: store.answer('West 2025 Actual'),
                      lambda: store.visual_asset('grid', page_no=1, expected_source_sha256=record['sha256']),
                      lambda: store.visual_table_answer('grid', question='West 2025 Actual', page_no=1, expected_source_sha256=record['sha256'])]:
        with pytest.raises(visual_work_budget.VisualWorkBusy):
            operation()
    slots.release()
    assert store.answer('West 2025 Actual')['status'] == 'ok'
    assert slots.acquire(blocking=False)
    slots.release()


def test_page_budget_is_explicit_and_no_model_called(tmp_path):
    store, client, _ = store_with_grid(tmp_path)
    with fitz.open() as document:
        for _ in range(13):
            document.new_page().insert_text((40, 40), 'West 2025 Actual')
        raw = document.tobytes()
    store.ingest(raw, document_id='large', title='Large PDF', modality='pdf', filename='large.pdf')
    result, trace = route_visual_table_question(store, 'West 2025 Actual', [], document_id='large')
    assert result['status'] == 'incomplete' and trace['status'] == 'candidate_page_budget_exceeded' and not client.calls
    main = store.answer('West 2025 Actual', document_id='large')
    assert main['status'] == 'incomplete' and main['answer'] is None and not client.calls


def test_explicit_page_resolves_duplicates_in_a_source(tmp_path):
    store, client, _ = store_with_grid(tmp_path)
    store.ingest(multipage_pdf(True), document_id='pages', title='Grid pages', modality='pdf', filename='pages.pdf')
    result = store.answer('West 2025 Actual', document_id='pages', page_no=2)
    assert result['status'] == 'ok' and result['citations'][0]['metadata']['page_no'] == 2
    assert result['trace'][0]['scope'] == 'explicit_document_page' and len(client.calls) == 1
    with pytest.raises(ValueError):
        store.answer('West 2025 Actual', page_no=2)
    with pytest.raises(ValueError):
        store.answer('West 2025 Actual', document_id='grid', page_no=2)


def test_explicit_source_limits_normal_text_retrieval(tmp_path):
    store = KnowledgeStore(tmp_path)
    for key, text in [('one', 'Hardware warranty is two years.'), ('two', 'Hardware warranty is ten years.')]:
        store.ingest(text.encode(), document_id=key, title='Hardware', modality='txt', filename=key + '.txt')
    result = store.answer('Hardware warranty', document_id='one')
    assert result['citations'] and all(c['metadata']['document_id'] == 'one' for c in result['citations'])
    assert 'ten years' not in result['answer']


def test_main_http_scope_and_busy_errors(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app as module
    store, _, _ = store_with_grid(tmp_path)
    monkeypatch.setattr(module, 'knowledge_store', store)
    client = TestClient(module.app)
    payload = {'question': 'West 2025 Actual', 'document_id': 'grid'}
    assert client.post('/api/v1/knowledge/query', json=payload).json()['status'] == 'ok'
    assert client.post('/api/v1/knowledge/query', json={**payload, 'document_id': 'missing'}).status_code == 404
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(visual_work_budget, 'VISUAL_WORK_SLOTS', slots)
    slots.acquire()
    try:
        response = client.post('/api/v1/knowledge/query', json=payload)
        assert response.status_code == 429 and response.json()['detail']['code'] == 'visual_render_busy'
    finally:
        slots.release()
