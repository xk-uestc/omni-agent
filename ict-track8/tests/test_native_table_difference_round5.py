"""Signed differences over independent native-table fixtures, without APIs."""
from copy import deepcopy
from decimal import localcontext
from types import SimpleNamespace

import fitz
import pytest

from backend.knowledge_store import KnowledgeStore, SourceIntegrityError
from backend.native_table_question import annotation_arithmetic, route_native_table_question, REVIEW


def operands(left='$130.25', right='$105.10'):
    base = {'source_sha256': 'a' * 64, 'page_no': 1, 'table_id': 'local-costs',
            'column_header_path': ['Cost'], 'period': '2030', 'unit': 'currency_symbol:$',
            'scale': None, 'calculator_input_eligible': False}
    return [{**base, 'fact_id': 'north', 'row_header': 'North', 'raw_value': left},
            {**base, 'fact_id': 'south', 'row_header': 'South', 'raw_value': right}]


def test_difference_preserves_order_sign_unit_and_unprivileged_domain():
    facts = operands()
    assert annotation_arithmetic(facts, 'difference')['answer'] == '$25.15'
    reversed_result = annotation_arithmetic(list(reversed(facts)), 'difference')
    assert reversed_result['answer'] == '$-25.15'
    assert reversed_result['numeric_result'] == '-25.15'
    assert reversed_result['operands'] == ['$105.10', '$130.25']
    assert reversed_result['unit'] == 'currency_symbol:$'
    assert reversed_result['physical_calculator_input_eligible'] is False


def test_difference_remains_decimal_exact_under_short_global_context():
    tiny = '0.' + '0' * 40 + '1'
    with localcontext() as context:
        context.prec = 2
        result = annotation_arithmetic(operands('$1', '$' + tiny), 'difference')
    assert result['numeric_result'] == '0.' + '9' * 41


@pytest.mark.parametrize('changed', [
    {'source_sha256': 'b' * 64}, {'page_no': 2}, {'table_id': 'other'},
    {'unit': 'currency_symbol:€'}, {'scale': '1000'},
    {'column_header_path': ['Other cost']}, {'period': '2031'},
])
def test_difference_refuses_cross_source_table_column_period_unit_or_scale(changed):
    facts = operands()
    facts[1].update(changed)
    with pytest.raises(ValueError, match='scope_mismatch'):
        annotation_arithmetic(facts, 'difference')


def test_difference_refuses_undeclared_units_and_never_infers_from_prose():
    facts = operands('0.41', '0.32')
    for fact in facts:
        fact['unit'] = 'unknown'
        fact['nearby_text'] = 'The observed quantity is expressed in an external unit.'
    with pytest.raises(ValueError, match='unit_or_scale_unbound'):
        annotation_arithmetic(facts, 'difference')


@pytest.mark.parametrize('count', [1, 3])
def test_difference_requires_exactly_two_distinct_operands(count):
    facts = operands()[:count]
    if count == 3:
        facts.append({**facts[0], 'fact_id': 'spare'})
    with pytest.raises(ValueError, match='operands_invalid'):
        annotation_arithmetic(facts, 'difference')
    with pytest.raises(ValueError, match='operands_invalid'):
        annotation_arithmetic([operands()[0], operands()[0]], 'difference')


def cost_pdf():
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 40), 'Local cost plan')
        page.insert_text((50, 65), 'July 2030 - June 2031')
        for row, (label, amount) in enumerate([('North', '$130.25'), ('South', '$105.10'), ('West', '$150.00')]):
            page.insert_text((50, 100 + row * 25), label, fontsize=10)
            page.insert_text((260, 100 + row * 25), amount, fontsize=10)
        return document.tobytes()


class LiteralSelector:
    model = 'gpt-6-luna'
    reasoning = 'medium'

    def __init__(self, *, reverse=False, operation='difference', abstain=False, hook=None):
        self.reverse, self.operation, self.abstain, self.hook = reverse, operation, abstain, hook
        self.calls, self.audit, self.contexts = 0, {}, []

    def generate(self, instructions, context, schema, **kwargs):
        self.calls += 1
        self.contexts.append(deepcopy(context))
        self.audit = {'status': 'completed', 'http_status': 200, 'model_verified': True,
                      'model': self.model, 'reasoning': self.reasoning, 'response_model': self.model}
        if self.hook:
            self.hook(self.calls)
        if self.calls == 1:
            self.selection_schema = deepcopy(schema)
            ids = [fact['selection_id'] for fact in context['native_table_registry'][0]['facts'][:2]]
            if self.reverse:
                ids.reverse()
            return {'abstain': self.abstain, 'operation': self.operation, 'fact_ids': ids}
        assert 'minuend then subtrahend' in instructions
        return {key: not self.reverse for key in REVIEW['properties']}


def environment(tmp_path, **options):
    knowledge = KnowledgeStore(tmp_path / 'knowledge')
    knowledge.ingest(cost_pdf(), document_id='local-costs', title='Local cost plan',
                     modality='pdf', filename='costs.pdf')
    client = LiteralSelector(**options)
    knowledge.generator = SimpleNamespace(client=client)
    return knowledge, client, [SimpleNamespace(metadata={'document_id': 'local-costs'})]


@pytest.mark.parametrize('question', [
    'What is the difference of North minus South?',
    'Subtract South from North.',
    'North减去South的差值是多少？',
])
def test_generic_difference_words_reach_verified_native_operation(tmp_path, question):
    knowledge, client, hits = environment(tmp_path)
    result, trace = route_native_table_question(knowledge, question, hits)
    assert result['status'] == 'ok' and result['answer'] == '$25.15'
    assert client.calls == 2 and trace['model_requests_attempted'] == 2
    assert 'difference' in client.selection_schema['properties']['operation']['enum']
    assert result['computation']['operation'] == 'difference'
    assert result['calculator_input_eligible'] is False
    assert result['answer_scope']['calculator_input_eligible'] is False
    assert result['computation']['physical_calculator_input_eligible'] is False


def test_reversed_difference_must_pass_independent_direction_review(tmp_path):
    knowledge, client, hits = environment(tmp_path, reverse=True)
    result, trace = route_native_table_question(knowledge, 'Subtract South from North.', hits)
    assert result is None and trace['status'] == 'native_table_semantic_scope_review_rejected'
    assert client.calls == 2
    assert client.contexts[1]['server_annotation_computation']['numeric_result'] == '-25.15'


def test_unsupported_absolute_difference_is_not_coerced_to_signed_difference(tmp_path):
    knowledge, client, hits = environment(tmp_path, operation='absolute_difference')
    result, trace = route_native_table_question(knowledge, 'What is the difference of North minus South?', hits)
    assert result is None and trace['status'] == 'native_table_selection_invalid'
    assert client.calls == 1


def test_unbound_direction_can_abstain_without_server_guessing(tmp_path):
    knowledge, client, hits = environment(tmp_path, abstain=True)
    result, trace = route_native_table_question(knowledge, 'What is the difference between North and South?', hits)
    assert result is None and trace['status'] == 'native_table_whole_question_unsupported'
    assert client.calls == 1


def test_difference_still_replays_the_pinned_source_after_selection(tmp_path):
    knowledge, client, hits = environment(tmp_path)
    source, _ = knowledge.original('local-costs')
    client.hook = lambda call: source.write_bytes(b'changed local source') if call == 1 else None
    with pytest.raises(SourceIntegrityError):
        route_native_table_question(knowledge, 'Subtract South from North.', hits)
