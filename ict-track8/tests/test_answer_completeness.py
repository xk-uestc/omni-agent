"""Generic answer-shape and numbered-prose regressions, with no benchmark gold."""
from copy import deepcopy

import fitz
import pytest

from backend.answer_contract import (body_answer_affinity, literal_answer_shape_error,
    substantive_numbered_heading, whole_answer_shape_error)
from backend.grounded_generation import GroundedGenerator
from backend.grounded_span_answer import bind_grounded_span_answer, replay_literal_span_proof
from backend.grounded_span_answer import _snapshot
from backend.knowledge_store import KnowledgeStore
from backend.responses_client import GenerationError
from backend.source_span_answer import bind_source_span_answer, replay_source_span_proof
from backend.source_span_answer import _source_snapshot
from tests.test_grounded_generation import RepairClient, _citations, _claim
from tests.test_grounded_span_answer import fixture


def test_title_only_explanation_gets_fresh_supported_substantive_answer():
    title = 'Standard METHODS FOR EVALUATION OF QUALITY OF PEARS'
    purpose = 'This standard prescribes methods for estimating the quality of fresh pears.'
    source = title + '\n\n' + purpose
    client = RepairClient([_claim(title, title), _claim(purpose, purpose)])
    result = GroundedGenerator(client).answer('What is the purpose of the ' + title + '?', _citations(source))
    assert result['status'] == 'ok' and purpose in result['answer']
    assert result['generation_repaired'] is True
    assert client.calls[1]['context']['correction']['validation_error_category'] == 'incomplete_question_answer'
    assert client.calls[0]['context']['evidence'] == client.calls[1]['context']['evidence']
    assert 'claims' not in client.calls[1]['context']


def test_complete_signature_record_over_300_characters_is_a_literal_candidate():
    source = ('Northwind Labs. Clearview Services.\n'
        'By: /s/ Mira Chen By: /s/ Leo Park\n' + '-' * 60 + '\n'
        'Name: Mira Chen Name: Leo Park\n' + '-' * 60 + '\n'
        'Title: Vice Chair Title: President and CEO\n' + '-' * 60 + '\n'
        'Date: 04/11/2030 Date: 04/11/2030\n' + '-' * 60)
    assert 300 < len(source) <= 600
    evidence = [{'citation_id': 1, 'text': source}]
    choices = GroundedGenerator.verbatim_candidates('What are the roles of Mira Chen and Leo Park?', evidence)
    assert any(claim['text'] == source for claim in choices)
    assert GroundedGenerator.validate(_claim(source, source), {1: source})
    swapped = source.replace('Mira Chen', 'Noah Lee')
    with pytest.raises(GenerationError):
        GroundedGenerator.validate(_claim(swapped, source), {1: source})


def test_unrepaired_subject_title_retains_its_excerpt_without_claiming_complete_answer(tmp_path):
    title = 'Standard METHODS FOR EVALUATION OF QUALITY OF PEARS'
    client = RepairClient([_claim(title, title), _claim(title, title)])
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    store.ingest(title.encode(), document_id='standard', title='Quality standard', modality='txt', filename='standard.txt')
    result = store.answer('What is the purpose of the ' + title + '?')
    assert result['status'] == 'insufficient_evidence'
    assert result['answer_mode'] == 'extractive_fallback'
    assert title in result['answer'] and result['citations']
    assert result['answer_completeness'] == 'subject_only_answer_not_complete'
    assert len(client.calls) == 2


class ShapeClient:
    model, reasoning = 'gpt-6-luna', 'medium'

    def __init__(self, first, corrected, fail_repair=False):
        self.first, self.corrected, self.fail_repair = first, corrected, fail_repair
        self.calls, self.audit = [], {}

    def generate(self, instructions, context, schema, **kwargs):
        name = kwargs['name']
        self.calls.append((name, deepcopy(context)))
        self.audit = {'model': self.model, 'reasoning': self.reasoning, 'status': 'completed',
            'http_status': 200, 'model_verified': True, 'response_model': self.model,
            'operation': name, 'call_index': len(self.calls)}
        if name.endswith('_correction') and self.fail_repair:
            self.audit['status'] = 'failed'
            raise GenerationError('private provider payload')
        if 'selection' in name:
            return deepcopy(self.corrected if name.endswith('_correction') else self.first)
        result = {key: True for key in schema['properties']}
        result['reason_code'] = 'supported_unique_complete_scope'
        return result


