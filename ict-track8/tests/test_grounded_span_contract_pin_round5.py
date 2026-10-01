"""No API: full native contract pins from freshly constructed PDF fixtures.

Local mock approvals exercise binding/replay only, not semantic accuracy.
"""
from copy import deepcopy

import fitz
import pytest

from backend.answer_contract import native_source_only_contract_valid
from backend.grounded_span_answer import (
    _sha, bind_grounded_span_answer, replay_literal_span_proof,
)
from backend.pdf_native_context import extract_native_page_context
from tests.test_grounded_span_answer import Client
from tests.test_typed_span_execution import QUESTION, ThresholdClient


def caption_pdf(*, threshold=False):
    entries = [(80, 80, 'Table 7. Annual service records'),
               (80, 100, 'Service'), (240, 100, 'Budget'), (350, 100, 'Actual')]
    for index in range(3):
        label = 'Defect rate <= 30%.' if threshold and index == 0 else f'Service-{index + 1}'
        entries.extend([(80, 110 + 28 * index, label),
                        (240, 110 + 28 * index, str(31 + index)),
                        (350, 110 + 28 * index, str(42 + index))])
    with fitz.open() as document:
        page = document.new_page(width=600, height=700)
        for x, y, text in entries:
            page.insert_text((x, y), text, fontsize=10)
        return document.tobytes()


@pytest.fixture(scope='module')
def native_templates():
    templates = {}
    for kind in ('literal', 'threshold'):
        native = extract_native_page_context(caption_pdf(threshold=kind == 'threshold'),
            1, 'Annual service records', max_chars=3200)
        assert native['mode'] == 'original_native_complete_captioned_table_region'
        assert native['captioned_native_region']['semantic_row_column_binding_verified'] is False
        text = native['text']
        source = {'text': text, 'source_sha256': native['source_sha256'],
            'evidence_sha256': native['evidence_sha256'], 'source_locator': 'page:1:block:0',
            'page_no': 1, 'native_context': native, 'native_context_max_chars': 3200,
            'mode': 'authoritative_pdf_native_block_context', 'offset_start': None,
            'offset_end': None, 'truncated': False, 'original_chars': len(text),
            'evidence_chars': len(text)}
        citation = {'citation_id': 1, 'metadata': {'document_id': 'fresh-caption-pdf',
            'source_sha256': native['source_sha256']}, 'generation_evidence': source}
        claim = {'text': text, 'support': [{'citation_id': 1, 'quote': text}]}
        if kind == 'threshold':
            # Make the literal inequality an explicit catalog entry while
            # retaining the complete PDF region as the validated claim.
            claim['support'].append({'citation_id': 1, 'quote': 'Defect rate <= 30%.'})
        templates[kind] = (claim, citation)
    return templates


@pytest.fixture(params=['literal', 'threshold'])
def caption_case(request, native_templates):
    claim, citation = deepcopy(native_templates[request.param])
    question = QUESTION if request.param == 'threshold' else 'What is the Actual value for Service-2?'
    return request.param, question, claim, citation


def bind_case(case, *, mutate=None):
    kind, question, claim, citation = case
    if kind == 'threshold':
        client = ThresholdClient(mutate=mutate)
        client.threshold_quote = 'Defect rate <= 30%.'
    else:
        client = Client(mutate=mutate, candidate={'abstain': False, 'citation_id': 1,
            'answer_span': '43', 'answer_context': 'Service-2 32 43', 'answer_type': 'quantity',
            'scope': [{'citation_id': 1, 'quote': claim['text']}]})
    return bind_grounded_span_answer(question, [claim], [citation], client), client


def change_field(source, path, value):
    current = source
    for part in path[:-1]:
        current = current[part]
    current[path[-1]] = deepcopy(value)


# Changes leave the quoted source text and its hashes untouched. Several
# contracts remain locally valid, so rejection must also pin provenance.
CONTRACT_MUTATIONS = [
    pytest.param(('native_context', 'captioned_native_region',
                  'semantic_row_column_binding_verified'), True, id='caption-semantic-flag'),
    pytest.param(('native_context', 'captioned_native_region',
                  'horizontal_bounds_unrotated_pt'), [41.0, 420.0], id='caption-bounds'),
    pytest.param(('native_context', 'native_row_layout', 0, 0,
                  'bbox_fitz_unrotated_pt'), [0, 0, 1, 1], id='row-layout-box'),
    pytest.param(('native_context', 'native_row_layout', 0, 0,
                  'text_sha256'), 'b' * 64, id='row-layout-hash'),
    pytest.param(('native_context', 'members', 0, 'lines', 0,
                  'native_direction'), [0.0, -1.0], id='member-direction'),
    pytest.param(('native_context', 'members', 0, 'bbox_fitz_unrotated_pt'),
                 [0, 0, 1, 1], id='member-box'),
    pytest.param(('native_context', 'reading_order', 'block_ids'), [3, 2, 1, 0], id='reading-order'),
    pytest.param(('native_context', 'anchor_match_policy'), 'invented-policy', id='anchor-policy'),
    pytest.param(('native_context', 'unit_insertions'), ['USD'], id='unit-insertion'),
    pytest.param(('native_context', 'column_transition'), {'verified': True}, id='column-transition'),
    pytest.param(('native_context', 'calculator_input_eligible'), True, id='calculator-eligibility'),
    pytest.param(('native_context', 'mode'), 'original_native_complete_table_region', id='native-mode'),
    pytest.param(('native_context', 'extraction_version'),
                 'original-native-bounded-page-region-v1', id='extraction-version'),
    pytest.param(('native_context', 'max_chars'), 3199, id='native-budget'),
    pytest.param(('native_context_max_chars',), 3199, id='generation-budget'),
    pytest.param(('mode',), 'authoritative_hit_chunk_complete_prefix', id='generation-mode'),
    pytest.param(('offset_start',), 0, id='generation-offset'),
    pytest.param(('truncated',), True, id='generation-truncation'),
    pytest.param(('evidence_chars',), 999, id='generation-char-count'),
    pytest.param(('native_context',), None, id='native-contract-removed'),
    pytest.param(('native_context', 'future_provenance'),
                 {'nested': {'semantic_binding_verified': True}}, id='future-nested-field'),
]


