"""Fresh synthetic PDFs, exact arithmetic, two independent mock model gates."""
from copy import deepcopy
from types import SimpleNamespace
from decimal import Decimal
import pytest

from backend.visual_charts import extract_pdf_charts, compute_chart_annotations
from backend.visual_chart_routing import route_visual_chart_question, ARITHMETIC_REVIEW
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import GenerationError
from tests.test_visual_charts import chart_pdf


def operands(left='1', right='0.'+'0'*40+'1'):
    base = {'source_sha256': 'x', 'page_no': 1, 'chart_id': 'c', 'unit': 'unknown',
            'scale': None, 'series': 'Alpha'}
    return [{**base, 'fact_id': 'a', 'raw_value': left, 'numeric_value': str(Decimal(left))},
            {**base, 'fact_id': 'b', 'raw_value': right, 'numeric_value': str(Decimal(right))}]


def test_sum_retains_tiny_fraction_without_decimal_context_rounding():
    value = compute_chart_annotations(operands(), 'sum')
    assert value['answer'] == '1.'+'0'*40+'1'
    assert value['unit'] == 'unknown' and value['calculator_input_eligible'] is False


@pytest.mark.parametrize('operation,left,right,expected', [
    ('difference', '1', '2', '-1'), ('sum', '-1', '2', '1'),
    ('ratio', '7.5', '2', '3.75'), ('difference', '1', '1', '0')])
def test_exact_operations(operation, left, right, expected):
    assert compute_chart_annotations(operands(left, right), operation)['answer'] == expected


@pytest.mark.parametrize('right,code', [('0', 'zero_denominator'), ('3', 'nonterminating')])
def test_ratio_does_not_round_or_divide_by_zero(right, code):
    with pytest.raises(ValueError, match=code):
        compute_chart_annotations(operands('1', right), 'ratio')


@pytest.mark.parametrize('field,value', [('chart_id', 'other'), ('page_no', 2),
    ('unit', 'USD'), ('scale', 1000), ('source_sha256', 'other')])
def test_arithmetic_cannot_cross_native_scope(field, value):
    facts = operands(); facts[1][field] = value
    with pytest.raises(ValueError): compute_chart_annotations(facts, 'sum')


def test_currency_and_scale_never_inferred_and_total_not_double_counted():
    facts = operands(); facts[0]['series'] = 'Total'
    with pytest.raises(ValueError, match='total_components'): compute_chart_annotations(facts, 'sum')
    facts = operands(); facts[0]['numeric_value'] = '100'
    with pytest.raises(ValueError, match='literal_mismatch'): compute_chart_annotations(facts, 'sum')


def test_percent_ratio_remains_proportion_without_inventing_percentage_conversion():
    facts = operands('25', '50')
    for f in facts: f.update(raw_value=f['raw_value']+'%', unit='percent')
    result = compute_chart_annotations(facts, 'ratio')
    assert result['answer'] == '0.5' and result['unit'] == 'ratio'
    assert compute_chart_annotations(facts, 'difference')['answer'] == '-25%'


class Client:
    model = 'gpt-6-luna'; reasoning = 'medium'
    def __init__(self, operation='difference', rows=(0, 2), reject=False, fail=None, hook=None):
        self.operation, self.rows, self.reject, self.fail, self.hook = operation, rows, reject, fail, hook
        self.audit = {}; self.calls = []
    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append((deepcopy(context), deepcopy(schema), kwargs))
        self.audit = {'status': 'completed', 'model_verified': True, 'http_status': 200,
                      'model': self.model, 'reasoning': self.reasoning, 'response_model': self.model}
        if self.hook: self.hook(len(self.calls))
        if self.fail == 'http': self.audit['http_status'] = 401
        if self.fail == 'provider': raise GenerationError('no retry')
        if len(self.calls) == 1:
            facts = context['all_native_chart_candidates'][0]['facts']
            return {'abstain': self.fail == 'abstain', 'operation': self.operation,
                    'fact_ids': [facts[n]['selection_id'] for n in self.rows]}
        return {k: not self.reject for k in ARITHMETIC_REVIEW['properties']}


def prepare(tmp_path, **kwargs):
    client = Client(**kwargs)
    store = KnowledgeStore(tmp_path/'knowledge', generator=GroundedGenerator(client))
    store.ingest(chart_pdf(), document_id='chart', title='Activity', modality='pdf', filename='a.pdf')
    return store, client


def route(store, question='By how much did Alpha decrease from 2021 to 2025?'):
    return route_visual_chart_question(store, question,
        [SimpleNamespace(metadata={'document_id': 'chart'})], document_id='chart')


