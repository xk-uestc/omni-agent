"""Mixed document composition gates using isolated fake providers and stores."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.document_parts_answer import compose_document_parts, REVIEW
from backend import document_parts_answer
from backend.knowledge_store import SourceRevisionError


QUESTION = ('What percentage of board meetings did Morgan attend in 2025, '
            'and what position does Morgan hold within the Group in 2025?')
QUERIES = ['What percentage of board meetings did Morgan attend in 2025?',
           'What position does Morgan hold within the Group in 2025?']
PRIOR = {'status': 'insufficient_evidence', 'answer_mode': 'unsupported',
         'answer': 'No complete answer.', 'trace': []}


def audit(operation='prior', *, completed=True):
    return {'provider': 'responses', 'model': 'gpt-6-luna', 'reasoning': 'medium',
            'response_model': 'gpt-6-luna', 'status': 'completed' if completed else 'failed',
            'model_verified': completed, 'http_status': 200 if completed else 503,
            'operation': operation}


class Client:
    model = 'gpt-6-luna'
    reasoning = 'medium'
    audit_generation = 0
    audit_dropped_count = 0

    def __init__(self):
        self.audit = audit()
        self.audit_history = [deepcopy(self.audit)]
        self.calls = []
        self.plan = {'abstain': False, 'parts': [
            {'part_id': index, 'standalone_question': query}
            for index, query in enumerate(QUERIES, 1)]}
        self.review = {key: True for key in REVIEW['properties']}
        self.fail_operation = None

    def record(self, operation, *, completed=True):
        self.audit = audit(operation, completed=completed)
        self.audit_history.append(deepcopy(self.audit))

    def generate(self, instructions, packet, schema, *, name, max_tokens):
        self.calls.append((name, deepcopy(packet)))
        self.record(name, completed=name != self.fail_operation)
        return deepcopy(self.plan if name == 'document_parts_navigation_plan' else self.review)


def components():
    citation = {'citation_id': 1, 'snippet': 'navigation must not replace source',
                'metadata': {'document_id': 'report-r17', 'source_sha256': 'a' * 64, 'page_no': 3},
                'generation_evidence': {'text': 'Morgan attended 5 of 6 board meetings in 2025.'}}
    first = {'status': 'ok', 'answer': '83.33%', 'answer_mode': 'native_table_model_reviewed',
             'citations': [citation], 'answer_scope': {'entity': 'Morgan', 'period': '2025'},
             'computation': {'operation': 'percentage', 'numerator': 5, 'denominator': 6,
                             'value': 83.33, 'precision': 2,
                             'source_sha256': 'a' * 64}}
    second = {'status': 'ok', 'answer': 'Managing Director', 'answer_mode': 'source_span_model_reviewed',
              'citations': [deepcopy(citation)], 'answer_scope': {'entity': 'Morgan', 'period': '2025'}}
    second['citations'][0]['generation_evidence']['text'] = 'Morgan is Managing Director of the Group in 2025.'
    return [first, second]


@pytest.fixture(autouse=True)
def composition_replay(monkeypatch):
    """Isolate orchestration from the separately exercised PDF calculator.

    This replaces only the source replay helper; the fake provider still
    records every fresh audit and the composition gates execute unchanged.
    """
    expected = components()[0]['computation']
    def replay(store, child):
        if child.get('computation') and child['computation'] != expected:
            raise ValueError('fixture_calculation_invalid')
    monkeypatch.setattr(document_parts_answer, '_replay_computation', replay)


class Store:
    def __init__(self):
        self.client = Client()
        self.generator = SimpleNamespace(client=self.client)
        self.children = components()
        self.executions = []
        self.verifications = 0
        self.change_source_at = None
        self.record_component_audit = True
        self.fail_component = None

    def _answer_with_recovery(self, question, **scope):
        index = len(self.executions)
        self.executions.append((question, deepcopy(scope)))
        if self.record_component_audit:
            self.client.record('component_answer', completed=index != self.fail_component)
        return deepcopy(self.children[index])

    def _verify_citation_sources(self, citations):
        self.verifications += 1
        if self.change_source_at == self.verifications:
            raise SourceRevisionError('source_changed')

    def retrieval_health(self):
        return {'mode': 'fake'}


def run(store, *, start=(0, 0)):
    return compose_document_parts(store, QUESTION, PRIOR, top_k=4,
                                  document_id='report-r17', page_no=3, answer_audit_start=start)


def test_success_preserves_original_question_scope_literals_and_server_computation():
    store = Store()
    result, trace = run(store)
    assert result and result['answer'] == '1. 83.33%\n\n2. Managing Director'
    assert result['question'] == QUESTION
    assert result['calculator_input_eligible'] is False
    assert result['component_answers'][0]['computation'] == store.children[0]['computation']
    assert result['semantic_verification'] == 'independent_model_review_not_formal_entailment'
    assert trace['status'] == 'complete_original_question_reviewed'
    assert store.executions == [(query, {'top_k': 4, 'document_id': 'report-r17', 'page_no': 3})
                                for query in QUERIES]
    packet = store.client.calls[-1][1]
    assert packet['original_question'] == QUESTION
    assert [row['answer_scope'] for row in packet['components']] == [
        child['answer_scope'] for child in store.children]
    assert all('navigation must not' not in row['evidence'][0]['text'] for row in packet['components'])


def test_dossier_role_component_reaches_whole_question_review(monkeypatch):
    from backend import source_answer_dossier
    store = Store()
    store.children[1]['answer_mode'] = 'native_page_dossier_model_reviewed'
    context = {'original_pages': [{'document_id': 'report-r17', 'source_sha256': 'a'*64,
        'page_no': 3, 'text': 'Morgan is Managing Director of the Group in 2025.',
        'text_sha256': 'b'*64}], 'support_quotes': [{'document_id': 'report-r17',
        'source_sha256': 'a'*64, 'page_no': 3,
        'quote': 'Morgan is Managing Director of the Group in 2025.'}],
        'answer_clauses': [{'text': 'Managing Director', 'support_ids': [1]}],
        'scope': {'included_pages': 1}}
    monkeypatch.setattr(source_answer_dossier, 'replay_context', lambda *args: deepcopy(context))
    result, trace = run(store)
    assert result and trace['status'] == 'complete_original_question_reviewed'
    assert store.client.calls[-1][0] == 'document_parts_original_question_review'
    component = store.client.calls[-1][1]['components'][1]
    assert component['answer_clauses'] == context['answer_clauses']
    assert component['evidence'][0]['text'] == context['support_quotes'][0]['quote']
    assert len(component['native_page_contexts']) == 1
    assert store.verifications >= 6


@pytest.mark.parametrize('change', ['failed_status', 'fallback', 'empty_answer', 'provider_failed'])
def test_one_incomplete_component_prevents_partial_publication(change):
    store = Store()
    if change == 'failed_status':
        store.children[1]['status'] = 'insufficient_evidence'
    elif change == 'fallback':
        store.children[1]['answer_mode'] = 'extractive_fallback'
    elif change == 'empty_answer':
        store.children[1]['answer'] = ''
    else:
        store.fail_component = 1
    result, trace = run(store)
    assert result is None and trace['status'] == 'component_not_complete'
    assert trace['failed_part_id'] == 2
    assert len(store.client.calls) == 1


def test_no_server_computation_cannot_publish_a_calculated_answer():
    store = Store()
    store.children[0].pop('computation')
    result, trace = run(store)
    assert result is None and trace['status'] == 'no_verified_server_computation'
    assert len(store.client.calls) == 1


@pytest.mark.parametrize('condition', ['missing', 'failed', 'evicted', 'reset'])
def test_prior_audit_must_belong_to_current_answer_and_be_successful(condition):
    store = Store()
    if condition == 'missing':
        store.client.audit_history = []
    elif condition == 'failed':
        store.client.audit_history[0] = audit(completed=False)
    elif condition == 'evicted':
        store.client.audit_dropped_count = 1
    else:
        store.client.audit_generation = 1
    result, _ = run(store)
    assert result is None and not store.client.calls and not store.executions


@pytest.mark.parametrize('operation', ['document_parts_navigation_plan', 'document_parts_original_question_review'])
def test_planner_or_whole_question_review_provider_failure_prevents_publication(operation):
    store = Store()
    store.client.fail_operation = operation
    result, _ = run(store)
    assert result is None


@pytest.mark.parametrize('flag', list(REVIEW['properties']))
def test_every_original_question_review_flag_is_required(flag):
    store = Store()
    store.client.review[flag] = False
    result, _ = run(store)
    assert result is None


def test_source_change_at_final_recheck_propagates_without_publication():
    store = Store()
    store.change_source_at = 3
    with pytest.raises(SourceRevisionError):
        run(store)


def test_plan_cannot_introduce_new_entity_or_period():
    store = Store()
    store.client.plan['parts'][1]['standalone_question'] = 'What position does Casey hold in 2024?'
    result, _ = run(store)
    assert result is None and not store.executions


def test_stale_plan_audit_cannot_authorize_components_with_no_new_provider_audit():
    store = Store()
    store.record_component_audit = False
    result, _ = run(store)
    assert result is None


@pytest.mark.parametrize('start', [(), (0,), (0, '0'), (-1, 0), (False, 0)])
def test_malformed_audit_boundary_is_rejected_without_provider_calls(start):
    store = Store()
    result, _ = run(store, start=start)
    assert result is None and not store.client.calls


def test_real_computation_replay_rejects_fake_truthy_metadata_without_original_cells(monkeypatch):
    # Restore the production replay helper: arbitrary truthy computation is
    # insufficient even when every fake model review would otherwise approve.
    monkeypatch.undo()
    store = Store()
    result, _ = run(store)
    assert result is None
    assert len(store.client.calls) == 1


def test_real_computation_replay_rejects_calculation_from_literal_answer_mode(monkeypatch):
    monkeypatch.undo()
    store = Store()
    store.children[0]['answer_mode'] = 'source_span_model_reviewed'
    result, _ = run(store)
    assert result is None
    assert len(store.client.calls) == 1


def test_success_renumbers_outer_citations_but_preserves_child_reference_ids():
    store = Store()
    result, _ = run(store)
    assert result
    assert [row['citation_id'] for row in result['citations']] == [1, 2]
    assert [row['component_part_id'] for row in result['citations']] == [1, 2]
    assert [row['component_citation_id'] for row in result['citations']] == [1, 1]
    assert [child['citations'][0]['citation_id'] for child in result['component_answers']] == [1, 1]


def test_navigation_snippet_without_authoritative_generation_evidence_is_rejected():
    store = Store()
    store.children[1]['citations'][0].pop('generation_evidence')
    result, _ = run(store)
    assert result is None
    assert len(store.client.calls) == 1


def test_component_without_citations_is_rejected_even_when_provider_audit_succeeds():
    store = Store()
    store.children[1]['citations'] = []
    result, trace = run(store)
    assert result is None and trace['status'] == 'component_not_complete'


def test_successful_prior_answer_is_never_regenerated_for_composition():
    store = Store()
    result, _ = compose_document_parts(store, QUESTION, {'status': 'ok'}, top_k=4,
                                       answer_audit_start=(0, 0))
    assert result is None and not store.client.calls and not store.executions
