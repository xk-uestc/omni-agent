"""Source quote IDs remove transcription errors without relaxing literal proof."""
from copy import deepcopy
import hashlib

import pytest

from backend.grounded_span_answer import bind_grounded_span_answer, replay_literal_span_proof


def citation(text, cid=1):
    return {'citation_id': cid, 'metadata': {'document_id': 'original', 'source_sha256': 'a' * 64},
            'generation_evidence': {'text': text, 'source_sha256': 'a' * 64,
                'evidence_sha256': hashlib.sha256(text.encode()).hexdigest(),
                'source_locator': f'page:1:block:{cid}', 'page_no': 1}}


def claim(text, cid=1):
    return {'text': text, 'support': [{'citation_id': cid, 'quote': text}]}


def approved():
    return {'approved': True, 'reason_code': 'supported_unique_complete_scope',
            'all_question_constraints_bound': True, 'unique_answer': True,
            'literal_units_and_signs_preserved': True, 'negation_and_modality_preserved': True,
            'no_evidence_conflict': True}


class CatalogClient:
    model, reasoning = 'gpt-6-luna', 'medium'

    def __init__(self, answer, context_quote, scope_quotes, answer_type='entity',
                 transform=None, review=None, mutate=None):
        self.answer, self.context_quote, self.scope_quotes = answer, context_quote, scope_quotes
        self.answer_type, self.transform = answer_type, transform
        self.review = approved() if review is None else review
        self.mutate, self.calls, self.audit = mutate, [], {}

    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append((deepcopy(context), deepcopy(schema)))
        self.audit = {'model': self.model, 'reasoning': self.reasoning, 'status': 'completed',
                      'http_status': 200, 'model_verified': True, 'response_model': self.model,
                      'operation': kwargs['name'], 'call_index': len(self.calls)}
        if self.mutate:
            self.mutate(len(self.calls))
        if len(self.calls) == 2:
            return deepcopy(self.review)
        registry = context['quote_catalog']

        def resolve(cid, quote):
            return next(item['quote_id'] for item in registry
                        if item['citation_id'] == cid and item['quote'] == quote)

        output = {'abstain': False, 'answer_span': self.answer,
                  'answer_context_id': resolve(*self.context_quote), 'answer_type': self.answer_type,
                  'scope_ids': [resolve(*quote) for quote in self.scope_quotes]}
        if self.transform:
            self.transform(output, registry)
        return output


def test_joint_entities_and_multiline_scope_resolve_original_quotes_and_replay():
    source = ('An emergency conference was held by the Regional Court.\n\n'
              'Also appearing were Mira Chen and Leo Park\non behalf of Defendant Acme Inc.')
    fact = 'Also appearing were Mira Chen and Leo Park\non behalf of Defendant Acme Inc.'
    question = 'Who represented Acme Inc. in the emergency conference?'
    client = CatalogClient('Mira Chen and Leo Park', (1, fact), [(1, source)])
    result = bind_grounded_span_answer(question, [claim(fact)], [citation(source)], client)
    assert result['status'] == 'model_reviewed', result
    assert result['answer_value'] == 'Mira Chen and Leo Park'
    assert result['answer_scope'][0]['quote'] == source
    assert result['answer_proof']['literal']['answer_context'] == fact
    assert replay_literal_span_proof(question, result, [citation(source)])
    selection, schema = client.calls[0]
    assert set(schema['properties']) == {'abstain', 'answer_span', 'answer_context_id',
                                         'answer_type', 'scope_ids'}
    for entry in selection['quote_catalog']:
        assert selection['evidence'][0]['text'].count(entry['quote']) == 1
        assert entry['quote_id'] in schema['properties']['scope_ids']['items']['enum']
    review = client.calls[1][0]
    assert review['evidence'] == selection['evidence']
    assert review['complete_validated_facts'] == [claim(fact)]
    assert 'quote_catalog' not in review
    assert review['candidate']['scope'][0]['quote'] == source


def test_identifier_repeated_in_dates_can_use_source_generated_local_anchor():
    source = '02/07/2040 7 ANSWER to Complaint. (Entered: 02/07/2040)'
    client = CatalogClient('7', (1, '7 ANSWER to'), [(1, source)], 'identifier')
    question = 'What is the docket number for the ANSWER to Complaint on February 7, 2040?'
    result = bind_grounded_span_answer(question, [claim(source)], [citation(source)], client)
    assert result['status'] == 'model_reviewed', result
    assert result['answer_value'] == '7'
    assert result['answer_proof']['literal']['offsets'] == [11, 12]
    assert replay_literal_span_proof(question, result, [citation(source)])


def test_shared_answer_with_explicit_forecast_scope_retains_all_original_qualifiers():
    source = 'Forecast for approved orders only.\nRevenue: -$180.50 USD.'
    client = CatalogClient('-$180.50 USD', (1, 'Revenue: -$180.50 USD.'), [(1, source)], 'quantity')
    result = bind_grounded_span_answer('What is the forecast revenue for approved orders?',
                                      [claim(source)], [citation(source)], client)
    assert result['status'] == 'model_reviewed', result
    assert result['answer_scope'][0]['quote'] == source
    assert result['calculator_input_eligible'] is False


