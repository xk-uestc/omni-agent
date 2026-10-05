"""Mock-only coverage/provenance gates; no benchmark answers or API calls."""
from copy import deepcopy

import pytest

from backend.source_multi_span_answer import (bind_multi_source_answer, replay_multi_source_proof,
    _REVIEW_FLAGS, MAX_ANSWER_CHARS)
from backend.grounded_span_answer import _sha
from backend.responses_client import GenerationError
from tests.test_grounded_span_answer import fixture


QUESTION = 'Which components were assayed using method X, and by whom were they assayed?'
TEXTS = ['Components assayed using X: Quartz, Zinc.', 'The analyst for method X is Mira Chen.']
FRAGMENTS = [(1, 'Quartz, Zinc', [1], 'entity'), (2, 'Mira Chen', [2], 'entity')]
MATCHES = [(1, 1, 'Quartz'), (1, 1, 'Zinc'), (2, 2, 'Mira Chen')]


def test_literal_missing_record_cannot_masquerade_as_complete_person_answer():
    texts = ['The research lead is Mira Chen.', 'No finance lead is stated in this record.']
    client = Client([(1, 'Mira Chen', [1], 'entity'),
                     (2, texts[1], [2], 'event')])
    result, _, client = run(client, texts, 'Who is the research lead and who is the finance lead?')
    assert result['reason'] == 'multi_explicit_missing_fact'
    assert 'answer_value' not in result and len(client.calls) == 1


class Client:
    model, reasoning = 'gpt-6-luna', 'medium'

    def __init__(self, fragments=FRAGMENTS, matches=MATCHES, *, reject=None, fail=None,
                 selection_hook=None, review_hook=None, source_hook=None, audit_change=None):
        self.fragments, self.matches = deepcopy(fragments), deepcopy(matches)
        self.reject, self.fail = reject, fail
        self.selection_hook, self.review_hook = selection_hook, review_hook
        self.source_hook, self.audit_change = source_hook, audit_change
        self.calls, self.audit = [], {}

    def generate(self, instructions, context, schema, **kwargs):
        name = kwargs['name']
        self.calls.append({'name': name, 'context': deepcopy(context), 'schema': deepcopy(schema)})
        self.audit = {'status': 'completed', 'http_status': 200, 'model_verified': True,
            'model': self.model, 'reasoning': self.reasoning, 'response_model': self.model,
            'operation': name, 'call_index': len(self.calls)}
        if len(self.calls) == self.fail:
            self.audit.update(status='failed', http_status=401)
            raise GenerationError('private credential/provider exception')
        if self.audit_change and len(self.calls) == self.audit_change[0]:
            self.audit.update(self.audit_change[1])
        registry = {entry['citation_id']: entry['quote_id'] for entry in context['quote_catalog']
            if entry['quote'] == next(source['text'] for source in context['evidence']
                                     if source['citation_id'] == entry['citation_id'])}
        if name.endswith('_selection'):
            result = {'abstain': False, 'fragments': [
                {'part_ids': parts, 'answer_span': span, 'answer_type': kind,
                 'answer_context_id': registry[cid], 'scope_ids': [registry[cid]]}
                for cid, span, parts, kind in self.fragments]}
            if self.selection_hook:
                self.selection_hook(result)
        else:
            result = {key: True for key in _REVIEW_FLAGS}
            result['reason_code'] = 'supported_unique_complete_scope'
            result['matched_items'] = [{'part_id': part, 'answer_context_id': registry[cid],
                                       'answer_span': span} for part, cid, span in self.matches]
            if self.reject:
                result[self.reject] = False
            if self.review_hook:
                self.review_hook(result)
        if self.source_hook:
            self.source_hook(len(self.calls))
        return deepcopy(result)


def run(client=None, texts=TEXTS, question=QUESTION):
    citations = [fixture(text, index + 1)[1] for index, text in enumerate(texts)]
    client = client or Client()
    return bind_multi_source_answer(question, citations, client), citations, client


def test_disjoint_sources_publish_only_original_fragments_and_bind_every_part_item():
    result, citations, client = run()
    assert result['status'] == 'model_reviewed'
    assert result['answer_value'] == 'Quartz, Zinc\nMira Chen'
    assert result['answer_type'] == 'multi_source_literal'
    assert result['coverage_scope'] == 'supplied_evidence_only_not_entire_document'
    assert result['evidence_contract'] == 'raw_source_only_no_validated_facts'
    assert result['calculator_input_eligible'] is False and 'claims' not in result
    assert len(client.calls) == len(result['model_audits']) == 2
    assert client.calls[0]['context']['evidence'] == client.calls[1]['context']['evidence']
    proof = result['answer_proof']
    assert len(proof['matching_inventory']) == 3
    assert proof['fragments'][0]['literal']['offsets'] == [28, 40]
    assert proof['matching_inventory'][1]['fragment_ids'] == [1]
    assert proof['semantic_verification'] == 'independent_model_review_not_formal_entailment'
    assert replay_multi_source_proof(QUESTION, result, citations)


