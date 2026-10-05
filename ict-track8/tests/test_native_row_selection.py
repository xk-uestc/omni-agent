"""Independent known PDF rows and adversarial inventory/offset/source cases."""
from copy import deepcopy
from types import SimpleNamespace
import fitz
import pytest
from backend.knowledge_store import KnowledgeStore, SourceIntegrityError
from backend.native_row_selection import (CHECKS, bind_selection, replay_selection,
    route_native_row_selection, requested_count, cardinality_context)
from backend.native_row_comparison import _sha
from backend.document_parts_answer import _review_component
from backend.evidence_recovery import recovery_eligible

QUESTION = 'Which items were handled by PERSON_A using ISO_A, and by whom?'


def source(*, changed=False, annotated=False):
    with fitz.open() as document:
        for pno in range(2):
            page = document.new_page(width=700, height=400)
            page.insert_text((30, 35), 'Record ID: CASE-7', fontsize=9)
            coordinates = [30, 230, 350, 470, 590]
            for x, label in zip(coordinates, ['Description', 'Result', 'When', 'Operator', 'Method']):
                page.insert_text((x, 70), label, fontsize=9)
            for i in range(3):
                index = pno * 3 + i
                cells = ['Item ' + chr(65 + index), '35 UG/L', '2025-01-01',
                    'PERSON_B' if index == 4 else 'PERSON_A', 'ISO_AB' if index == 2 else 'ISO_A']
                if changed and index == 0:
                    cells[0] = 'Changed item'
                if annotated and index == 0:
                    cells[0] = 'Item A *'
                    cells[1] = '* 35 UG/L'
                if annotated and index == 1:
                    cells[1] = '<200 UG/L'
                for x, cell in zip(coordinates, cells):
                    page.insert_text((x, 86 + i * 16), cell, fontsize=9)
        return document.tobytes()


class Client:
    model = 'gpt-6-luna'; reasoning = 'medium'
    def __init__(self):
        self.calls = []; self.edit_plan = None; self.edit_review = None; self.on_review = None
        self.review_context = None
    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append(kwargs['name'])
        assert all('members' not in p for r in context['original_registries'] for p in r['pages'])
        assert all('bbox_pt' not in f for r in context['original_registries'] for row in r['records'] for f in row['fields'])
        self.audit = {'status': 'completed', 'model_verified': True, 'http_status': 200,
            'model': self.model, 'response_model': self.model, 'reasoning': self.reasoning}
        if kwargs['name'] == 'native_row_selection_plan':
            rows = context['original_registries'][0]['records']
            assert len(rows) == 6
            # Independent expected inventory: A, B, D and F; C has ISO_AB,
            # E has PERSON_B. Do not compute this via the production filter.
            chosen = [rows[i] for i in (0, 1, 3, 5)]
            plan = {'abstain': False, 'document_id': 'sample', 'chain_index': 0, 'projection_mode': 'exact_spans',
                'predicates': [{'column_index': 4, 'operator': 'contains_token', 'literal': 'ISO_A'},
                    {'column_index': 3, 'operator': 'equals', 'literal': 'PERSON_A'}],
                'column_indices': [0, 3], 'fragments': [{'row_id': row['row_id'], 'column_index': i,
                    'quote': row['fields'][i]['text']}
                    for row in chosen for i in (0, 3)]}
            if self.edit_plan:
                self.edit_plan(plan)
            return plan
        assert kwargs['name'] == 'native_row_selection_independent_review'
        self.review_context = deepcopy(context)
        assert len(context['original_registries'][0]['pages']) == 2
        assert 'Item E' in context['original_registries'][0]['pages'][1]['text']
        assert 'ISO_AB' in context['original_registries'][0]['pages'][0]['text']
        review = {**{key: True for key in CHECKS}, 'matching_row_ids': [r['row_id'] for r in context['matched_rows']]}
        if self.edit_review:
            self.edit_review(review)
        if self.on_review:
            self.on_review()
        return review


def setup(tmp_path, **source_options):
    client = Client(); store = KnowledgeStore(tmp_path/'knowledge', generator=SimpleNamespace(client=client))
    store.ingest(source(**source_options), document_id='sample', title='Anonymous laboratory', modality='pdf', filename='records.pdf')
    return store, client, store.search(QUESTION, document_id='sample')


