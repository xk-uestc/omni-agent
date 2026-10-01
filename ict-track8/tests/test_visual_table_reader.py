"""Original-question binding and server-owned grid values, without API calls."""
import hashlib
from copy import deepcopy
import fitz
import pytest

from backend.visual_evidence import render_pdf_evidence
from backend.visual_tables import extract_pdf_tables
from backend.visual_table_reader import VisualTableReader
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import GenerationError


def grid_pdf():
    with fitz.open() as document:
        page = document.new_page(width=430, height=280)
        for x in (50, 150, 250, 350):
            page.draw_line((x, 50), (x, 155))
        for y in (50, 85, 120, 155):
            page.draw_line((50, y), (350, y))
        for r, row in enumerate([['Region', '2025 Actual', '2026 Forecast'], ['West', '3578', '4800'], ['East', '9900', '7000']]):
            for c, value in enumerate(row):
                page.insert_text((55 + c * 100, 72 + r * 35), value, fontsize=9)
        return document.tobytes()


class Client:
    model = 'gpt-6-luna'
    reasoning = 'medium'
    audit = {'status': 'completed', 'http_status': 200}

    def __init__(self, mutation=None):
        self.calls, self.mutation = [], mutation

    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append(deepcopy(context))
        table = context['table_registry_without_values'][0]
        result = {'abstain': False, 'table_id': table['table_id'], 'row_header': 'West', 'column_header_path': ['2025 Actual']}
        if self.mutation:
            self.mutation(result)
        return result


def setup():
    raw = grid_pdf()
    digest = hashlib.sha256(raw).hexdigest()
    return render_pdf_evidence(raw, page_no=1), extract_pdf_tables(raw, page_no=1, expected_source_sha256=digest)


def test_real_grid_and_image_chain_uses_server_literal_value():
    asset, tables = setup()
    client = Client()
    result = VisualTableReader(client).answer('What is West 2025 Actual?', asset, tables)
    assert result['status'] == 'ok' and result['fact']['raw_value'] == '3578'
    assert result['calculator_input_eligible'] is False
    assert '3578' not in str(client.calls)


@pytest.mark.parametrize('question', ['West 2025 Actual and East 2025 Actual', 'West 2025 Actual 2026', 'West total 2025 Actual', 'West amount', '2025 Actual', 'West 2025 Actual above 5000', 'West 2025 Actual increased by 10', 'West 2025 Actual if approved'])
def test_uncertain_or_computational_scope_does_not_call_model(question):
    asset, tables = setup()
    client = Client()
    assert VisualTableReader(client).answer(question, asset, tables)['status'] == 'incomplete'
    assert not client.calls


@pytest.mark.parametrize('field,value', [('row_header', 'East'), ('column_header_path', ['2026 Forecast']), ('table_id', 'invented')])
def test_model_cannot_select_real_neighbor_with_wrong_original_scope(field, value):
    asset, tables = setup()
    result = VisualTableReader(Client(lambda x: x.update({field: value}))).answer('West 2025 Actual', asset, tables)
    assert result['status'] == 'incomplete' and result['answer'] is None


def test_malformed_abstention_is_not_accepted_as_valid_model_output():
    asset, tables = setup()
    with pytest.raises(GenerationError):
        VisualTableReader(Client(lambda x: x.update(abstain=True, table_id=None, row_header=None, column_header_path=None))).answer(
            'West 2025 Actual', asset, tables)


def test_registered_store_rechecks_revision_after_model(tmp_path):
    raw = grid_pdf()
    client = Client()
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    record = store.ingest(raw, document_id='grid', title='Grid', modality='pdf', filename='grid.pdf')
    client.mutation = lambda x: store.ingest(grid_pdf() + b'\n%new revision', document_id='grid', title='Grid', modality='pdf', filename='grid.pdf')
    with pytest.raises(SourceRevisionError):
        store.visual_table_answer('grid', question='West 2025 Actual', page_no=1, expected_source_sha256=record['sha256'])


def test_table_query_http_uses_same_source_pin(tmp_path, monkeypatch):
    from backend import app as module
    from fastapi.testclient import TestClient
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(Client()))
    record = store.ingest(grid_pdf(), document_id='grid', title='Grid', modality='pdf', filename='grid.pdf')
    monkeypatch.setattr(module, 'knowledge_store', store)
    client = TestClient(module.app)
    body = {'question': 'West 2025 Actual', 'page_no': 1, 'expected_source_sha256': record['sha256']}
    response = client.post('/api/v1/knowledge/documents/grid/visual-table-query', json=body)
    assert response.status_code == 200 and response.json()['fact']['raw_value'] == '3578'
    body['expected_source_sha256'] = '0' * 64
    assert client.post('/api/v1/knowledge/documents/grid/visual-table-query', json=body).status_code == 409