@pytest.mark.parametrize('change,code', [
    (lambda item, registry: item.update(answer_context_id='Q999999'), 'catalog_context_invalid'),
    (lambda item, registry: item.update(scope_ids=['Q999999']), 'catalog_scope_invalid'),
    (lambda item, registry: item.update(scope_ids=[item['answer_context_id']] * 2), 'selection_contract_invalid'),
    (lambda item, registry: item.update(answer_context_id=1), 'catalog_context_invalid'),
    (lambda item, registry: item.update(scope_ids=[1]), 'selection_contract_invalid'),
    (lambda item, registry: item.update(answer_context='invented quote'), 'selection_contract_invalid'),
    (lambda item, registry: item.update(abstain=True), 'selection_abstained'),
])
def test_bad_id_contract_refuses_without_review_and_exposes_only_static_code(change, code):
    source = 'Revenue: -$180.50 USD.'
    client = CatalogClient('-$180.50 USD', (1, source), [(1, source)], 'quantity', transform=change)
    result = bind_grounded_span_answer('What is revenue?', [claim(source)], [citation(source)], client)
    assert result['status'] == 'unsupported' and len(client.calls) == 1
    assert result['literal_error_code'] == code
    assert 'Q999999' not in str(result) and 'invented quote' not in str(result)


def test_uncovered_source_context_cannot_be_selected_even_when_catalogued():
    source = 'Revenue: 180 USD. Costs: 90 USD.'
    fact = 'Revenue: 180 USD.'
    client = CatalogClient('90 USD', (1, 'Costs: 90 USD.'), [(1, source)], 'quantity')
    result = bind_grounded_span_answer('What are costs?', [claim(fact)], [citation(source)], client)
    assert result['status'] == 'unsupported' and len(client.calls) == 1
    assert result['literal_error_code'] == 'catalog_context_invalid'


@pytest.mark.parametrize('answer', ['180.50 USD', '$180.50 USD', '-$180.5 USD'])
def test_catalog_context_cannot_authorize_removed_sign_currency_or_partial_decimal(answer):
    source = 'Revenue: -$180.50 USD.'
    client = CatalogClient(answer, (1, source), [(1, source)], 'quantity')
    result = bind_grounded_span_answer('What is revenue?', [claim(source)], [citation(source)], client)
    assert result['status'] == 'unsupported' and len(client.calls) == 1


def test_model_flattened_answer_is_still_rejected_despite_safe_context_id():
    source = 'Delegates: Mira Chen and\nLeo Park.'
    client = CatalogClient('Mira Chen and Leo Park', (1, source), [(1, source)])
    result = bind_grounded_span_answer('Who are the delegates?', [claim(source)], [citation(source)], client)
    assert result['status'] == 'unsupported' and len(client.calls) == 1
    assert result['literal_error_code'] == 'answer_span_missing_or_ambiguous'


def test_catalog_does_not_turn_location_anchors_into_complete_scope():
    source = 'Applicable only to approved orders. Revenue: 180 USD.'
    review = approved()
    review.update(approved=False, all_question_constraints_bound=False, reason_code='unbound_question_scope')
    client = CatalogClient('180 USD', (1, 'Revenue: 180 USD.'), [], 'quantity', review=review)
    result = bind_grounded_span_answer('What is revenue?', [claim(source)], [citation(source)], client)
    assert result['status'] == 'unsupported' and len(client.calls) == 2
    assert result['reason'] == 'semantic_review_rejected_unbound_question_scope'
    assert client.calls[1][0]['evidence'][0]['text'] == source


def test_independent_review_has_competing_citation_not_just_selected_catalog_quote():
    source, conflict = 'Revenue: 180 USD.', 'Revenue: 90 USD.'
    review = approved()
    review.update(approved=False, no_evidence_conflict=False, reason_code='ambiguous_or_conflicting_evidence')
    client = CatalogClient('180 USD', (1, source), [(1, source)], 'quantity', review=review)
    result = bind_grounded_span_answer('What is revenue?', [claim(source)],
                                      [citation(source), citation(conflict, 2)], client)
    assert result['status'] == 'unsupported'
    assert client.calls[1][0]['evidence'][1]['text'] == conflict


def test_catalog_never_selects_ambiguous_repeated_quote_as_unique_anchor():
    source = '7 ANSWER. 7 ANSWER.'
    client = CatalogClient('7', (1, source), [(1, source)], 'identifier')
    result = bind_grounded_span_answer('What is the number?', [claim(source)], [citation(source)], client)
    assert all(entry['quote'] != '7 ANSWER.' for entry in client.calls[0][0]['quote_catalog'])
    assert result['status'] == 'unsupported' and len(client.calls) == 1
    assert result['literal_error_code'] == 'answer_span_missing_or_ambiguous'


def test_source_mutation_during_review_fails_catalog_proof():
    source = 'Revenue: 180 USD.'
    original = citation(source)
    client = CatalogClient('180 USD', (1, source), [(1, source)], 'quantity',
        mutate=lambda step: original['generation_evidence'].update(source_sha256='b' * 64)
        if step == 2 else None)
    result = bind_grounded_span_answer('What is revenue?', [claim(source)], [original], client)
    assert result['status'] == 'unsupported' and result['reason'] == 'source_snapshot_changed'


@pytest.mark.parametrize('limit_name,limit', [('MAX_QUOTE_CATALOG_ITEMS', 1),
                                           ('MAX_QUOTE_CATALOG_JSON_CHARS', 10)])
def test_catalog_budget_refuses_before_model_without_truncating_evidence(monkeypatch, limit_name, limit):
    monkeypatch.setattr('backend.grounded_span_answer.' + limit_name, limit)
    source = 'Revenue: 180 USD. Costs: 90 USD.'
    original = citation(source)
    client = CatalogClient('180 USD', (1, source), [(1, source)], 'quantity')
    result = bind_grounded_span_answer('What is revenue?', [claim(source)], [original], client)
    assert result['status'] == 'unsupported' and not client.calls
    assert result['reason'] == 'quote_catalog_budget_or_contract'
    assert original['generation_evidence']['text'] == source
