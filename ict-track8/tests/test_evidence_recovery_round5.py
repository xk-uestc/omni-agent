from copy import deepcopy
import time

import pytest

from backend.evidence_recovery import audit_boundary, plan_evidence_recovery, recovery_eligible
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.evidence_context import MAX_TOTAL_EVIDENCE_CHARS, text_sha256
from backend.responses_client import GenerationError, StructuredResponses


def completed(operation='grounded_answer'):
    return {'status': 'completed', 'model_verified': True, 'model': 'gpt-6-luna',
            'reasoning': 'medium', 'response_model': 'gpt-6-luna',
            'http_status': 200, 'operation': operation}


class Client:
    model = 'gpt-6-luna'
    reasoning = 'medium'

    def __init__(self, proposal=None):
        self.proposal = proposal or {'need_more_evidence': True, 'queries': ['harbor dispatch lighthouse']}
        self.calls, self.audit_history, self.audit = [], [], {}

    def generate(self, instructions, context, schema, *, name, **kwargs):
        self.calls.append({'operation': name, 'context': deepcopy(context)})
        self.audit = completed(name)
        self.audit_history.append(deepcopy(self.audit))
        if name == 'evidence_recovery_queries':
            if isinstance(self.proposal, Exception):
                self.audit.update(status='failed', http_status=502, model_verified=False)
                raise self.proposal
            return deepcopy(self.proposal)
        if name == 'grounded_answer':
            for evidence in context['evidence']:
                if 'lighthouse maintenance' in evidence['text']:
                    text = evidence['text']
                    return {'abstain': False, 'claims': [{'text': text, 'support': [
                        {'citation_id': evidence['citation_id'], 'quote': text}]}]}
            return {'abstain': True, 'claims': []}
        if name in {'source_span_selection', 'grounded_span_selection'}:
            return {'abstain': True, 'answer_span': '', 'answer_context_id': '',
                    'answer_type': 'event', 'scope_ids': []}
        raise AssertionError(name)


def ingest(store, text, key):
    store.ingest(text.encode(), document_id=key, title='Harbor dispatch',
                 modality='txt', filename=key + '.txt')


def test_real_source_retrieval_recovery_answers_original_question_once(tmp_path, monkeypatch):
    client = Client()
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    ingest(store, 'Harbor dispatch includes seasonal warnings.', 'distractor')
    ingest(store, 'After written approval, harbor dispatch includes lighthouse maintenance.', 'wanted')
    question = 'What does harbor dispatch include after written approval?'
    search = store.search
    calls = []

    def miss_first(query, **kwargs):
        calls.append((query, kwargs))
        return search(query, document_id='distractor', top_k=1) if query == question else search(query, **kwargs)

    monkeypatch.setattr(store, 'search', miss_first)
    # Make the initial incomplete pipeline abstain rather than claim its
    # unrelated but literally supported dispatch clause is a complete answer.
    result = store.answer(question)
    assert result['question'] == question
    assert 'After written approval' in result['answer'] and 'lighthouse maintenance' in result['answer']
    assert result['status'] == 'ok'
    assert result['evidence_recovery']['prior_status'] == 'insufficient_evidence'
    assert result['evidence_recovery']['navigation_audit']['round_limit'] == 1
    assert sum(c['operation'] == 'evidence_recovery_queries' for c in client.calls) == 1
    assert all(c['context']['question'] == question for c in client.calls if c['operation'] == 'grounded_answer')
    assert len(calls) == 2
    assert result['claims'][0]['support'][0]['quote'] in result['citations'][1]['generation_evidence']['text'] or any(
        result['claims'][0]['support'][0]['quote'] in c['generation_evidence']['text'] for c in result['citations'])


def test_retrieval_preserves_explicit_document_scope(tmp_path, monkeypatch):
    client = Client()
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    ingest(store, 'Harbor dispatch includes seasonal warnings.', 'allowed')
    ingest(store, 'After written approval, harbor dispatch includes lighthouse maintenance.', 'other')
    original = store.search
    scopes = []

    def observe(query, **kwargs):
        scopes.append(kwargs)
        return original(query, **kwargs)

    monkeypatch.setattr(store, 'search', observe)
    result = store.answer('What does harbor dispatch include after written approval?', document_id='allowed')
    assert all(scope['document_id'] == 'allowed' for scope in scopes)
    assert result['status'] == 'insufficient_evidence'
    assert all(c['metadata']['document_id'] == 'allowed' for c in result['citations'])
    assert result['trace'][-1]['status'] == 'no_new_source_evidence'


def test_new_queries_keep_original_identifier_and_year_without_changing_question(tmp_path):
    store = KnowledgeStore(tmp_path)
    ingest(store, 'CASE-341 harbor dispatch 2031.', 'case')
    client = Client({'need_more_evidence': True, 'queries': ['lighthouse approval', 'lighthouse approval']})
    question = 'CASE-341 harbor dispatch in 2031?'
    queries, audit = plan_evidence_recovery(question, store.search(question), client)
    assert queries == ['lighthouse approval CASE-341 2031']
    assert client.calls[0]['context']['original_question'] == question
    assert audit['navigation_only'] is True and audit['semantic_sufficiency'] == 'not_established'


