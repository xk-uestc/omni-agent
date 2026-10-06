"""Answer/evidence separation with complete original scopes and negative cases."""
from copy import deepcopy
from types import SimpleNamespace

import fitz
import pytest

from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.source_answer_dossier import (build_dossier, route, replay_context,
    validate_selection, validate_review, native_page, locate_quote, FLAGS)
from backend.document_parts_answer import _review_component


def pdf_pages(texts, rotation=0):
    with fitz.open() as doc:
        for text in texts:
            p = doc.new_page()
            p.insert_textbox(fitz.Rect(40, 40, 550, 740), text, fontsize=11)
            p.set_rotation(rotation)
        return doc.tobytes()


def setup(tmp_path, texts=None, rotation=0):
    texts = texts or ['Archive Lake report, FY 2046.\nAll sites except Old Lake are managed by '
        'Cedar Agency and Maple Office.\nOld Lake is managed by Local Council.']
    store = KnowledgeStore(tmp_path/'knowledge')
    raw = pdf_pages(texts, rotation)
    store.ingest(raw, document_id='archive', title='Archive', modality='pdf', filename='a.pdf')
    hits = [SimpleNamespace(metadata={'document_id': 'archive', 'page_no': 1})]
    return store, hits


class Client:
    supports_native_answer_dossier = True
    model = 'gpt-6-luna'
    reasoning = 'medium'
    audit = {}

    def __init__(self, mode='success', callback=None):
        self.mode, self.callback, self.calls = mode, callback, []

    def generate(self, instructions, packet, schema, **kwargs):
        self.calls.append((kwargs['name'], deepcopy(packet)))
        self.audit = {'status': 'completed', 'http_status': 200, 'model_verified': True,
            'model': self.model, 'reasoning': self.reasoning, 'response_model': self.model}
        if self.callback:
            self.callback(len(self.calls), packet)
        p = next(p for p in packet['original_pages'] if 'Cedar Agency' in p['text'])
        quote = 'All sites except Old Lake are managed by Cedar Agency and Maple Office.'
        # Preserve literal PDF wrapping for provenance validation.
        start = p['text'].index('All sites')
        end = p['text'].index('Office.', start)+len('Office.')
        quote = p['text'][start:end]
        if len(self.calls) == 1:
            if self.mode == 'abstain':
                return {'abstain': True, 'quotes': [], 'clauses': []}
            return {'abstain': False, 'quotes': [{'evidence_id': p['evidence_id'], 'quote': quote}],
                'clauses': [{'text': 'Cedar Agency and Maple Office', 'support_ids': [1]}]}
        result = {k: self.mode not in {'reject', 'missing_required_item'} for k in FLAGS}
        result['required_items'] = [{'evidence_id': p['evidence_id'], 'quote': quote, 'clause_ids': [1]}]
        if self.mode == 'missing_required_item':
            result = {k: True for k in FLAGS}
            result['required_items'] = [{'evidence_id': p['evidence_id'],
                'quote': 'Old Lake is managed by Local Council.', 'clause_ids': [1]}]
        return result


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_production_full_question_clause_support_and_original_replay(tmp_path, rotation):
    store, hits = setup(tmp_path, rotation=rotation)
    client = Client()
    store.generator = SimpleNamespace(client=client)
    question = 'Which agencies manage all sites except Old Lake in FY 2046?'
    result, trace = route(store, question, hits)
    assert trace['status'] == 'model_reviewed'
    assert result['answer'] == 'Cedar Agency and Maple Office'
    assert result['calculator_input_eligible'] is False
    assert all(packet['question'] == question for _, packet in client.calls)
    context = replay_context(store, result)
    assert context['original_pages'][0]['complete_native_page'] is True
    assert 'Local Council' in context['original_pages'][0]['text']
    assert result['citations'][0]['snippet'].startswith('All sites except Old Lake')
    component = _review_component(1, question, result, store=store)
    assert [p['complete_native_page_text'] for p in component['native_page_contexts'].values()] == [
        p['text'] for p in context['original_pages']]
    assert component['answer_clauses'] == context['answer_clauses']
    assert len(client.calls) == 2