def answer(store, hits, question=QUESTION, **kwargs):
    return route_native_row_selection(store, question, hits, document_id='sample', **kwargs)[0]


def test_full_inventory_excludes_prefix_method_and_wrong_operator(tmp_path):
    store, client, hits = setup(tmp_path); result = answer(store, hits)
    assert result['status'] == 'ok'
    assert 'bbox_pt' in result['native_row_proof']['selected_rows'][0]['fields'][0]
    assert result['trace'][0]['model_context']['candidate_rows']==6
    assert result['answer'] == 'Item A\nItem B\nItem D\nItem F\nOperator: PERSON_A'
    assert result['answer_scope']['matching_row_count'] == 4
    assert [c['metadata']['page_no'] for c in result['citations']] == [1, 1, 2, 2]
    assert replay_selection(store, result)
    assert not recovery_eligible(result, client)
    component = _review_component(1, QUESTION, result, store=store)
    assert len(component['native_row_context']['original_registries'][0]['records']) == 6
    assert len(component['native_page_contexts']) == 2
    assert 'Item E' in list(component['native_page_contexts'].values())[1]['complete_native_page_text']
    assert [e.get('tool') for e in result['trace'][1:]] == ['document.table.read', 'document.table.filter']


def test_requested_two_is_a_clarification_not_arbitrary_subset(tmp_path):
    store, client, hits = setup(tmp_path)
    result = answer(store, hits, 'Which two items were handled by PERSON_A using ISO_A?')
    assert result['status'] == 'clarification'
    assert '4 records' in result['answer'] and 'requests 2' in result['answer']
    assert result['clarification_code'] == 'document_native_row_count_ambiguous'
    assert len(result['citations']) == 4 and replay_selection(store, result)
    client.audit_history = [deepcopy(client.audit)]
    assert not recovery_eligible(result, client)


def test_cardinality_conflict_is_separate_and_preserves_full_review_input(tmp_path):
    store, client, hits = setup(tmp_path)
    result = answer(store, hits, 'Which two items were handled by PERSON_A using ISO_A?')
    context = client.review_context
    assert context['cardinality'] == {'requested_count':2,'complete_matching_count':4,
        'count_conflict':True,'required_status':'clarification','subset_permitted':False}
    assert len(context['matched_rows']) == 4
    assert len(context['original_registries'][0]['records']) == 6
    assert context['selection']['fragments'] == []
    assert result['native_row_proof']['review']['requested_cardinality_handled_without_subset'] is True
    assert replay_selection(store, result)


@pytest.mark.parametrize('flag', ['requested_cardinality_handled_without_subset',
    'filters_and_projection_match_original_question', 'all_matching_rows_in_supplied_sources_covered'])
def test_count_conflict_never_overrides_any_independent_rejection(tmp_path, flag):
    store, client, hits = setup(tmp_path)
    client.edit_review = lambda review:review.update({flag:False})
    result = answer(store, hits, 'Which two items were handled by PERSON_A using ISO_A?')
    assert result['answer_mode'] == 'native_row_selection_requires_scope'
    assert result['citations'] == [] and 'native_row_proof' not in result


def test_missing_cardinality_check_or_old_receipt_cannot_replay(tmp_path):
    store, client, hits = setup(tmp_path)
    result = answer(store, hits)
    result['native_row_proof']['review'].pop('requested_cardinality_handled_without_subset')
    result['native_row_proof_sha256'] = _sha(result['native_row_proof'])
    assert not replay_selection(store, result)
    for version in ('native-row-selection-independent-review-v2', 'native-row-selection-independent-review-v3', 'native-row-selection-independent-review-v4'):
        result = answer(store, hits)
        result['native_row_proof']['version'] = version
        result['native_row_proof_sha256'] = _sha(result['native_row_proof'])
        assert not replay_selection(store, result)


def test_unrequested_and_matching_counts_never_create_a_conflict():
    rows = [object(),object()]
    assert cardinality_context('Which items match?', rows)['required_status'] == 'ok'
    assert cardinality_context('Which two items match?', rows)['required_status'] == 'ok'
    assert cardinality_context('Which three items match?', rows)['required_status'] == 'clarification'