@pytest.mark.parametrize('proposal', [
    {'need_more_evidence': False, 'queries': ['invented']},
    {'need_more_evidence': True, 'queries': ['', 'too short']},
    {'need_more_evidence': 'true', 'queries': ['x']},
    {'need_more_evidence': True, 'queries': ['x'] * 4},
    {'need_more_evidence': True, 'queries': ['x' * 501]},
    {'need_more_evidence': True, 'queries': ['x'], 'answer': 'invented'},
])
def test_untrusted_query_contract_never_enters_search(proposal):
    queries, audit = plan_evidence_recovery('original question', [], Client(proposal))
    assert queries == [] and audit['status'] == 'query_contract_invalid'


def test_provider_failure_is_preserved_and_does_not_retry():
    client = Client(GenerationError('unavailable', status=502))
    queries, audit = plan_evidence_recovery('original question', [], client)
    assert not queries and audit['status'] == 'provider_unavailable' and len(client.calls) == 1
    assert 'unavailable' not in str(audit.get('error', ''))


def test_successful_answer_or_failed_provider_never_gets_recovery():
    client = Client()
    client.audit = completed()
    client.audit_history = [completed()]
    result = {'status': 'insufficient_evidence', 'answer_mode': 'model_grounded',
              'generation_attempts': [{'validation_status': 'validated', 'provider_audit': completed()}]}
    assert recovery_eligible(result, client)
    assert not recovery_eligible({**result, 'status': 'ok'}, client)
    client.audit.update(status='failed', http_status=502)
    assert not recovery_eligible(result, client)


def test_earlier_provider_failure_in_same_answer_blocks_recovery_even_when_latest_completed():
    client = Client()
    failed = {**completed('native_table_fact_selection'), 'status': 'failed',
              'http_status': 503, 'model_verified': False}
    client.audit_history = [failed, completed(), completed('source_span_selection')]
    client.audit = client.audit_history[-1]
    result = {'status': 'insufficient_evidence', 'answer_mode': 'model_grounded',
              'generation_attempts': [{'validation_status': 'validated', 'provider_audit': completed()}]}
    assert not recovery_eligible(result, client)


def test_previous_answer_failure_does_not_disable_independent_new_answer_recovery():
    client = Client()
    client.audit_history = [{**completed(), 'status': 'failed', 'http_status': 503}]
    boundary = audit_boundary(client)
    client.audit = completed()
    client.audit_history.append(client.audit)
    result = {'status': 'insufficient_evidence', 'answer_mode': 'model_grounded',
              'generation_attempts': [{'validation_status': 'validated', 'provider_audit': completed()}]}
    assert recovery_eligible(result, client, answer_audit_start=boundary)
    assert not recovery_eligible(result, client)


def test_missing_current_answer_audits_fail_closed_but_old_answer_eviction_is_allowed():
    client = Client()
    client.audit = completed()
    client.audit_history = [completed()]
    client.audit_dropped_count = 10
    result = {'status': 'insufficient_evidence', 'answer_mode': 'model_grounded',
              'generation_attempts': [{'validation_status': 'validated', 'provider_audit': completed()}]}
    assert recovery_eligible(result, client, answer_audit_start=(0, 10))
    assert not recovery_eligible(result, client, answer_audit_start=(0, 9))
    assert not recovery_eligible(result, client, answer_audit_start=(0, 12))
    assert not recovery_eligible(result, client, answer_audit_start=None)


@pytest.mark.parametrize('prior_calls', [0, 3])
def test_audit_reset_cannot_hide_this_answers_failure_even_after_counter_catches_up(prior_calls):
    client = StructuredResponses('http://127.0.0.1', 'synthetic', model='gpt-6-luna')
    for _ in range(prior_calls):
        client._record(completed(), time.perf_counter())
    boundary = audit_boundary(client)
    client._record({**completed(), 'status': 'failed', 'http_status': 503}, time.perf_counter())
    client.reset_audit()
    for _ in range(prior_calls + 1):
        client._record(completed(), time.perf_counter())
    result = {'status': 'insufficient_evidence', 'answer_mode': 'model_grounded',
              'generation_attempts': [{'validation_status': 'validated', 'provider_audit': completed()}]}
    assert not recovery_eligible(result, client, answer_audit_start=boundary)
    # Reset between independent requests is valid when the new request takes
    # its own epoch and count snapshot.
    new_boundary = audit_boundary(client)
    client._record(completed(), time.perf_counter())
    assert recovery_eligible(result, client, answer_audit_start=new_boundary)


def test_store_scopes_all_model_calls_including_routing_failure(tmp_path, monkeypatch):
    client = Client()
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    ingest(store, 'Harbor dispatch includes seasonal warnings.', 'case')
    answer_hits = store._answer_hits
    def failed_route(*args, **kwargs):
        failed = {**completed('native_table_fact_selection'), 'status': 'failed',
                  'http_status': 503, 'model_verified': False}
        client.audit = failed
        client.audit_history.append(failed)
        return answer_hits(*args, **kwargs)
    monkeypatch.setattr(store, '_answer_hits', failed_route)
    result = store.answer('What does harbor dispatch include after written approval?')
    assert result['status'] == 'insufficient_evidence'
    assert not any(call['operation'] == 'evidence_recovery_queries' for call in client.calls)