def test_complete_native_payload_is_pinned_and_unchanged_replay_succeeds(caption_case):
    kind, question, claim, citation = caption_case
    result, client = bind_case(caption_case)
    assert result['status'] == 'model_reviewed', result
    assert result['answer_value'] == ('Yes' if kind == 'threshold' else '43')
    assert replay_literal_span_proof(question, result, [citation])
    snapshot = result['answer_proof']['source_snapshots'][0]
    assert snapshot['generation_payload_sha256'] == _sha(citation['generation_evidence'])
    assert result['claims'] == [claim]
    assert result['calculator_input_eligible'] is False
    assert result['answer_proof']['calculator_input_eligible'] is False
    assert citation['generation_evidence']['native_context']['calculator_input_eligible'] is False
    assert len(client.calls) == 2


@pytest.mark.parametrize('path,value', CONTRACT_MUTATIONS)
def test_independent_replay_rejects_full_contract_mutation_without_model_call(caption_case, path, value):
    _, question, _, citation = caption_case
    result, client = bind_case(caption_case)
    assert result['status'] == 'model_reviewed'
    source = citation['generation_evidence']
    before = tuple(source[key] for key in ('text', 'source_sha256', 'evidence_sha256'))
    change_field(source, path, value)
    assert tuple(source[key] for key in ('text', 'source_sha256', 'evidence_sha256')) == before
    assert not replay_literal_span_proof(question, result, [citation])
    assert len(client.calls) == 2


@pytest.mark.parametrize('step', [1, 2], ids=['selection', 'review'])
@pytest.mark.parametrize('path,value', [CONTRACT_MUTATIONS[index] for index in (0, 2, 6, 14, 20)])
def test_contract_only_mutation_during_binding_refuses_after_mock_approval(caption_case, step, path, value):
    _, _, _, citation = caption_case
    source = citation['generation_evidence']
    before = tuple(source[key] for key in ('text', 'source_sha256', 'evidence_sha256'))

    def mutate(call):
        if call == step:
            change_field(source, path, value)

    result, client = bind_case(caption_case, mutate=mutate)
    assert tuple(source[key] for key in ('text', 'source_sha256', 'evidence_sha256')) == before
    assert result['status'] == 'unsupported' and result['reason'] == 'source_snapshot_changed'
    assert 'answer_value' not in result and result['calculator_input_eligible'] is False
    assert len(client.calls) == 2


def test_old_proof_without_complete_payload_pin_refuses_even_if_outer_hash_is_recomputed(caption_case):
    _, question, _, citation = caption_case
    result, _ = bind_case(caption_case)
    assert result['status'] == 'model_reviewed'
    del result['answer_proof']['source_snapshots'][0]['generation_payload_sha256']
    result['saved_proof_sha256'] = _sha(result['answer_proof'])
    assert not replay_literal_span_proof(question, result, [citation])


def test_existing_native_source_only_calculator_guard_still_refuses_before_model(caption_case):
    caption_case[3]['generation_evidence']['native_context']['calculator_input_eligible'] = True
    result, client = bind_case(caption_case)
    assert result['status'] == 'unsupported'
    assert result['reason'] == 'complete_claim_or_authoritative_evidence_invalid'
    assert result['calculator_input_eligible'] is False and client.calls == []


def test_caption_semantic_flag_mutation_matches_original_store_reconstruction_rejection(tmp_path):
    from backend.knowledge_store import KnowledgeStore, SourceRevisionError

    store = KnowledgeStore(tmp_path)
    store.ingest(caption_pdf(), document_id='fresh-caption', title='Annual service records',
                 modality='pdf', filename='fresh-caption.pdf', language='eng')
    hits = store.search('Annual service records', top_k=4)
    citations, _ = store._generation_citations([
        {'citation_id': index, **hit.to_dict()} for index, hit in enumerate(hits, 1)])
    citation = next(item for item in citations if item['generation_evidence'].get('native_context',
        {}).get('mode') == 'original_native_complete_captioned_table_region')
    store._verify_generation_chunks([citation])
    text = citation['generation_evidence']['text']
    claim = {'text': text, 'support': [{'citation_id': citation['citation_id'], 'quote': text}]}
    question = 'What is the Actual value for Service-2?'
    client = Client(candidate={'abstain': False, 'citation_id': citation['citation_id'],
        'answer_span': '43', 'answer_context': 'Service-2 32 43', 'answer_type': 'quantity',
        'scope': [{'citation_id': citation['citation_id'], 'quote': text}]})
    result = bind_grounded_span_answer(question, [claim], [citation], client)
    assert result['status'] == 'model_reviewed'
    assert replay_literal_span_proof(question, result, [citation])
    native = citation['generation_evidence']['native_context']
    native['captioned_native_region']['semantic_row_column_binding_verified'] = True
    # This is still locally admissible; complete pinning closes the replay gap.
    assert native_source_only_contract_valid(native)
    assert not replay_literal_span_proof(question, result, [citation])
    with pytest.raises(SourceRevisionError):
        store._verify_generation_chunks([citation])