def test_explicit_whole_fields_enumerates_full_inventory_and_replays(tmp_path):
    store, client, hits = setup(tmp_path, annotated=True)
    client.edit_plan = lambda p:p.update(projection_mode='whole_fields',fragments=[])
    result = answer(store, hits)
    assert result['answer'] == 'Item A *\nItem B\nItem D\nItem F\nOperator: PERSON_A'
    assert len(result['native_row_proof']['projection']) == 4
    assert result['native_row_proof']['selection']['fragments'] == []
    assert client.review_context['selection']['projection_mode'] == 'whole_fields'
    assert replay_selection(store, result)


@pytest.mark.parametrize('edit', [
    lambda p:p.update(projection_mode='whole_fields'),
    lambda p:p.update(projection_mode='exact_spans',fragments=[]),
    lambda p:p.update(projection_mode='implicit'),
    lambda p:p.pop('projection_mode'),
    lambda p:p.update(projection_mode='whole_fields',fragments=[],column_indices=[99]),
])
def test_projection_modes_never_rescue_invalid_or_incomplete_plans(tmp_path, edit):
    store, client, hits = setup(tmp_path)
    client.edit_plan = edit
    assert answer(store, hits) is None
    assert len(client.calls) == 1


@pytest.mark.parametrize('flag', CHECKS)
def test_whole_fields_does_not_override_independent_rejection(tmp_path, flag):
    store, client, hits = setup(tmp_path)
    client.edit_plan = lambda p:p.update(projection_mode='whole_fields',fragments=[])
    client.edit_review = lambda r:r.update({flag:False})
    assert answer(store, hits) is None


def test_whole_fields_matches_complete_exact_spans_and_rejects_tampering(tmp_path):
    store, client, hits = setup(tmp_path, annotated=True)
    exact = answer(store, hits)
    client.edit_plan = lambda p:p.update(projection_mode='whole_fields',fragments=[])
    whole = answer(store, hits)
    assert whole['answer'] == exact['answer']
    assert whole['native_row_proof']['projection'] == exact['native_row_proof']['projection']
    whole['native_row_proof']['selection']['projection_mode'] = 'exact_spans'
    whole['native_row_proof_sha256'] = _sha(whole['native_row_proof'])
    assert not replay_selection(store, whole)


def test_whole_fields_count_conflict_preserves_all_fields_and_replays(tmp_path):
    store, client, hits = setup(tmp_path)
    client.edit_plan = lambda p:p.update(projection_mode='whole_fields',fragments=[])
    result = answer(store, hits, 'Which two items were handled by PERSON_A using ISO_A?')
    assert result['status'] == 'clarification'
    assert len(result['native_row_proof']['projection']) == 4
    assert result['native_row_proof']['selection']['projection_mode'] == 'whole_fields'
    assert replay_selection(store, result)


def test_short_named_answer_preserves_visible_original_footnote_marker(tmp_path):
    store, client, hits = setup(tmp_path, annotated=True)
    def shorten(plan):
        plan['fragments'][0]['quote'] = 'Item A'
    client.edit_plan = shorten
    result = answer(store, hits)
    assert result['status'] == 'ok'
    assert result['answer'].startswith('Item A *\n')
    field = result['native_row_proof']['projection'][0]['fields'][0]
    assert field['quote'] == 'Item A *' and field['start'] == 0 and field['end'] == 8
    assert result['native_row_proof']['selection']['fragments'][0]['quote'] == 'Item A'
    assert client.review_context['projection'][0]['fields'][0]['quote'] == 'Item A *'
    assert client.review_context['proposed_answer'].startswith('Item A *\n')
    assert replay_selection(store, result)


def test_short_numeric_quotes_preserve_units_inequalities_and_markers(tmp_path):
    store, client, hits = setup(tmp_path, annotated=True)
    def values(plan):
        names = [f for f in plan['fragments'] if f['column_index'] == 0]
        plan['column_indices'] = [0, 1]
        plan['fragments'] = [part for index, name in enumerate(names)
            for part in (name, {'row_id':name['row_id'],'column_index':1,'quote':'200' if index==1 else '35'})]
    client.edit_plan = values
    result = answer(store, hits)
    assert 'Item A * · * 35 UG/L' in result['answer']
    assert 'Item B · <200 UG/L' in result['answer']
    assert 'Item D · 35 UG/L' in result['answer']
    assert replay_selection(store, result)