@pytest.mark.parametrize('source,question,kind,initial,answer', [
    ('Mira Chen 1971 59 Westport Research Director',
        'Which person born in 1971 is the Research Director?', 'entity',
        'Mira Chen 1971 59 Westport Research Director', 'Mira Chen'),
    ('SUBTOTAL Other -$150 USD', 'What is the total cost?', 'quantity',
        'SUBTOTAL Other -$150 USD', '-$150 USD'),
    ('The category percentages are 60 percent free and 40 percent reserve.',
        'What are the free and reserve percentages?', 'quantity',
        'The category percentages are 60 percent free and 40 percent reserve.',
        '60 percent free and 40 percent reserve'),
])
@pytest.mark.parametrize('source_lane', [False, True])
def test_obvious_row_or_framing_leakage_is_reselected_then_independently_reviewed(
        source, question, kind, initial, answer, source_lane):
    claim, citation = fixture(source)
    first = {'abstain': False, 'citation_id': 1, 'answer_span': initial,
        'answer_context': source, 'answer_type': kind, 'scope': [{'citation_id': 1, 'quote': source}]}
    corrected = {**first, 'answer_span': answer}
    client = ShapeClient(first, corrected)
    result = (bind_source_span_answer(question, [citation], client) if source_lane
        else bind_grounded_span_answer(question, [claim], [citation], client))
    assert result['status'] == 'model_reviewed' and result['answer_value'] == answer
    assert len(result['model_audits']) == len(client.calls) == 3
    assert client.calls[1][0].endswith('_selection_correction')
    assert client.calls[0][1]['evidence'] == client.calls[1][1]['evidence'] == client.calls[2][1]['evidence']
    assert 'answer_span' not in client.calls[1][1]['correction']
    replay = replay_source_span_proof if source_lane else replay_literal_span_proof
    assert replay(question, result, [citation])
    if not source_lane:
        assert result['claims'] == [claim]


@pytest.mark.parametrize('source_lane', [False, True])
def test_shape_repair_provider_failure_never_retries_or_calls_review(source_lane):
    source = 'SUBTOTAL Other -$150 USD'
    claim, citation = fixture(source)
    candidate = {'abstain': False, 'citation_id': 1, 'answer_span': source,
        'answer_context': source, 'answer_type': 'quantity', 'scope': []}
    client = ShapeClient(candidate, candidate, fail_repair=True)
    result = (bind_source_span_answer('What is the cost?', [citation], client) if source_lane
        else bind_grounded_span_answer('What is the cost?', [claim], [citation], client))
    assert result['status'] == 'unsupported' and len(client.calls) == 2
    assert 'answer_value' not in result and 'private provider payload' not in str(result)


@pytest.mark.parametrize('source_lane', [False, True])
def test_repeated_bad_shape_stops_after_one_reselection_and_keeps_static_diagnostic(source_lane):
    source = 'SUBTOTAL Other -$150 USD'
    claim, citation = fixture(source)
    candidate = {'abstain': False, 'citation_id': 1, 'answer_span': source,
        'answer_context': source, 'answer_type': 'quantity', 'scope': []}
    client = ShapeClient(candidate, candidate)
    result = (bind_source_span_answer('What is the cost?', [citation], client) if source_lane
        else bind_grounded_span_answer('What is the cost?', [claim], [citation], client))
    assert result['status'] == 'unsupported' and len(client.calls) == 2
    assert result['literal_error_code'] == 'literal_answer_shape_invalid'
    assert 'answer_value' not in result


@pytest.mark.parametrize('span', ['within 14 days from this order', 'at most 35%', '-$150 USD',
    '3.52 on a scale of 4.0', '60 percent free and 40 percent reserve'])
def test_answer_defining_qualifiers_currency_and_multiple_values_are_preserved(span):
    assert literal_answer_shape_error('What is the value?', {'answer_span': span, 'answer_type': 'quantity'}) is None