def test_actual_pdf_decrease_exact_proof_and_compact_keys(tmp_path):
    store, client = prepare(tmp_path)
    result, trace = route(store)
    assert result['answer'] == '50' and result['status'] == 'ok'
    assert len(client.calls) == 2 and trace['model_requests_attempted'] == 2
    assert result['computation']['operands'] == ['80', '30']
    assert all(c['metadata']['fact']['bbox_display_pt'] for c in result['citations'])
    context, schema, _ = client.calls[0]
    assert schema['properties']['fact_ids']['items']['enum'] == ['F001','F002','F003','F004','F005','F006']
    assert context['all_native_chart_candidates'][0]['facts'][0]['selection_id'] == 'F001'
    assert 'fact_id' not in context['all_native_chart_candidates'][0]['facts'][0]
    assert 'Rates of service activity' in context['complete_chart_page_contexts'][0]['complete_native_page_text']
    assert result['answer_scope']['unit'] == 'unknown'
    assert result['semantic_verification'] == 'independent_model_review_not_formal_entailment'


def test_main_store_runs_arithmetic_without_other_module_changes(tmp_path):
    store, client = prepare(tmp_path)
    result = store.answer('By how much did Alpha decrease from 2021 to 2025?', document_id='chart')
    assert result['answer'] == '50' and len(client.calls) == 2


def test_total_word_can_bind_single_existing_native_series(tmp_path):
    store, client = prepare(tmp_path, operation='lookup', rows=(1,))
    result, _ = route(store, 'What is the total value of Alpha in 2023?')
    assert result['answer'] == '60' and result['computation']['operation'] == 'lookup'


@pytest.mark.parametrize('kwargs,code,calls', [
    ({'reject': True}, 'scope_review_rejected', 2),
    ({'fail': 'provider'}, 'model_unavailable', 1),
    ({'fail': 'http'}, 'selection_invalid', 1),
    ({'fail': 'abstain'}, 'whole_question_unsupported', 1),
    ({'rows': (0, 0)}, 'annotation_contract_failed', 1)])
def test_rejections_do_not_fall_back_to_partial_text(tmp_path, kwargs, code, calls):
    store, client = prepare(tmp_path, **kwargs)
    result, _ = route(store)
    assert result['status'] == 'incomplete' and result['answer'] is None
    assert code in result['clarification_code'] and len(client.calls) == calls


def test_reversed_subtraction_and_compound_question_require_full_scope_review(tmp_path):
    store, client = prepare(tmp_path, rows=(2, 0), reject=True)
    result, _ = route(store, 'What is the total value of Alpha in 2023 and how does it compare to Beta?')
    assert result['status'] == 'incomplete'
    assert len(client.calls) == 2
    assert client.calls[1][0]['server_computation']['answer'] == '-50'


def test_source_changed_during_model_request_is_not_swallowed(tmp_path):
    store, client = prepare(tmp_path)
    def mutate(call):
        if call == 1:
            store.ingest(chart_pdf(values=((81,61,31),(71,56,46))), document_id='chart',
                         title='Activity', modality='pdf', filename='a.pdf')
    client.hook = mutate
    with pytest.raises(SourceRevisionError): route(store)


def test_duplicate_native_source_stays_ambiguous(tmp_path):
    store, client = prepare(tmp_path)
    raw = store.verify_source('chart').read_bytes()
    store.ingest(raw, document_id='other', title='Duplicate', modality='pdf', filename='b.pdf')
    hits = [SimpleNamespace(metadata={'document_id': k}) for k in ('chart','other')]
    result, _ = route_visual_chart_question(store, 'What is the total value of Alpha in 2023?', hits)
    assert result['clarification_code'] == 'chart_arithmetic_duplicate_source_scope'
    assert not client.calls


def test_complete_context_budget_refuses_truncation(tmp_path, monkeypatch):
    import fitz
    store, client = prepare(tmp_path); original = fitz.Page.get_text
    def verbose(page, *args, **kwargs):
        return 'scope '*1000 if args and args[0] == 'text' else original(page, *args, **kwargs)
    monkeypatch.setattr(fitz.Page, 'get_text', verbose)
    result, _ = route(store)
    assert result['clarification_code'] == 'chart_arithmetic_complete_context_budget'
    assert not client.calls


def test_fresh_native_replay_change_never_returns_answer(tmp_path, monkeypatch):
    import backend.visual_chart_routing as module
    store, client = prepare(tmp_path); original = module.extract_pdf_charts; calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        manifest = original(*args, **kwargs)
        if calls == 2: manifest['status'] = 'changed_after_review'
        return manifest
    monkeypatch.setattr(module, 'extract_pdf_charts', changed)
    result, _ = route(store)
    assert result['clarification_code'] == 'chart_arithmetic_native_replay_failed'
    assert result['answer'] is None


def test_other_model_is_not_used_for_arithmetic(tmp_path):
    store, client = prepare(tmp_path); client.model = 'other-model'
    result, _ = route(store)
    assert result['clarification_code'] == 'chart_arithmetic_model_contract_unavailable'
    assert not client.calls
