"""Cross-page original records, real NULL/units, and complete replay contracts."""
from copy import deepcopy
from types import SimpleNamespace
import fitz
import pytest
from backend.knowledge_store import KnowledgeStore, SourceIntegrityError
from backend.native_row_registry import extract_rows
from backend.native_row_comparison import (CHECKS, bind_comparison, replay_comparison,
    route_native_row_comparison, _sha)
from backend.document_parts_answer import _replay_computation, _review_component
from backend.evidence_recovery import recovery_eligible


def pdf(*, identity='SUBJECT-17', value='35.000 UG/L', shift=0):
    with fitz.open() as document:
        for pno in range(2):
            page = document.new_page(width=700, height=400)
            page.insert_text((30, 35), 'Record ID: ' + (identity if pno else 'SUBJECT-17'), fontsize=9)
            coordinates = [30, 230, 350, 470, 590]
            if pno:
                coordinates = [x + shift for x in coordinates]
            for x, label in zip(coordinates, ['Description', 'Result', 'When', 'Operator', 'Method']):
                page.insert_text((x, 70), label, fontsize=9)
            for row in range(3):
                number = value if pno and row == 0 else '211.000 UG/L' if row == 0 else '10 MG/L'
                cells = ['Entity ' + chr(65 + pno * 3 + row), number, '2025-01-01', 'PERSON_A', 'ISO_A']
                for x, cell in zip(coordinates, cells):
                    page.insert_text((x, 86 + row * 16), cell, fontsize=9)
        return document.tobytes()


class Client:
    model = 'gpt-6-luna'
    reasoning = 'medium'
    def __init__(self, *, approve=True, on_review=None):
        self.calls = []; self.approve = approve; self.on_review = on_review
    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append(kwargs['name'])
        self.audit = {'status': 'completed', 'model_verified': True, 'http_status': 200,
            'model': self.model, 'response_model': self.model, 'reasoning': self.reasoning}
        if kwargs['name'] == 'native_row_comparison_selection':
            registry = context['original_registries'][0]
            assert len(registry['records']) == 6
            return {'abstain': False, 'left_row_id': registry['records'][0]['row_id'],
                'right_row_id': registry['records'][3]['row_id'], 'column_index': 1,
                'scope_label': 'Record ID', 'scope_value': 'SUBJECT-17'}
        assert len(context['original_registries'][0]['pages']) == 2
        assert 'Entity F' in context['original_registries'][0]['pages'][1]['text']
        if self.on_review:
            self.on_review()
        return {key: self.approve for key in CHECKS}


def setup(tmp_path, **kwargs):
    client = Client()
    store = KnowledgeStore(tmp_path / 'knowledge', generator=SimpleNamespace(client=client))
    store.ingest(pdf(**kwargs), document_id='sample', title='Sample records', modality='pdf', filename='data.pdf')
    return store, client, store.search('Entity A Entity D', document_id='sample')


QUESTION = 'Compare Entity A with Entity D for Record ID SUBJECT-17.'


def answer(store, hits):
    return route_native_row_comparison(store, QUESTION, hits, document_id='sample')[0]


def test_complete_comparison_and_source_replay(tmp_path):
    store, client, hits = setup(tmp_path)
    result = answer(store, hits)
    assert result['status'] == 'ok'
    assert result['computation']['operands'] == ['211.000 UG/L', '35.000 UG/L']
    assert result['computation']['operator'] == '>'
    assert [c['metadata']['page_no'] for c in result['citations']] == [1, 2]
    assert replay_comparison(store, result)
    assert not recovery_eligible(result, client)
    assert [t.get('tool') for t in result['trace'][1:]] == ['document.table.read', 'document.compare']
    _replay_computation(store, result)
    context = _review_component(1, QUESTION, result, store=store)
    assert len(context['native_row_context']['original_registries'][0]['pages']) == 2
    assert len(context['native_page_contexts']) == 2
    assert 'Entity F' in list(context['native_page_contexts'].values())[1]['complete_native_page_text']
    assert client.calls == ['native_row_comparison_selection', 'native_row_comparison_independent_review']


@pytest.mark.parametrize('kwargs', [{'identity': 'SUBJECT-99'}, {'value': '35.000 MG/L'},
    {'value': '<200 UG/L'}, {'value': 'NO_RESULT'}, {'shift': 20}])
def test_invalid_subject_units_censored_missing_and_alignment(tmp_path, kwargs):
    store, client, hits = setup(tmp_path, **kwargs)
    assert answer(store, hits) is None
    assert len(client.calls) <= 1


def test_rejected_independent_review_never_returns_answer(tmp_path):
    store, client, hits = setup(tmp_path); client.approve = False
    assert answer(store, hits) is None
    assert len(client.calls) == 2


def test_explicit_page_scope_cannot_expand_to_sibling(tmp_path):
    store, client, hits = setup(tmp_path)
    result, trace = route_native_row_comparison(store, QUESTION, hits, document_id='sample', page_no=1)
    assert result is None and trace['status'] == 'not_applicable' and not client.calls


@pytest.mark.parametrize('path,value', [('answer', 'wrong answer'), ('status', 'failed'),
    ('calculator_input_eligible', True), ('computation.operator', '<'),
    ('citations.0.metadata.page_no', 2), ('answer_scope.unit', 'MG/L'),
    ('native_row_proof.selected_rows.0.fields.1.text', '999 UG/L')])
def test_replay_rejects_mutated_output_scope_or_cell(tmp_path, path, value):
    store, _, hits = setup(tmp_path); result = answer(store, hits); altered = deepcopy(result)
    target = altered; keys = path.split('.')
    for key in keys[:-1]:
        target = target[int(key)] if isinstance(target, list) else target[key]
    target[int(keys[-1]) if isinstance(target, list) else keys[-1]] = value
    altered['native_row_proof_sha256'] = _sha(altered['native_row_proof'])
    assert not replay_comparison(store, altered)


def test_source_replaced_during_review_propagates(tmp_path):
    store, client, hits = setup(tmp_path)
    client.on_review = lambda: store.ingest(pdf(value='99.000 UG/L'), document_id='sample',
        title='Changed', modality='pdf', filename='data.pdf')
    with pytest.raises(SourceIntegrityError):
        answer(store, hits)


def test_store_dispatches_real_row_tool(tmp_path):
    store, _, _ = setup(tmp_path)
    result = store._answer_hits(QUESTION, store.search(QUESTION, document_id='sample'), document_id='sample')
    assert result['answer_mode'] == 'native_row_comparison_model_reviewed'
