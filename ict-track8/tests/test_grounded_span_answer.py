"""Mock-only independent semantic review; literal replay is not model replay."""
from copy import deepcopy
import hashlib

import pytest

from backend.grounded_span_answer import bind_grounded_span_answer, replay_literal_span_proof
from backend.responses_client import GenerationError


def fixture(text='Revenue: -125.50 USD.', cid=1):
    claim = {'text': text, 'support': [{'citation_id': cid, 'quote': text}]}
    citation = {'citation_id': cid, 'metadata': {'document_id': 'source', 'source_sha256': 'a' * 64},
                'generation_evidence': {'text': text, 'source_sha256': 'a' * 64,
                    'evidence_sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'source_locator': 'page:1:block:2', 'page_no': 1}}
    return claim, citation


def approved():
    return {'approved': True, 'reason_code': 'supported_unique_complete_scope',
            'all_question_constraints_bound': True, 'unique_answer': True,
            'literal_units_and_signs_preserved': True, 'negation_and_modality_preserved': True,
            'no_evidence_conflict': True}


class Client:
    model, reasoning = 'gpt-6-luna', 'medium'

    def __init__(self, candidate=None, review=None, fail=None, mutate=None):
        self.candidate = candidate or {'abstain': False, 'citation_id': 1, 'answer_span': '-125.50 USD',
            'answer_context': 'Revenue: -125.50 USD.',
            'answer_type': 'quantity', 'scope': [{'citation_id': 1, 'quote': 'Revenue: -125.50 USD.'}]}
        self.review, self.fail, self.mutate = approved() if review is None else review, fail, mutate
        self.calls, self.audit = [], {}

    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append((instructions, deepcopy(context), kwargs))
        self.audit = {'model': self.model, 'reasoning': self.reasoning, 'status': 'completed',
                      'http_status': 200, 'model_verified': True, 'response_model': self.model,
                      'operation': kwargs['name'], 'call_index': len(self.calls)}
        if len(self.calls) == self.fail:
            self.audit['status'] = 'failed'
            raise GenerationError('simulated secret provider exception')
        if self.mutate:
            self.mutate(len(self.calls))
        return deepcopy(self.candidate if len(self.calls) == 1 else self.review)


def run(client=None, text='Revenue: -125.50 USD.'):
    claim, citation = fixture(text)
    client = client or Client()
    result = bind_grounded_span_answer('What amount is revenue?', [claim], [citation], client)
    return result, citation, client


def test_model_reviewed_retains_full_claim_scopes_and_explicit_limit_and_two_audits():
    result, citation, client = run()
    assert result['status'] == 'model_reviewed'
    assert result['answer_value'] == '-125.50 USD'
    assert result['claims'][0]['text'] == 'Revenue: -125.50 USD.'
    assert result['answer_proof']['literal']['offsets'] == [9, 20]
    assert 'not_formal_entailment' in result['answer_proof']['semantic_verification']
    assert result['calculator_input_eligible'] is False
    assert len(client.calls) == len(result['model_audits']) == 2
    assert client.calls[1][1]['evidence'] == client.calls[0][1]['evidence']
    assert replay_literal_span_proof('What amount is revenue?', result, [citation])


@pytest.mark.parametrize('change', [
    {'answer_span': '999 USD'}, {'citation_id': 9}, {'answer_type': 'madeup'},
    {'scope': [{'citation_id': 1, 'quote': 'approved only'}]},
    {'scope': [{'citation_id': 1, 'quote': 'Revenue: -125.50 USD.'}] * 2},
    {'abstain': True}, {'offsets': [0, 1]},
])
def test_bad_selection_never_calls_reviewer(change):
    client = Client()
    client.candidate.update(change)
    result, _, client = run(client)
    assert result['status'] == 'unsupported' and len(client.calls) == 1


def test_literal_failure_exposes_only_static_diagnostic_not_candidate_or_provider_text():
    client = Client()
    client.candidate['answer_context'] = 'private nonexistent source context'
    result, _, _ = run(client)
    assert result['literal_error_code'] == 'answer_context_missing_or_ambiguous'
    assert 'private nonexistent' not in str(result)


def test_literal_event_preserves_actor_action_and_reviewed_date_scope():
    text = '03/11/2040 Request approved by reviewer Mira Chen.'
    claim, citation = fixture(text)
    client = Client(candidate={'abstain': False, 'citation_id': 1,
        'answer_span': 'Request approved by reviewer Mira Chen', 'answer_context': text,
        'answer_type': 'event', 'scope': [{'citation_id': 1, 'quote': text}]})
    result = bind_grounded_span_answer('What action happened on March 11, 2040?', [claim], [citation], client)
    assert result['status'] == 'model_reviewed' and result['answer_type'] == 'event'
    assert result['answer_value'] == 'Request approved by reviewer Mira Chen'
    assert replay_literal_span_proof('What action happened on March 11, 2040?', result, [citation])


def test_repeated_answer_quote_is_not_unique():
    client = Client(candidate={'abstain': False, 'citation_id': 1, 'answer_span': 'USD',
        'answer_context': 'Revenue: 100 USD. Costs: 200 USD.',
        'answer_type': 'quantity', 'scope': []})
    result, _, client = run(client, 'Revenue: 100 USD. Costs: 200 USD.')
    assert result['status'] == 'unsupported' and len(client.calls) == 1


def test_span_outside_supported_claim_never_projects():
    claim, citation = fixture('Revenue: -125.50 USD. Costs: 2 USD.')
    claim = {'text': 'Revenue: -125.50 USD', 'support': [{'citation_id': 1, 'quote': 'Revenue: -125.50 USD'}]}
    client = Client(candidate={'abstain': False, 'citation_id': 1, 'answer_span': '2 USD',
        'answer_context': 'Costs: 2 USD.',
        'answer_type': 'quantity', 'scope': []})
    result = bind_grounded_span_answer('What amount is costs?', [claim], [citation], client)
    assert result['status'] == 'unsupported' and len(client.calls) == 1


@pytest.mark.parametrize('reason', ['unbound_question_scope', 'unit_or_sign_mismatch',
    'forecast_or_negation_mismatch', 'ambiguous_or_conflicting_evidence', 'instruction_injection'])
def test_independent_semantic_rejection_does_not_publish_projection(reason):
    review = approved()
    review.update(approved=False, reason_code=reason)
    result, _, client = run(Client(review=review))
    assert result['status'] == 'unsupported' and len(client.calls) == 2
    assert result['claims'] and 'answer_value' not in result


@pytest.mark.parametrize('key', ['all_question_constraints_bound', 'unique_answer',
    'literal_units_and_signs_preserved', 'negation_and_modality_preserved', 'no_evidence_conflict'])
def test_any_negative_review_guard_overrides_approved(key):
    review = approved()
    review[key] = False
    assert run(Client(review=review))[0]['status'] == 'unsupported'


def test_all_citations_are_in_independent_review_for_conflict_detection():
    claim, citation = fixture()
    _, second = fixture('Revenue: 999 USD.', 2)
    review = approved()
    review.update(approved=False, no_evidence_conflict=False, reason_code='ambiguous_or_conflicting_evidence')
    client = Client(review=review)
    result = bind_grounded_span_answer('What amount is revenue?', [claim], [citation, second], client)
    assert result['status'] == 'unsupported'
    assert len(client.calls[1][1]['evidence']) == 2


@pytest.mark.parametrize('step', [1, 2])
def test_provider_failure_is_sanitized_and_never_retried(step):
    result, _, client = run(Client(fail=step))
    assert result['status'] == 'unsupported' and len(client.calls) == step
    assert 'secret' not in str(result)


@pytest.mark.parametrize('field,value', [('model', 'gpt-6-sol'), ('reasoning', 'high')])
def test_disallowed_model_configuration_calls_nothing(field, value):
    client = Client()
    setattr(client, field, value)
    result, _, client = run(client)
    assert result['status'] == 'unsupported' and not client.calls


@pytest.mark.parametrize('field,value', [('answer_value', '125.50 USD'), ('answer_type', 'entity'),
    ('answer_scope', []), ('claims', []), ('calculator_input_eligible', True)])
def test_public_projection_tampering_fails_literal_replay(field, value):
    result, citation, _ = run()
    result[field] = value
    assert not replay_literal_span_proof('What amount is revenue?', result, [citation])


@pytest.mark.parametrize('field,value', [('offsets', [0, 1]), ('answer_span', '125.50 USD')])
def test_saved_literal_range_tampering_fails_replay(field, value):
    result, citation, _ = run()
    result['answer_proof']['literal'][field] = value
    assert not replay_literal_span_proof('What amount is revenue?', result, [citation])


def test_source_mutation_during_review_fails_even_when_semantic_model_approves():
    claim, citation = fixture()
    client = Client(mutate=lambda step: citation['generation_evidence'].update(source_sha256='b' * 64) if step == 2 else None)
    result = bind_grounded_span_answer('What amount is revenue?', [claim], [citation], client)
    assert result['status'] == 'unsupported' and len(client.calls) == 2


def test_replay_detects_changed_source_or_question_without_model_call():
    result, citation, client = run()
    citation['generation_evidence']['source_sha256'] = 'b' * 64
    assert not replay_literal_span_proof('What amount is revenue?', result, [citation])
    assert not replay_literal_span_proof('What amount is costs?', result, [citation])
    assert len(client.calls) == 2


def test_full_claim_validation_cannot_be_bypassed_with_missing_scope():
    _, citation = fixture('Conditions: approved orders. Revenue: -125.50 USD.')
    claim = {'text': 'Revenue: -125.50 USD', 'support': [{'citation_id': 1, 'quote': 'Revenue: -125.50 USD'}]}
    client = Client()
    result = bind_grounded_span_answer('What amount is revenue?', [claim], [citation], client)
    assert result['status'] == 'unsupported' and not client.calls


def test_positive_substring_of_negative_token_is_not_a_literal_answer():
    client = Client()
    client.candidate['answer_span'] = '125.50 USD'
    result, _, client = run(client)
    assert result['status'] == 'unsupported' and len(client.calls) == 1


@pytest.mark.parametrize('mutation', ['extra', 'wrong_type', 'unknown_code'])
def test_review_schema_requires_boolean_flags_and_known_reason(mutation):
    review = approved()
    if mutation == 'extra': review['secret'] = 'untrusted'
    if mutation == 'wrong_type': review['approved'] = 1
    if mutation == 'unknown_code': review['reason_code'] = 'definitely_correct'
    assert run(Client(review=review))[0]['status'] == 'unsupported'


def test_literal_replay_does_not_accept_changed_saved_semantic_review():
    result, citation, _ = run()
    result['answer_proof']['semantic_review']['approved'] = False
    assert not replay_literal_span_proof('What amount is revenue?', result, [citation])


def test_evidence_budget_stops_before_model_and_class_interface_remains_bounded():
    from backend.grounded_span_answer import GroundedSpanAnswer, replay_literal_proof
    claim, citation = fixture()
    client = Client()
    citations = [fixture('Revenue: 1 USD.', i)[1] for i in range(1, 10)]
    assert GroundedSpanAnswer(client).answer('What amount is revenue?', [claim], citations)['status'] == 'unsupported'
    assert not client.calls
    result = GroundedSpanAnswer(client).answer('What amount is revenue?', [claim], [citation])
    assert replay_literal_proof('What amount is revenue?', result, [citation])


@pytest.mark.parametrize('text,span', [
    ('Revenue: -125.50 USD.', '-125'), ('Revenue: 125.50 USD.', '125'),
    ('Revenue: -125.50 USD.', '125'), ('Revenue: -125.50 USD.', '-125.5'),
    ('Revenue: $125.50 USD.', '125.50'), ('Revenue: -$125.50 USD.', '$125.50'),
    ('Revenue: $-125.50 USD.', '-125.50'), ('Revenue: 1,125.50 USD.', '125.50'),
    ('Revenue: 1,125.50 USD.', '1,125'),
])
def test_partial_decimal_grouping_currency_or_sign_cannot_be_approved(text, span):
    claim, citation = fixture(text)
    client = Client(candidate={'abstain': False, 'citation_id': 1, 'answer_span': span,
        'answer_context': text, 'answer_type': 'quantity', 'scope': []})
    result = bind_grounded_span_answer('What amount is revenue?', [claim], [citation], client)
    assert result['status'] == 'unsupported' and len(client.calls) == 1


def test_unique_context_locates_duplicate_numeric_value_without_accepting_arbitrary_occurrence():
    text = '01/05/2022 5 ANSWER to Complaint. 05/05/2022 6 NOTICE.'
    claim, citation = fixture(text)
    context = '5 ANSWER to Complaint'
    client = Client(candidate={'abstain': False, 'citation_id': 1, 'answer_span': '5',
        'answer_context': context, 'answer_type': 'identifier', 'scope': [{'citation_id': 1, 'quote': text}]})
    result = bind_grounded_span_answer('What number is the ANSWER to Complaint?', [claim], [citation], client)
    assert result['status'] == 'model_reviewed', result
    literal = result['answer_proof']['literal']
    assert literal['offsets'] == [text.index(context), text.index(context) + 1]
    assert literal['context_offsets'] == [text.index(context), text.index(context) + len(context)]
    assert replay_literal_span_proof('What number is the ANSWER to Complaint?', result, [citation])


@pytest.mark.parametrize('text,context', [
    ('5 ANSWER. 5 ANSWER.', '5 ANSWER'),
    ('5 ANSWER and 5 NOTICE.', '5 ANSWER and 5 NOTICE'),
    ('5 ANSWER.', '5 fabricated ANSWER'),
])
def test_duplicate_context_or_duplicate_value_in_context_or_missing_context_refuses(text, context):
    claim, citation = fixture(text)
    client = Client(candidate={'abstain': False, 'citation_id': 1, 'answer_span': '5',
        'answer_context': context, 'answer_type': 'identifier', 'scope': []})
    result = bind_grounded_span_answer('What number is ANSWER?', [claim], [citation], client)
    assert result['status'] == 'unsupported' and len(client.calls) == 1


def test_context_not_entirely_covered_by_verified_support_is_rejected():
    text = 'Revenue: -125.50 USD. Costs: 2 USD.'
    _, citation = fixture(text)
    claim = {'text': 'Revenue: -125.50 USD', 'support': [{'citation_id': 1, 'quote': 'Revenue: -125.50 USD'}]}
    client = Client(candidate={'abstain': False, 'citation_id': 1, 'answer_span': '-125.50 USD',
        'answer_context': text, 'answer_type': 'quantity', 'scope': []})
    assert bind_grounded_span_answer('What amount is revenue?', [claim], [citation], client)['status'] == 'unsupported'
    assert len(client.calls) == 1


def test_context_is_location_only_review_still_has_all_conditions_and_evidence():
    text = 'Conditions: approved only. Revenue: -125.50 USD.'
    claim, citation = fixture(text)
    review = approved()
    review.update(approved=False, all_question_constraints_bound=False, reason_code='unbound_question_scope')
    client = Client(candidate={'abstain': False, 'citation_id': 1, 'answer_span': '-125.50 USD',
        'answer_context': 'Revenue: -125.50 USD', 'answer_type': 'quantity', 'scope': []}, review=review)
    result = bind_grounded_span_answer('What amount is revenue?', [claim], [citation], client)
    assert result['status'] == 'unsupported' and len(client.calls) == 2
    assert client.calls[1][1]['evidence'][0]['text'] == text
    assert client.calls[1][1]['complete_validated_facts'] == [claim]


@pytest.mark.parametrize('field,value', [('context_offsets', [0, 1]), ('context_sha256', 'b' * 64),
                                       ('answer_context', 'Revenue')])
def test_saved_context_proof_mutation_fails_literal_replay(field, value):
    result, citation, _ = run()
    result['answer_proof']['literal'][field] = value
    assert not replay_literal_span_proof('What amount is revenue?', result, [citation])
