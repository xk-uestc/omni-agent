"""Store integration: fresh source/chunk replay around span model review."""
import json

import pytest

from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.responses_client import GenerationError


class Client:
    model, reasoning = 'gpt-6-luna', 'medium'

    def __init__(self, approve=True, mutate=None, fail=False):
        self.approve, self.mutate, self.fail = approve, mutate, fail
        self.calls, self.audit = [], {}

    def generate(self, instructions, context, schema, **kwargs):
        name = kwargs['name']
        self.calls.append(name)
        self.audit = {'model': self.model, 'reasoning': self.reasoning, 'status': 'completed',
            'http_status': 200, 'model_verified': True, 'response_model': self.model, 'operation': name}
        if name.startswith('grounded_answer'):
            hit = context['evidence'][0]
            return {'abstain': False, 'claims': [{'text': hit['text'],
                'support': [{'citation_id': hit['citation_id'], 'quote': hit['text']}]}]}
        if name == 'grounded_span_selection':
            hit = context['evidence'][0]
            return {'abstain': False, 'citation_id': hit['citation_id'], 'answer_span': 'Mira Chen',
                'answer_context': hit['text'], 'answer_type': 'entity',
                'scope': [{'citation_id': hit['citation_id'], 'quote': hit['text']}]}
        if self.mutate:
            self.mutate()
        if self.fail:
            self.audit['status'] = 'failed'
            raise GenerationError('private provider message')
        return {'approved': self.approve,
            'reason_code': 'supported_unique_complete_scope' if self.approve else 'unbound_question_scope',
            'all_question_constraints_bound': self.approve, 'unique_answer': True,
            'literal_units_and_signs_preserved': True, 'negation_and_modality_preserved': True,
            'no_evidence_conflict': True}


def store_fixture(tmp_path, **kwargs):
    client = Client(**kwargs)
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    store.ingest(b'Finance director: Mira Chen.', document_id='record', title='Finance director',
                 modality='txt', filename='record.txt')
    return store, client


def test_typed_projection_has_priority_actual_short_answer_and_no_extra_model_calls(tmp_path):
    store, client = store_fixture(tmp_path)
    result = store.answer('Who is the finance director?')
    assert result['answer'] == 'Mira Chen'
    assert result['answer_strategy'] == 'deterministic_typed_projection'
    assert 'Finance director: Mira Chen.' in result['full_fact_answer']
    assert result['answer_projection']['status'] == 'verified'
    assert len(client.calls) == 1 and 'answer_span_result' not in result


def test_unsupported_typed_question_runs_two_review_steps_then_fresh_literal_replay(tmp_path):
    store, client = store_fixture(tmp_path)
    result = store.answer('Name the finance director')
    assert result['answer'] == 'Mira Chen'
    assert result['answer_strategy'] == 'model_reviewed_source_span'
    assert result['answer_mode'] == 'model_grounded'
    assert 'Finance director: Mira Chen.' in result['full_fact_answer']
    assert result['answer_span_result']['status'] == 'model_reviewed'
    assert client.calls == ['grounded_answer', 'grounded_span_selection', 'grounded_span_independent_review']
    assert result['trace'][-1]['status'] == 'model_reviewed'
    assert len(result['trace'][-1]['model_audits']) == 2


@pytest.mark.parametrize('kwargs', [{'approve': False}, {'fail': True}])
def test_rejected_or_failed_review_retains_complete_answer(tmp_path, kwargs):
    store, client = store_fixture(tmp_path, **kwargs)
    result = store.answer('Name the finance director')
    assert 'Finance director: Mira Chen.' in result['answer']
    assert 'answer_span_result' not in result and 'full_fact_answer' not in result
    assert result['trace'][-1]['original_answer_retained'] is True
    assert len(client.calls) == 3 and 'private provider message' not in str(result)