def test_source_replaced_during_navigation_cannot_authorize_old_facts(tmp_path, monkeypatch):
    client = Client()
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    ingest(store, 'Harbor dispatch includes seasonal warnings.', 'case')
    real_generate = client.generate

    def replace(instructions, context, schema, **kwargs):
        if kwargs['name'] == 'evidence_recovery_queries':
            ingest(store, 'Harbor dispatch cancelled all service.', 'case')
        return real_generate(instructions, context, schema, **kwargs)

    monkeypatch.setattr(client, 'generate', replace)
    with pytest.raises(SourceRevisionError):
        store.answer('What does harbor dispatch include after written approval?')


def test_navigation_sees_original_condition_beyond_retrieval_preview(tmp_path):
    store = KnowledgeStore(tmp_path)
    text = ('Harbor dispatch covers routine maintenance. ' + 'Routine operational notes. ' * 20
            + 'After written approval, the lighthouse dispatch window starts at dawn.')
    ingest(store, text, 'case')
    hits = store.search('harbor dispatch', top_k=1)
    assert 'window starts at dawn' not in hits[0].snippet
    citations, _ = store._generation_citations([
        {'citation_id': 1, **hits[0].to_dict()}])
    # Poisoned convenience metadata must never become the model context.
    hits[0].metadata['raw_page_text'] = 'FORGED secret approval at midnight'
    hits[0].metadata['raw_text'] = 'FORGED secret approval at midnight'
    client = Client()
    _, audit = plan_evidence_recovery('When does harbor dispatch start?', hits, client,
                                      source_contexts=citations)
    fragment = client.calls[0]['context']['retrieved_fragments'][0]
    assert fragment['fragment_mode'] == 'authoritative_context'
    assert 'window starts at dawn' in fragment['fragment']
    assert 'FORGED' not in fragment['fragment']
    assert audit['navigation_contexts'][0]['sha256'] == text_sha256(fragment['fragment'])
    assert audit['navigation_total_chars'] <= MAX_TOTAL_EVIDENCE_CHARS


def test_store_reconstructs_navigation_context_and_replays_after_call(tmp_path, monkeypatch):
    client = Client()
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    ingest(store, 'Harbor dispatch includes seasonal warnings.', 'case')
    calls = []
    verify = store._verify_generation_chunks
    def observe(citations):
        if any(call['operation'] == 'evidence_recovery_queries' for call in client.calls):
            calls.append('after_navigation')
        else:
            calls.append('before_navigation')
        return verify(citations)
    monkeypatch.setattr(store, '_verify_generation_chunks', observe)
    result = store.answer('What does harbor dispatch include after written approval?')
    assert 'before_navigation' in calls and 'after_navigation' in calls
    assert result['trace'][-1]['navigation_contexts'][0]['mode'] == 'authoritative_context'


@pytest.mark.parametrize('field,value', [('text', 'forged'), ('source_sha256', '0' * 64),
    ('source_locator', 'other'), ('page_no', 99)])
def test_mismatched_original_navigation_context_never_calls_provider(tmp_path, field, value):
    store = KnowledgeStore(tmp_path)
    ingest(store, 'Harbor dispatch includes seasonal warnings.', 'case')
    hits = store.search('harbor dispatch', top_k=1)
    contexts, _ = store._generation_citations([{'citation_id': 1, **hits[0].to_dict()}])
    contexts[0]['generation_evidence'][field] = value
    client = Client()
    with pytest.raises(ValueError, match='context_contract_mismatch'):
        plan_evidence_recovery('harbor dispatch', hits, client, source_contexts=contexts)
    assert client.calls == []


def test_navigation_combines_original_context_and_previews_under_same_total_budget(tmp_path):
    store = KnowledgeStore(tmp_path)
    for index in range(8):
        ingest(store, 'Harbor dispatch includes seasonal warnings.', f'case-{index}')
    hits = store.search('harbor dispatch', top_k=8)
    contexts, _ = store._generation_citations([
        {'citation_id': i, **hit.to_dict()} for i, hit in enumerate(hits, 1)])
    # Omitted first preview cannot consume the budget reserved for originals.
    contexts = contexts[1:]
    for item in contexts:
        evidence = item['generation_evidence']
        evidence['text'] = 'x' * 1700
        evidence['evidence_sha256'] = text_sha256(evidence['text'])
    client = Client()
    _, audit = plan_evidence_recovery('harbor dispatch', hits, client, source_contexts=contexts)
    assert audit['navigation_total_chars'] <= MAX_TOTAL_EVIDENCE_CHARS
    assert audit['navigation_contexts'][0]['mode'] == 'excerpt_navigation_only'
    contexts[0]['generation_evidence']['text'] = 'x' * MAX_TOTAL_EVIDENCE_CHARS
    with pytest.raises(ValueError, match='context_budget_exceeded'):
        plan_evidence_recovery('harbor dispatch', hits, Client(), source_contexts=contexts)