def test_one_complete_original_sentence_can_cover_two_parts_without_duplication():
    text = 'Quartz was assayed using X by Mira Chen.'
    client = Client([(1, text, [1, 2], 'event')], [(1, 1, 'Quartz'), (2, 1, 'Mira Chen')])
    result, citations, _ = run(client, [text])
    assert result['status'] == 'model_reviewed' and result['answer_value'] == text
    assert replay_multi_source_proof(QUESTION, result, citations)


def test_single_part_enumeration_supports_multiple_distinct_source_records():
    question = 'Which components were assayed using method X?'
    client = Client([(1, 'Quartz', [1], 'entity'), (2, 'Zinc', [1], 'entity')],
                    [(1, 1, 'Quartz'), (1, 2, 'Zinc')])
    result, citations, _ = run(client, ['Quartz was assayed using X.', 'Zinc was assayed using X.'], question)
    assert result['answer_value'] == 'Quartz\nZinc'
    assert replay_multi_source_proof(question, result, citations)


@pytest.mark.parametrize('flag', list(_REVIEW_FLAGS))
def test_every_independent_semantic_flag_is_mandatory(flag):
    result, _, client = run(Client(reject=flag))
    assert result['status'] == 'unsupported' and result['reason'] == 'multi_semantic_review_rejected'
    assert 'answer_value' not in result and len(client.calls) == 2


def test_omitted_matching_item_cannot_be_supplied_by_scope_or_true_review_flags():
    client = Client([(1, 'Quartz', [1], 'entity'), FRAGMENTS[1]])
    result, _, _ = run(client)
    assert result['reason'] == 'multi_inventory_item_missing_from_answer'
    assert 'answer_value' not in result
    assert 'Quartz, Zinc' in client.calls[1]['context']['candidate_fragments'][0]['literal']['scope'][0]['quote']


def test_false_part_assignment_is_rejected_even_if_all_boolean_flags_are_true():
    client = Client([(1, 'Quartz, Zinc', [1, 2], 'entity'), FRAGMENTS[1]])
    result, _, _ = run(client)
    assert result['reason'] == 'multi_inventory_part_mapping_incomplete'


@pytest.mark.parametrize('fragments,reason', [
    ([FRAGMENTS[0]], 'multi_parts_not_covered'),
    ([FRAGMENTS[0], FRAGMENTS[0], FRAGMENTS[1]], 'multi_duplicate_fragment'),
    ([(1, 'Quartz, Zinc', [True], 'entity'), FRAGMENTS[1]], 'multi_fragment_contract_invalid'),
    ([(1, 'Quartz, Zinc', [1, 1], 'entity'), FRAGMENTS[1]], 'multi_fragment_contract_invalid'),
    ([(1, 'Quartz, Zinc', [7], 'entity'), FRAGMENTS[1]], 'multi_fragment_contract_invalid'),
    ([(1, 'private hallucination', [1], 'entity'), FRAGMENTS[1]], 'multi_source_contract_invalid'),
    ([(1, 'Quartz, Zinc', [1], 'invented-type'), FRAGMENTS[1]], 'multi_source_contract_invalid'),
])
def test_invalid_fragment_contract_stops_before_review(fragments, reason):
    result, _, client = run(Client(fragments))
    assert result['reason'] == reason and len(client.calls) == 1
    assert 'private hallucination' not in str(result)


@pytest.mark.parametrize('matches,reason', [
    ([MATCHES[0], MATCHES[0], MATCHES[2]], 'multi_duplicate_inventory_item'),
    ([MATCHES[0], MATCHES[1]], 'multi_inventory_part_mapping_incomplete'),
    ([(True, 1, 'Quartz'), MATCHES[1], MATCHES[2]], 'multi_inventory_contract_invalid'),
    ([(3, 1, 'Quartz'), MATCHES[1], MATCHES[2]], 'multi_inventory_contract_invalid'),
    ([(1, 1, 'Invented'), MATCHES[1], MATCHES[2]], 'multi_source_contract_invalid'),
])
def test_inventory_is_locally_source_bound_and_cannot_invent_or_duplicate_items(matches, reason):
    result, _, client = run(Client(matches=matches))
    assert result['reason'] == reason and len(client.calls) == 2


def test_partial_signed_numeric_token_never_reaches_review():
    text = 'Changes: -123.50 USD; analyst Mira Chen.'
    client = Client([(1, '123.50 USD', [1], 'quantity'), (1, 'Mira Chen', [2], 'entity')])
    result, _, client = run(client, [text], 'What were the changes, and who was the analyst?')
    assert result['status'] == 'unsupported' and len(client.calls) == 1


@pytest.mark.parametrize('call', [1, 2])
def test_provider_failure_never_retries_or_leaks_provider_payload(call):
    result, _, client = run(Client(fail=call))
    assert result['reason'] == 'multi_provider_failed' and len(client.calls) == call
    assert 'private credential' not in str(result) and 'answer_value' not in result