@pytest.mark.parametrize('mutation', ['source', 'chunk', 'delete'])
@pytest.mark.parametrize('approve', [True, False])
def test_source_or_chunk_changed_during_review_raises_even_after_rejection(tmp_path, mutation, approve):
    store, client = store_fixture(tmp_path, approve=approve)
    def mutate():
        if mutation == 'source':
            store.ingest(b'Finance director: Noel Jones.', document_id='record', title='Finance director',
                         modality='txt', filename='record.txt')
            return
        with store.connect() as connection:
            row = connection.execute('SELECT chunk_id,payload FROM chunks LIMIT 1').fetchone()
            if mutation == 'delete':
                connection.execute('DELETE FROM chunks WHERE chunk_id=?', (row[0],))
            else:
                chunk = json.loads(row[1])
                chunk['metadata']['injected'] = 'changed snapshot'
                connection.execute('UPDATE chunks SET payload=? WHERE chunk_id=?', (json.dumps(chunk), row[0]))
    client.mutate = mutate
    with pytest.raises(SourceRevisionError):
        store.answer('Name the finance director')
    assert len(client.calls) == 3


def test_literal_replay_failure_keeps_full_answer_and_does_not_claim_semantic_failure(tmp_path, monkeypatch):
    store, client = store_fixture(tmp_path)
    monkeypatch.setattr('backend.grounded_span_answer.replay_literal_proof', lambda *args: False)
    result = store.answer('Name the finance director')
    assert 'Finance director: Mira Chen.' in result['answer']
    assert 'answer_span_result' not in result
    assert result['trace'][-1]['status'] == 'literal_replay_failed'


def test_old_client_without_readable_reasoning_never_gets_extra_calls(tmp_path):
    store, client = store_fixture(tmp_path)
    client.reasoning = None
    result = store.answer('Name the finance director')
    assert 'Finance director: Mira Chen.' in result['answer']
    assert len(client.calls) == 1


@pytest.mark.parametrize('question,strategy', [
    ('Who is the finance director?', 'deterministic_typed_projection'),
    ('Name the finance director', 'model_reviewed_source_span'),
])
def test_unchanged_initial_omission_does_not_block_valid_projection(tmp_path, monkeypatch, question, strategy):
    store, _ = store_fixture(tmp_path)
    original = store._generation_citations
    def rehydrate(citations):
        selected, omitted = original(citations)
        return selected, omitted + [{'citation_id': 4, 'reason': 'native_complete_context_unavailable'}]
    monkeypatch.setattr(store, '_generation_citations', rehydrate)
    result = store.answer(question)
    assert result['answer'] == 'Mira Chen' and result['answer_strategy'] == strategy
    assert result['trace'][-1]['complete_facts_retained'] is True
    assert result['trace'][-1]['full_fact_answer_field'] == 'full_fact_answer'


@pytest.mark.parametrize('question', ['Who is the finance director?', 'Name the finance director'])
@pytest.mark.parametrize('change', ['new_omission', 'omission_reason', 'metadata', 'generation_evidence', 'new_source'])
def test_any_fresh_omission_or_selected_snapshot_change_refuses_projection(tmp_path, monkeypatch, question, change):
    from copy import deepcopy
    store, _ = store_fixture(tmp_path)
    original, count = store._generation_citations, 0
    def rehydrate(citations):
        nonlocal count
        selected, omitted = original(citations)
        count += 1
        omitted = omitted + [{'citation_id': 4, 'reason': 'native_complete_context_unavailable'}]
        if count > 1:
            selected = deepcopy(selected)
            if change == 'new_omission': omitted.append({'citation_id': 5, 'reason': 'new_missing'})
            if change == 'omission_reason': omitted[0]['reason'] = 'changed_reason'
            if change == 'metadata': selected[0]['metadata']['new_scope'] = 'unproved'
            if change == 'generation_evidence': selected[0]['generation_evidence']['truncated'] = True
            if change == 'new_source': selected.append(deepcopy(selected[0]))
        return selected, omitted
    monkeypatch.setattr(store, '_generation_citations', rehydrate)
    result = store.answer(question)
    assert 'Finance director: Mira Chen.' in result['answer']
    assert 'answer_strategy' not in result
    assert result['trace'][-1]['status'] in ('proof_replay_failed', 'literal_replay_failed')


def test_safe_literal_error_code_is_retained_in_failed_span_trace(tmp_path):
    store, client = store_fixture(tmp_path)
    original = client.generate
    def generate(instructions, context, schema, **kwargs):
        response = original(instructions, context, schema, **kwargs)
        if kwargs['name'] == 'grounded_span_selection':
            response['answer_context'] = 'not in original source'
        return response
    client.generate = generate
    result = store.answer('Name the finance director')
    assert result['trace'][-1]['status'] == 'unsupported'
    assert result['trace'][-1]['literal_error_code'] == 'answer_context_missing_or_ambiguous'
