"""Native PDF scope evidence stays literal and independent refusals remain binding."""
from copy import deepcopy
from types import SimpleNamespace
import hashlib

import fitz
import pytest

from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.native_table_question import REVIEW, route_native_table_question
from backend.native_text_tables import extract_native_text_tables


def original(headings=('Regional operating cost allocation for 2030',), symbol='$'):
    with fitz.open() as document:
        page = document.new_page()
        for index, heading in enumerate(headings):
            page.insert_text((50, 40+index*18), heading)
        for index, (label, value) in enumerate((('North',4),('South',11),('Total',15))):
            page.insert_text((50, 100+index*25), label, fontsize=10)
            literal = symbol+str(value)
            page.insert_text((310-fitz.get_text_length(literal, fontsize=10), 100+index*25),
                             literal, fontsize=10)
        return document.tobytes()


class Client:
    model, reasoning = 'gpt-6-luna', 'medium'

    def __init__(self, *, approve=True, reverse=False, operation='difference', hook=None):
        self.audit = {}
        self.calls = []
        self.approve, self.reverse, self.operation, self.hook = approve, reverse, operation, hook

    def generate(self, instructions, context, response_schema, **options):
        self.calls.append((options['name'], deepcopy(context), instructions))
        self.audit = {'status':'completed','http_status':200,'model_verified':True,
                      'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
        if options['name'] == 'native_table_fact_selection':
            table = context['native_table_registry'][0]
            selected = {fact['row_label']:fact['selection_id'] for fact in table['facts']}
            ids = [selected['North'],selected['South']]
            return {'abstain':False,'operation':self.operation,
                    'fact_ids':list(reversed(ids)) if self.reverse else ids}
        if self.hook:
            self.hook()
        return {key:self.approve for key in REVIEW['properties']}


def setup(tmp_path, *, headings=('Regional operating cost allocation for 2030',), symbol='$', **options):
    raw = original(headings, symbol)
    client = Client(**options)
    store = KnowledgeStore(tmp_path/'knowledge', generator=SimpleNamespace(client=client))
    store.ingest(raw, document_id='cost', title='Cost allocation', modality='pdf', filename='cost.pdf')
    return raw, store, client, [SimpleNamespace(metadata={'document_id':'cost'})]


def query(store, hits, operation='difference'):
    question = ('Subtract South cost from North cost in 2030.' if operation == 'difference' else
                'What is the absolute difference between North cost and South cost in 2030?')
    return route_native_table_question(store, question, hits, document_id='cost')


@pytest.mark.parametrize('operation,answer,numeric', [
    ('difference','$-7','-7'), ('absolute_difference','$7','7'),
])
def test_explicit_original_title_and_computed_signed_display_are_reviewed(tmp_path, operation, answer, numeric):
    raw, store, client, hits = setup(tmp_path, operation=operation)
    result, trace = query(store, hits, operation)
    assert result is not None and result['answer'] == answer
    assert result['computation']['numeric_result'] == numeric
    assert result['computation']['operands'] == ['$4','$11']
    assert result['answer_scope']['period'] is None  # no invented column year
    assert result['answer_scope']['currency'] == 'unknown'
    assert result['answer_scope']['scale'] is None
    assert result['calculator_input_eligible'] is False
    assert all(citation['metadata']['source_sha256'] == hashlib.sha256(raw).hexdigest()
               for citation in result['citations'])
    operation_name, context, instructions = client.calls[1]
    assert operation_name == 'native_table_independent_scope_review'
    assert 'NOT evidence that the original page lacks an explicit period' in instructions
    assert 'Never automatically inherit' in instructions
    assert 'need not be printed verbatim' in instructions
    assert '$-x and -$x' in instructions
    assert context['annotation_display_contract'] == trace['annotation_display_contract']
    assert context['annotation_display_contract']['domain'] == (
        'computed_native_annotation_not_literal_source_quote')
    assert 'for 2030' in context['complete_table_page_contexts'][0]['complete_native_page_text']
    facts = context['all_native_table_candidates'][0]['facts']
    assert all(fact['period'] is None for fact in facts)
    assert any('2030' in part['text'] for fact in facts for part in fact['period_scope_text'])


@pytest.mark.parametrize('headings', [
    ('Regional operating cost allocation',),
    ('Regional operating cost allocation for 2031',),
    ('Regional operating cost allocation for 2030','Forecast cost allocation for 2031'),
])
def test_missing_wrong_or_competing_title_period_cannot_bypass_a_review_refusal(tmp_path, headings):
    _, store, client, hits = setup(tmp_path, headings=headings, approve=False)
    result, trace = query(store, hits)
    assert result is None and trace['status'] == 'native_table_semantic_scope_review_rejected'
    assert len(client.calls) == 2
    assert trace['semantic_review_checks']['all_entity_period_conditions_bound'] is False
    context = client.calls[1][1]
    assert all(fact['period'] is None for table in context['all_native_table_candidates'] for fact in table['facts'])
    page = context['complete_table_page_contexts'][0]['complete_native_page_text']
    assert all(heading in page for heading in headings)


def test_correct_title_does_not_override_an_independent_rejection(tmp_path):
    _, store, _, hits = setup(tmp_path, approve=False)
    result, trace = query(store, hits)
    assert result is None and trace['status'] == 'native_table_semantic_scope_review_rejected'
    assert trace['server_annotation_computation']['answer'] == '$-7'


def test_reversed_operands_are_still_rejected_by_independent_direction_review(tmp_path):
    _, store, client, hits = setup(tmp_path, approve=False, reverse=True)
    result, trace = query(store, hits)
    assert result is None and trace['status'] == 'native_table_semantic_scope_review_rejected'
    assert trace['server_annotation_computation']['operands'] == ['$11','$4']
    assert trace['server_annotation_computation']['numeric_result'] == '7'
    assert len(client.calls) == 2


def test_wire_scope_preserves_actual_original_manifest_fields_and_geometry(tmp_path):
    raw, store, client, hits = setup(tmp_path)
    result, _ = query(store, hits)
    assert result is not None
    manifest = extract_native_text_tables(raw, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest())
    for call in client.calls:
        key = 'native_table_registry' if call[0] == 'native_table_fact_selection' else 'all_native_table_candidates'
        registry = call[1][key][0]
        source_table = manifest['tables'][0]
        assert registry['bbox_display_pt'] == source_table['bbox_display_pt']
        for wire in registry['facts']:
            source = next(fact for fact in source_table['facts'] if fact['row_header'] == wire['row_label'])
            assert wire['period'] == source.get('period')
            assert wire['period_status'] == source.get('period_status')
            assert wire['period_scope_text'] == source.get('period_scope_text', [])
            assert wire['title_context'] == source.get('title_context', [])


def test_undeclared_currency_symbol_and_scale_are_not_filled_from_title(tmp_path):
    _, store, _, hits = setup(tmp_path, headings=('Regional operating costs in 2030; summary in millions',))
    result, _ = query(store, hits)
    assert result is not None
    assert result['answer'] == '$-7'
    assert result['answer_scope']['currency'] == 'unknown'
    assert result['answer_scope']['scale'] is None
    assert all(citation['metadata']['fact']['scale'] is None for citation in result['citations'])


def test_unlabelled_numeric_values_do_not_gain_currency_from_display_contract(tmp_path):
    _, store, _, hits = setup(tmp_path, symbol='')
    result, _ = query(store, hits)
    assert result is None or result['status'] != 'ok'


def test_source_replacement_after_scope_review_cannot_publish_old_period_evidence(tmp_path):
    _, store, client, hits = setup(tmp_path)
    client.hook = lambda: store.ingest(original(('Regional operating costs for 2031',)),
        document_id='cost', title='Replacement', modality='pdf', filename='cost.pdf')
    with pytest.raises(SourceRevisionError):
        query(store, hits)