def test_widened_display_cannot_override_independent_rejection(tmp_path):
    store, client, hits = setup(tmp_path, annotated=True)
    client.edit_plan = lambda p:p['fragments'][0].update(quote='Item A')
    client.edit_review = lambda r:r.update(literal_spans_preserve_requested_qualifiers=False)
    assert answer(store, hits) is None


def test_display_expansion_cannot_rescue_an_unbound_quote(tmp_path):
    store, client, hits = setup(tmp_path, annotated=True)
    client.edit_plan = lambda p:p['fragments'][0].update(quote='Item Z')
    assert answer(store, hits) is None and len(client.calls) == 1


def test_tampering_with_full_annotated_projection_fails_source_replay(tmp_path):
    store, client, hits = setup(tmp_path, annotated=True)
    client.edit_plan = lambda p:p['fragments'][0].update(quote='Item A')
    result = answer(store, hits)
    result['native_row_proof']['projection'][0]['fields'][0].update(quote='Item A',end=6)
    result['native_row_proof_sha256'] = _sha(result['native_row_proof'])
    assert not replay_selection(store, result)


def test_cardinality_conflict_uses_full_original_fields_not_partial_model_fragments(tmp_path):
    store, client, hits = setup(tmp_path)
    client.edit_plan = lambda p:p.update(fragments=[{'row_id':'fake','column_index':0,'quote':'invented'}])
    result = answer(store, hits, 'Which two items were handled by PERSON_A using ISO_A?')
    assert result['status'] == 'clarification' and len(result['citations']) == 4
    assert result['native_row_proof']['selection']['fragments'] == []
    assert result['native_row_proof']['projection'][0]['fields'][0]['quote'] == 'Item A'
    assert replay_selection(store, result)


def test_invalid_count_plan_does_not_fall_back_to_arbitrary_answer(tmp_path):
    store, client, hits = setup(tmp_path)
    client.edit_review = lambda r:r.update(approved=False)
    result = store._answer_hits('Which two items were handled by PERSON_A using ISO_A?', hits, document_id='sample')
    assert result['status'] == 'clarification'
    assert result['answer_mode'] == 'native_row_selection_requires_scope'
    assert result['citations'] == [] and result['clarification_code'] == 'document_native_row_projection_unverified'
    assert len(client.calls) == 2
    client.audit_history = [deepcopy(client.audit)]
    assert not recovery_eligible(result, client)


def test_offsets_are_derived_from_unique_original_quote(tmp_path):
    store, client, hits = setup(tmp_path)
    result = answer(store, hits)
    span = result['native_row_proof']['projection'][0]['fields'][0]
    assert span == {'row_id':result['citations'][0]['metadata']['native_row']['row_id'],
        'column_index':0,'quote':'Item A','start':0,'end':6}
    assert all('start' not in f and 'end' not in f for f in result['native_row_proof']['selection']['fragments'])


@pytest.mark.parametrize('mutation', [
    lambda p: p['fragments'].pop(),
    lambda p: p['fragments'].append(deepcopy(p['fragments'][0])),
    lambda p: p['fragments'][0].update(quote='Item Z'),
    lambda p: p['fragments'][0].update(start=True),
    lambda p: p['fragments'][0].update(row_id='another-document-row'),
    lambda p: p['predicates'][0].update(operator='regex'),
    lambda p: p['predicates'][0].update(literal=''),
    lambda p: p.update(column_indices=[0, True]),
    lambda p: p.update(chain_index=99),
    lambda p: p.update(document_id='unknown'),
    lambda p: p.update(limit=2),
    lambda p: p.update(abstain=True),
])
def test_invalid_or_incomplete_projection_cannot_publish(tmp_path, mutation):
    store, client, hits = setup(tmp_path); client.edit_plan = mutation
    assert answer(store, hits) is None
    assert len(client.calls) == 1


@pytest.mark.parametrize('mutation', [lambda r:r.update(approved=False),
    lambda r:r['matching_row_ids'].pop(), lambda r:r['matching_row_ids'].reverse(),
    lambda r:r.update(literal_spans_preserve_requested_qualifiers=False)])
def test_independent_inventory_or_semantic_rejection(tmp_path, mutation):
    store, client, hits = setup(tmp_path); client.edit_review = mutation
    assert answer(store, hits) is None


