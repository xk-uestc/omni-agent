"""Unseen literal relation fixtures and adversarial projection proof replay."""
from copy import deepcopy
import hashlib

import pytest

from backend.typed_answer import bind_answer_slot, replay_answer_proof


def fixture(text, identifier=1):
    citation = {'citation_id': identifier, 'generation_evidence': {
        'text': text, 'source_sha256': 'a' * 64,
        'evidence_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'source_locator': 'page:1:block:2', 'page_no': 1}}
    claim = {'text': text, 'support': [{'citation_id': identifier, 'quote': text}]}
    return claim, citation


@pytest.mark.parametrize('text,question,value,kind', [
    ('Finance director: Mira Chen.', 'Who is the finance director?', 'Mira Chen', 'entity'),
    ('Order number: A-17.', 'What number is the order number?', 'A-17', 'identifier'),
    ('Order number: 17.', 'What number is the order number?', '17', 'identifier'),
    ('Revenue: -125.50 USD.', 'What amount is revenue?', '-125.50 USD', 'quantity'),
    ('Demand: 211,000 tons.', 'What is demand?', '211,000 tons', 'quantity'),
    ('Alice and Bob represent North Company.', 'Which parties do Alice and Bob represent?', 'North Company', 'entity'),
    ('Alice on behalf of North Company.', 'Who represents North Company?', 'Alice', 'entity'),
])
def test_supported_projection_keeps_full_claim_and_replays(text, question, value, kind):
    claim, citation = fixture(text)
    result = bind_answer_slot(question, [claim], [citation])
    assert result['status'] == 'verified', result
    assert result['answer_value'] == value and result['answer_type'] == kind
    assert result['claims'] == [claim]
    assert replay_answer_proof(question, result, [citation])
    start, end = result['answer_proof']['value_range']
    assert text[start:end] in value


def test_year_entity_condition_forecast_and_unit_bind_and_remain_visible():
    text = 'Conditions: approved orders only. Year: 2025. Entity: East. Modality: forecast. Revenue: -125.50 USD.'
    claim, citation = fixture(text)
    q = 'For approved orders only, what amount is forecast revenue for East in 2025?'
    result = bind_answer_slot(q, [claim], [citation])
    assert result['status'] == 'verified', result
    assert result['answer_scope']['conditions'] == 'approved orders only'
    assert result['answer_scope']['year'] == '2025'
    assert result['answer_scope']['entity'] == 'East'
    assert result['answer_scope']['modality'] == 'forecast'
    for wrong in (q.replace('2025', '2024'), q.replace('East', 'West'),
                  q.replace('forecast', 'actual'), q.replace('approved orders only', 'all orders')):
        assert bind_answer_slot(wrong, [claim], [citation])['status'] != 'verified'


def test_declared_unit_is_projected_without_scaling_or_arithmetic():
    claim, citation = fixture('Units: USD. Revenue: -125.50.')
    result = bind_answer_slot('What amount is revenue?', [claim], [citation])
    assert result['status'] == 'verified' and result['answer_value'] == '-125.50 USD'
    assert result['answer_scope']['unit'] == 'USD'
    start, end = result['answer_proof']['unit_range']
    assert citation['generation_evidence']['text'][start:end] == 'USD'


def test_inline_unit_and_field_have_exact_original_spans():
    claim, citation = fixture('Revenue: -125.50 USD.')
    result = bind_answer_slot('What amount is revenue?', [claim], [citation])
    assert result['answer_scope']['unit'] == 'USD'
    for key, expected in [('unit_range', 'USD'), ('label_range', 'Revenue')]:
        start, end = result['answer_proof'][key]
        assert citation['generation_evidence']['text'][start:end] == expected


@pytest.mark.parametrize('text,question', [
    ('Revenue: 100 USD.', 'What amount is revenue excluding returns?'),
    ('Revenue: 100 USD.', 'What amount is revenue for East?'),
    ('Revenue: 100 USD.', 'What amount is revenue in 2025?'),
    ('Revenue: 100 USD.', 'What amount is actual revenue?'),
    ('Revenue: 100.', 'What amount is revenue?'),
    ('Finance director: not Mira Chen.', 'Who is the finance director?'),
    ('Revenue: 100 USD. Unless approval is complete.', 'What amount is revenue?'),
    ('Alice and Bob represent North Company.', 'Which parties does Alice represent?'),
    ('Alice represents North Company.', 'Who represents Alice?'),
    ('Revenue: 100 USD.', 'What is revenue plus five dollars?'),
    ('Alice does not represent North Company.', 'Who represents North Company?'),
    ('Alice represents not North Company.', 'Which parties does Alice represent?'),
    ('Exclusions: returned orders. Revenue: 100 USD.', 'What amount is revenue?'),
    ('Approval: denied. Revenue: 100 USD.', 'What amount is revenue?'),
    ('Units: USD. Revenue: 100 EUR.', 'What amount is revenue?'),
])
def test_unknown_scope_missing_units_polarity_direction_and_calculation_are_not_ignored(text, question):
    claim, citation = fixture(text)
    result = bind_answer_slot(question, [claim], [citation])
    assert result['status'] != 'verified'
    assert result['claims'] == [claim]


def test_conflicting_or_duplicate_role_candidates_do_not_pick_first():
    claims, citations = zip(*(fixture(text, i) for i, text in enumerate([
        'Finance director: Mira Chen.', 'Finance director: Noel Smith.'], 1)))
    assert bind_answer_slot('Who is finance director?', list(claims), list(citations))['status'] != 'verified'


def test_unclaimed_conflicting_source_or_unknown_relevant_qualifier_also_blocks_projection():
    claim, citation = fixture('Finance director: Mira Chen.')
    for other in ('Finance director: Noel Smith.', 'Finance director: Mira Chen.',
                  'Exclusions: temporary role. Finance director: Noel Smith.'):
        _, conflict = fixture(other, 2)
        assert bind_answer_slot('Who is finance director?', [claim], [citation, conflict])['status'] != 'verified'


@pytest.mark.parametrize('key', ['answer_value', 'answer_type', 'answer_scope', 'answer_proof', 'claims'])
def test_projection_or_proof_or_full_claim_tampering_fails_replay(key):
    claim, citation = fixture('Revenue: -125.50 USD.')
    result = bind_answer_slot('What amount is revenue?', [claim], [citation])
    altered = deepcopy(result)
    altered[key] = 'forged'
    assert not replay_answer_proof('What amount is revenue?', altered, [citation])


@pytest.mark.parametrize('key,value', [
    ('source_sha256', 'b' * 64), ('source_locator', 'page:9:block:9'),
    ('text', 'Revenue: 125.50 USD.'), ('evidence_sha256', 'b' * 64),
    ('page_no', 99),
])
def test_source_pin_locator_and_text_mutation_fail_replay(key, value):
    claim, citation = fixture('Revenue: -125.50 USD.')
    result = bind_answer_slot('What amount is revenue?', [claim], [citation])
    modified = deepcopy(citation)
    modified['generation_evidence'][key] = value
    assert not replay_answer_proof('What amount is revenue?', result, [modified])
    assert not replay_answer_proof('What amount is costs?', result, [citation])


def test_short_claim_cannot_bypass_full_fact_scope_guard_or_use_substring_role():
    full = 'Conditions: approved orders only. Revenue: 100 USD.'
    _, citation = fixture(full)
    short = {'text': 'Revenue: 100 USD', 'support': [{'citation_id': 1, 'quote': 'Revenue: 100 USD'}]}
    assert bind_answer_slot('What amount is revenue?', [short], [citation])['status'] != 'verified'
    claim, citation = fixture('Regional finance director: Mira Chen.')
    assert bind_answer_slot('Who is finance director?', [claim], [citation])['status'] != 'verified'


@pytest.mark.parametrize('key,value', [
    ('value_range', [0, 1]), ('relation_range', [0, 1]),
    ('scope_ranges', {'year': [0, 1]}), ('record_sha256', 'b' * 64),
    ('question_sha256', 'b' * 64), ('claims_sha256', 'b' * 64),
])
def test_specific_hash_and_span_forgery_fail_replay(key, value):
    claim, citation = fixture('Revenue: -125.50 USD.')
    result = bind_answer_slot('What amount is revenue?', [claim], [citation])
    result['answer_proof'][key] = value
    assert not replay_answer_proof('What amount is revenue?', result, [citation])


def test_authoritative_document_snapshot_mismatch_is_not_accepted():
    claim, citation = fixture('Revenue: -125.50 USD.')
    citation['metadata'] = {'source_sha256': 'b' * 64}
    assert bind_answer_slot('What amount is revenue?', [claim], [citation])['status'] != 'verified'
