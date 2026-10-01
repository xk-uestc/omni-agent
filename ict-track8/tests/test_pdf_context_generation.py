"""Original-byte PDF context integration and source snapshot regressions."""
from copy import deepcopy
import json

import fitz
import pytest

from backend.cross_source import DocumentHit
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore, SourceRevisionError


class Client:
    model = 'gpt-6-luna'

    def __init__(self):
        self.calls, self.audit, self.mutation = [], {}, None

    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append(deepcopy(context))
        if self.mutation:
            self.mutation()
        self.audit = {'status': 'completed', 'http_status': 200}
        return {'abstain': True, 'claims': []}


def prepared(tmp_path):
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((45, 100), 'Example report is final.\nAlice Doe\nLEAD ENGINEER\nCompany unit', fontsize=10)
    raw = pdf.tobytes()
    pdf.close()
    client = Client()
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    store.ingest(raw, document_id='example', title='Engineering report', modality='pdf', filename='example.pdf')
    record = next(r for r in store.records() if 'Alice' in r.content)
    hit = DocumentHit(record.document_id, record.title, .9, ('alice', 'engineer'), record.content,
                      record.source_uri, record.metadata)
    return store, client, hit


def test_pdf_context_uses_original_block_not_untrusted_raw_page_text(tmp_path, monkeypatch):
    store, client, hit = prepared(tmp_path)
    with store.connect() as connection:
        for chunk_id, payload in connection.execute('SELECT chunk_id,payload FROM chunks').fetchall():
            chunk = json.loads(payload)
            chunk['metadata']['raw_page_text'] = 'INVENTED payroll is 999999 dollars.'
            connection.execute('UPDATE chunks SET payload=? WHERE chunk_id=?', (json.dumps(chunk), chunk_id))
    monkeypatch.setattr(store, 'search', lambda *args, **kwargs: [hit])
    result = store.answer('Alice engineer')
    evidence = result['citations'][0]['generation_evidence']
    assert evidence['mode'] == 'authoritative_pdf_native_block_context'
    assert 'LEAD ENGINEER' in evidence['text']
    assert 'INVENTED' not in evidence['text']
    assert result['citations'][0]['snippet'] == hit.snippet
    assert client.calls[0]['evidence'][0]['text'] == evidence['text']
    assert evidence['offset_start'] is None and evidence['offset_end'] is None


@pytest.mark.parametrize('mutation', ['layout', 'neighbor_text', 'delete'])
def test_pdf_context_source_snapshot_detects_mutation(tmp_path, monkeypatch, mutation):
    store, client, hit = prepared(tmp_path)
    monkeypatch.setattr(store, 'search', lambda *args, **kwargs: [hit])

    def change():
        with store.connect() as connection:
            if mutation == 'delete':
                connection.execute('DELETE FROM chunks WHERE chunk_id=?', (hit.document_id,))
                return
            row = connection.execute('SELECT payload FROM chunks WHERE chunk_id=?', (hit.document_id,)).fetchone()
            chunk = json.loads(row[0])
            if mutation == 'layout':
                chunk['metadata']['coordinate_evidence']['source_bbox_fitz_unrotated_pt'][0] += 10
            else:
                # Even metadata-only changes are part of the anchor snapshot.
                chunk['metadata']['raw_page_text'] = 'Changed neighboring text.'
            connection.execute('UPDATE chunks SET payload=? WHERE chunk_id=?', (json.dumps(chunk), hit.document_id))

    client.mutation = change
    with pytest.raises(SourceRevisionError):
        store.answer('Alice engineer')
    assert len(client.calls) == 1


def test_forecast_context_budget_failure_never_falls_back_to_isolated_anchor(tmp_path, monkeypatch):
    import backend.knowledge_store as module
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((45, 60), 'Forecast data.', fontsize=10)
    page.insert_text((45, 150), 'Revenue is 30.83 dollars.', fontsize=10)
    raw = pdf.tobytes()
    pdf.close()
    store = KnowledgeStore(tmp_path)
    store.ingest(raw, document_id='forecast', title='Forecast', modality='pdf', filename='forecast.pdf')
    hit = store.search('Revenue')[0]
    monkeypatch.setattr(module, 'MAX_EVIDENCE_CHARS', 30)
    citations, omitted = store._generation_citations([{'citation_id': 1, **hit.to_dict()}])
    assert citations == []
    assert omitted == [{'citation_id': 1, 'reason': 'native_complete_context_unavailable'}]