@pytest.mark.parametrize('call', [1, 2])
@pytest.mark.parametrize('change', [
    {'status': 'failed'}, {'model_verified': False}, {'response_model': 'gpt-6-sol'},
    {'http_status': True}, {'http_status': 403}, {'reasoning': 'high'},
])
def test_each_call_requires_completed_verified_allowed_model(call, change):
    result, _, client = run(Client(audit_change=(call, change)))
    assert result['status'] == 'unsupported' and len(client.calls) == call
    assert result['reason'] == ('multi_selection_provider_invalid' if call == 1 else 'multi_review_provider_invalid')


@pytest.mark.parametrize('question', [
    'Who is the analyst?', 'What is the cost?', '',
    'Which components were tested?' + ' and who was the analyst?' * 6,
])
def test_nonapplicable_or_overbudget_question_makes_no_api_call(question):
    result, _, client = run(question=question)
    assert result['status'] == 'unsupported' and not client.calls


@pytest.mark.parametrize('attribute,value', [('model', 'gpt-6-sol'), ('reasoning', 'high')])
def test_disallowed_model_makes_no_api_call(attribute, value):
    client = Client()
    setattr(client, attribute, value)
    result, _, _ = run(client)
    assert result['reason'] == 'multi_model_invalid' and not client.calls


def test_untrusted_unknown_catalog_or_scope_id_never_reaches_review():
    for key in ('answer_context_id', 'scope_ids'):
        def corrupt(selection):
            selection['fragments'][0][key] = 'private unknown' if key == 'answer_context_id' else ['private unknown']
        result, _, client = run(Client(selection_hook=corrupt))
        assert result['status'] == 'unsupported' and len(client.calls) == 1
        assert 'private unknown' not in str(result)


def test_abstention_does_not_trigger_review_or_another_attempt():
    def abstain(selection):
        selection.update(abstain=True, fragments=[])
    result, _, client = run(Client(selection_hook=abstain))
    assert result['status'] == 'unsupported' and len(client.calls) == 1


def test_answer_budget_includes_separators_and_stops_before_review():
    texts = ['Record' + chr(65 + index) + ': ' + chr(97 + index) * 260 for index in range(7)]
    assert len('\n'.join(texts)) > MAX_ANSWER_CHARS
    client = Client([(i + 1, text, [1], 'event') for i, text in enumerate(texts)], [])
    result, _, client = run(client, texts, 'Which items were recorded?')
    assert result['reason'] == 'multi_answer_budget' and len(client.calls) == 1


def test_source_payload_change_during_review_never_publishes_answer():
    citations = [fixture(text, i + 1)[1] for i, text in enumerate(TEXTS)]
    client = Client(source_hook=lambda call: citations[0]['generation_evidence'].update(source_locator='replaced')
                    if call == 2 else None)
    result = bind_multi_source_answer(QUESTION, citations, client)
    assert result['reason'] == 'multi_source_snapshot_changed' and 'answer_value' not in result


@pytest.mark.parametrize('target', ['offsets', 'part_ids', 'inventory', 'review', 'contract', 'answer_hash'])
def test_rehashed_proof_tamper_never_replays(target):
    result, citations, _ = run()
    proof = result['answer_proof']
    if target == 'offsets':
        proof['fragments'][0]['literal']['offsets'][0] += 1
    elif target == 'part_ids':
        proof['fragments'][0]['part_ids'] = [2]
    elif target == 'inventory':
        proof['matching_inventory'][0]['fragment_ids'] = [2]
    elif target == 'review':
        proof['semantic_review']['no_evidence_conflict'] = False
    elif target == 'contract':
        proof['question_contract']['requested_parts'][0] = 'Different subject'
    else:
        proof['answer_sha256'] = 'b' * 64
    result['saved_proof_sha256'] = _sha(proof)
    assert not replay_multi_source_proof(QUESTION, result, citations)


@pytest.mark.parametrize('target', ['answer', 'source', 'question', 'payload', 'coverage'])
def test_changed_output_source_question_or_contract_never_replays(target):
    result, citations, _ = run()
    question = QUESTION
    if target == 'answer':
        result['answer_value'] = 'Invented summary'
    elif target == 'source':
        citations[0]['generation_evidence']['source_sha256'] = 'b' * 64
    elif target == 'question':
        question = QUESTION.replace('X', 'Y')
    elif target == 'payload':
        citations[0]['generation_evidence']['extra_future_field'] = 'changed'
    else:
        result['coverage_scope'] = 'entire_document'
    assert not replay_multi_source_proof(question, result, citations)


def test_reviewer_cannot_replace_literal_match_with_candidate_scope():
    def scope_only(review):
        review['matched_items'][0]['answer_span'] = TEXTS[0]
    result, _, _ = run(Client(review_hook=scope_only))
    assert result['reason'] == 'multi_inventory_item_missing_from_answer'