def test_purpose_navigation_requires_topic_overlap_and_is_not_truth_validation():
    question = 'What is the purpose of the standard for quality of fresh pears?'
    body = 'This standard prescribes methods for estimating quality of fresh pears.'
    assert body_answer_affinity(question, body) > 1
    assert body_answer_affinity(question, 'The project aims to increase revenue.') == 1
    assert body_answer_affinity('What is the quality rating of fresh pears?', body) == 1
    assert substantive_numbered_heading('0.2 This standard has been formulated to prescribe methods')
    assert not substantive_numbered_heading('0.2 METHODS FOR QUALITY EVALUATION')


def test_numbered_normative_prose_reaches_native_pdf_evidence_and_fresh_replay(tmp_path):
    document = fitz.open()
    page = document.new_page()
    page.insert_text((45, 60), 'STANDARD METHODS FOR QUALITY OF PEARS')
    full = ('0.2 This standard has been formulated to prescribe test methods\n'
        'for estimating the quality of fresh pears.')
    page.insert_text((45, 110), full, fontsize=10)
    raw = document.tobytes()
    document.close()
    store = KnowledgeStore(tmp_path)
    store.ingest(raw, document_id='standard', title='Quality standard', modality='pdf', filename='standard.pdf')
    assert any(chunk['content_type'] == 'heading' and 'prescribe' in chunk['text']
        for chunk in store.document('standard')['chunks'])
    hits = store.search('What is the purpose of the STANDARD METHODS FOR QUALITY OF PEARS?')
    assert any('prescribe' in hit.snippet for hit in hits)
    citations = [{'citation_id': index, **hit.to_dict()} for index, hit in enumerate(hits, 1)]
    selected, omitted = store._generation_citations(citations)
    substantive = [hit for hit in selected if 'prescribe' in hit['generation_evidence']['text']]
    assert substantive and all('for estimating the quality of fresh pears.' in hit['generation_evidence']['text']
        for hit in substantive)
    assert all(hit['generation_evidence']['native_context'] for hit in substantive)
    store._verify_generation_chunks(selected)


@pytest.mark.parametrize('version,mode', [
    ('original-native-complete-block-context-v4', 'original_native_complete_block'),
    ('original-native-complete-block-context-v4', 'original_native_complete_continuation'),
    ('original-native-complete-block-context-v4', 'original_native_complete_rotated_row'),
    ('original-native-bounded-page-region-v2', 'original_native_complete_table_region'),
    ('original-native-bounded-page-region-v2', 'original_native_complete_paragraph_region'),
    ('original-native-bounded-page-region-v2', 'original_native_complete_captioned_table_region'),
])
def test_new_native_context_budget_is_source_only_and_cannot_upgrade_geometry_to_numeric_proof(version, mode):
    fact = 'Research director: Mira Chen.'
    text = 'Background information is recorded. ' * 53 + fact
    assert 1800 < len(text) < 3200
    _, citation = fixture(text)
    source = citation['generation_evidence']
    source['native_context_max_chars'] = 3200
    source['native_context'] = {'extraction_version': version, 'mode': mode,
        'max_chars': 3200, 'source_sha256': source['source_sha256'],
        'evidence_sha256': source['evidence_sha256'], 'page_no': source['page_no'],
        'anchor_match_policy': 'whitespace_and_printed_alphabetic_line_wrap_hyphen_only',
        'calculator_input_eligible': False,
        'rotated_native_row': {'semantic_row_column_binding_verified': False},
        'native_row_layout': [[{'bbox_fitz_unrotated_pt': [1, 2, 3, 4], 'text_sha256': 'b' * 64}]]}
    claim = {'text': fact, 'support': [{'citation_id': 1, 'quote': fact}]}
    assert _snapshot([claim], [citation])[0][1] == text
    assert _source_snapshot([citation])[0][1] == text
    for field in ('calculator_input_eligible', 'anchor_match_policy'):
        forged = deepcopy(citation)
        forged['generation_evidence']['native_context'][field] = True
        with pytest.raises(ValueError):
            _snapshot([claim], [forged])
        with pytest.raises(ValueError):
            _source_snapshot([forged])
    if mode == 'original_native_complete_rotated_row':
        source['native_context']['rotated_native_row']['semantic_row_column_binding_verified'] = True
        with pytest.raises(ValueError):
            _source_snapshot([citation])
    if mode == 'original_native_complete_captioned_table_region':
        source['native_context']['native_row_layout'] = []
        with pytest.raises(ValueError):
            _snapshot([claim], [citation])