@pytest.mark.parametrize('field,value', [('answer','fabricated'), ('status','clarification'),
    ('calculator_input_eligible',True), ('semantic_review',{}), ('answer_scope',{})])
def test_changed_receipt_replay_fails(tmp_path, field, value):
    store, _, hits = setup(tmp_path); result = answer(store, hits); result[field] = value
    assert not replay_selection(store, result)


def test_changed_literal_even_with_new_proof_digest_fails(tmp_path):
    store, _, hits = setup(tmp_path); result = answer(store, hits)
    result['native_row_proof']['projection'][0]['fields'][0]['quote'] = 'Item Z'
    result['native_row_proof_sha256'] = _sha(result['native_row_proof'])
    assert not replay_selection(store, result)


def test_changed_source_during_independent_review_propagates(tmp_path):
    store, client, hits = setup(tmp_path)
    client.on_review = lambda:store.ingest(source(changed=True), document_id='sample', title='Changed', modality='pdf', filename='records.pdf')
    with pytest.raises(SourceIntegrityError):
        answer(store, hits)


def test_explicit_page_not_expanded_and_real_store_dispatch(tmp_path):
    store, client, hits = setup(tmp_path)
    assert answer(store, hits, page_no=1) is None and not client.calls
    assert store._answer_hits(QUESTION, hits, document_id='sample')['answer_mode'] == 'native_row_selection_model_reviewed'


def test_original_count_is_not_a_model_suggestion():
    assert requested_count('Which two tests match?') == 2
    assert requested_count('What 5 records match?') == 5
    assert requested_count('Which tests match?') is None


def test_real_comparison_and_selection_reach_whole_question_review(tmp_path):
    from backend.native_row_comparison import CHECKS as COMPARE_CHECKS
    from backend.document_parts_answer import compose_document_parts, REVIEW
    first = 'Compare Item A with Item D for Record ID CASE-7.'
    second = 'Which items were handled by PERSON_A using ISO_A?'
    question = first.rstrip('.') + ', and ' + second
    class ComposingClient(Client):
        def __init__(self):
            super().__init__(); self.audit_generation = 0; self.audit_dropped_count = 0
            self.audit = {'status': 'completed', 'model_verified': True, 'http_status': 200,
                'model': self.model, 'response_model': self.model, 'reasoning': self.reasoning}
            self.audit_history = [deepcopy(self.audit)]; self.final_packet = None
        def generate(self, instructions, context, schema, **kwargs):
            name = kwargs['name']
            if name == 'document_parts_navigation_plan':
                result = {'abstain': False, 'parts': [{'part_id':1,'standalone_question':first},
                    {'part_id':2,'standalone_question':second}]}
            elif name == 'native_row_comparison_selection':
                rows = context['original_registries'][0]['records']
                result = {'abstain':False,'left_row_id':rows[0]['row_id'],'right_row_id':rows[3]['row_id'],
                    'column_index':1,'scope_label':'Record ID','scope_value':'CASE-7'}
            elif name == 'native_row_comparison_independent_review':
                result = {k:True for k in COMPARE_CHECKS}
            elif name == 'document_parts_original_question_review':
                self.final_packet = deepcopy(context)
                assert all(len(c['native_page_contexts']) == 2 for c in context['components'])
                assert context['components'][0]['server_computation']['operator'] == '='
                assert context['components'][1]['answer_scope']['matching_row_count'] == 4
                result = {k:True for k in REVIEW['properties']}
            else:
                result = super().generate(instructions, context, schema, **kwargs)
            self.audit_history.append(deepcopy(self.audit))
            return result
    client = ComposingClient(); store = KnowledgeStore(tmp_path/'knowledge', generator=SimpleNamespace(client=client))
    store.ingest(source(), document_id='sample', title='Anonymous', modality='pdf', filename='records.pdf')
    result, trace = compose_document_parts(store, question, {'status':'insufficient_evidence'},
        top_k=4, document_id='sample', answer_audit_start=(0,0))
    assert result is not None and result['status'] == 'ok', trace
    assert trace['status'] == 'complete_original_question_reviewed'
    assert client.final_packet['original_question'] == question
    assert result['component_answers'][1]['answer'] == 'Item A\nItem B\nItem D\nItem F\nOperator: PERSON_A'