@pytest.mark.parametrize('mode', ['abstain', 'reject', 'missing_required_item'])
def test_reject_missing_scope_or_independent_inventory_not_covered(tmp_path, mode):
    store, hits = setup(tmp_path)
    store.generator = SimpleNamespace(client=Client(mode))
    result, trace = route(store, 'Which agencies manage sites except Old Lake?', hits)
    assert result is None
    assert trace['status'] in {'dossier_selection_abstained_or_invalid',
        'dossier_semantic_review_rejected', 'dossier_required_item_not_supported_by_clause'}


@pytest.mark.parametrize('mutation', [
    lambda r: r.update(answer='Local Council'),
    lambda r: r.update(question='Who manages Old Lake?'),
    lambda r: r['native_dossier_proof']['review'].update(approved=False),
    lambda r: r['citations'][0].update(snippet='Other text'),
    lambda r: r['native_dossier_proof']['support_quotes'][0].update(offsets=[0, 1]),
    lambda r: r['native_dossier_proof']['matching_inventory'][0].update(clause_ids=[2]),
])
def test_saved_result_tampering_rejected_before_composition(tmp_path, mutation):
    store, hits = setup(tmp_path)
    store.generator = SimpleNamespace(client=Client())
    result, _ = route(store, 'Which agencies manage sites except Old Lake?', hits)
    mutation(result)
    with pytest.raises((ValueError, KeyError)):
        replay_context(store, result)


def test_actual_source_replacement_during_model_review_propagates(tmp_path):
    store, hits = setup(tmp_path)
    def replace(n, packet):
        if n == 2:
            store.ingest(pdf_pages(['Changed content and a different current department.']),
                document_id='archive', title='Archive', modality='pdf', filename='new.pdf')
    store.generator = SimpleNamespace(client=Client(callback=replace))
    with pytest.raises(SourceRevisionError):
        route(store, 'Which agencies manage sites except Old Lake?', hits)


def test_unquoted_competing_page_also_pinned_for_replay(tmp_path):
    store, hits = setup(tmp_path, texts=[
        'All sites except Old Lake are managed by Cedar Agency and Maple Office.\nOld Lake is managed by Local Council.',
        'FY 2046 review. This page records the full archive status for the current period.'])
    store.generator = SimpleNamespace(client=Client())
    result, _ = route(store, 'Which agencies manage sites except Old Lake?', hits)
    assert len(result['native_dossier_proof']['page_manifest']) == 2
    broken = deepcopy(result)
    broken['native_dossier_proof']['page_manifest'][1]['text_sha256'] = '0'*64
    with pytest.raises(ValueError, match='saved_page_changed'):
        replay_context(store, broken)


def test_full_document_navigation_finds_body_not_only_retrieved_heading(tmp_path):
    store, hits = setup(tmp_path, texts=[
        'The release supports guaranteed archive services. Its recorded amount is $13.7 billion.',
        'Support to archives for FY 2046. Heading for archive services and disclosure.',
        'Appendix archive list. The table index refers to archive services.'])
    pages, scope = build_dossier(store, 'How were archive services supported and what amount was recorded?', hits)
    assert any('recorded amount' in p['text'] for p in pages)
    assert scope['coverage'][0]['whole_document_included'] is True
    assert scope['ranking_is_navigation_only'] is True


def test_dossier_keeps_competing_retrieved_documents(tmp_path):
    store, hits = setup(tmp_path)
    store.ingest(pdf_pages(['In FY 2046 other agencies manage the archive sites. This is an alternate report.']),
        document_id='other', title='Other', modality='pdf', filename='o.pdf')
    hits.append(SimpleNamespace(metadata={'document_id': 'other', 'page_no': 1}))
    pages, scope = build_dossier(store, 'Which agencies manage the archive sites in FY 2046?', hits)
    assert {p['document_id'] for p in pages} == {'archive', 'other'}
    scoped, _ = build_dossier(store, 'Which agencies manage the sites?', hits, document_id='archive', page_no=1)
    assert {p['document_id'] for p in scoped} == {'archive'}


@pytest.mark.parametrize('answer', ['19', '13.70', '-13.7', '13.7%', '1.37e1', '13,700', '19kg'])
def test_composer_cannot_compute_or_reformat_numeric_values(answer):
    with fitz.open(stream=pdf_pages(['The recorded amount is 13.7 billion USD for FY 2046.']), filetype='pdf') as pdf:
        page = native_page({'document_id': 'a', 'title': 'A', 'sha256': 'a'*64}, pdf[0], 1, 1)
    page['evidence_id'] = 'P1'
    candidate = {'abstain': False, 'quotes': [{'evidence_id': 'P1', 'quote': '13.7 billion USD'}],
        'clauses': [{'text': answer, 'support_ids': [1]}]}
    with pytest.raises(ValueError, match='new_numeric_value'):
        validate_selection(candidate, [page])


def test_unsupported_adapter_does_not_change_existing_generator_contract(tmp_path):
    store, hits = setup(tmp_path)
    client = Client()
    client.supports_native_answer_dossier = False
    store.generator = SimpleNamespace(client=client)
    result, trace = route(store, 'Which agencies manage sites?', hits)
    assert result is None and trace['status'] == 'not_applicable' and not client.calls


def test_whitespace_quote_rebinds_to_literal_original_offsets(tmp_path):
    store, hits = setup(tmp_path)
    pages, _ = build_dossier(store, 'Which agencies manage sites?', hits)
    p = pages[0]
    quote = 'All sites except Old Lake are managed by Cedar Agency and Maple Office.'
    located = locate_quote({'evidence_id': p['evidence_id'], 'quote': quote}, pages)
    start, end = located['offsets']
    assert located['quote'] == p['text'][start:end]
    assert ' '.join(located['quote'].split()) == quote
    assert located['bbox_display_pt']


@pytest.mark.parametrize('quote', ['Cedar Agency & Maple Office', 'cedar Agency',
    'Cedar Agency and Maple Office!', 'Cedar Agency and Local Council'])
def test_whitespace_locator_never_repairs_content(tmp_path, quote):
    store, hits = setup(tmp_path)
    pages, _ = build_dossier(store, 'Which agencies manage sites?', hits)
    with pytest.raises(ValueError, match='not_unique_literal'):
        locate_quote({'evidence_id': pages[0]['evidence_id'], 'quote': quote}, pages)


@pytest.mark.parametrize('text,quote', [('Cedar Agency\nCedar Agency', 'Cedar Agency'),
    ('aaaa', 'aaa'), ('Cedar Agency and Cedar\nAgency', 'Cedar Agency')])
def test_ambiguous_and_overlapping_quotes_rejected(text, quote):
    with pytest.raises(ValueError, match='not_unique_literal'):
        locate_quote({'evidence_id': 'P1', 'quote': quote}, [{'evidence_id': 'P1', 'text': text}])


def test_noncomputed_component_rechecks_unquoted_pages_after_whole_review(tmp_path):
    from backend.document_parts_answer import _replay_computation
    store, hits = setup(tmp_path, texts=[
        'All sites except Old Lake are managed by Cedar Agency and Maple Office.',
        'FY 2046 review. This page records the full archive status for the current period.'])
    store.generator = SimpleNamespace(client=Client())
    result, _ = route(store, 'Which agencies manage sites except Old Lake?', hits)
    _replay_computation(store, result)
    result['native_dossier_proof']['page_manifest'][1]['text_sha256'] = '0'*64
    with pytest.raises(ValueError, match='saved_page_changed'):
        _replay_computation(store, result)


def test_prose_component_cannot_supply_an_arithmetic_computation(tmp_path):
    from backend.document_parts_answer import _replay_computation
    store, hits = setup(tmp_path)
    store.generator = SimpleNamespace(client=Client())
    result, _ = route(store, 'Which agencies manage sites except Old Lake?', hits)
    result['computation'] = {'operation': 'percentage', 'value': 75}
    with pytest.raises(ValueError, match='unsupported_component_computation'):
        _replay_computation(store, result)
