"""Native-source arithmetic keeps exact ratios, explicit rounding and scope."""
from decimal import Decimal, localcontext
from types import SimpleNamespace

import fitz
import pytest

from backend.knowledge_store import KnowledgeStore
from backend.native_table_question import (annotation_arithmetic, route_native_table_question, REVIEW,
                                         _percentage_decimal_places, _operation_request_supported)


def facts(left='$1', right='$3'):
    base = {'source_sha256': 'a'*64, 'page_no': 1, 'table_id': 'costs',
            'column_header_path': ['Cost'], 'period': '2030', 'scale': None,
            'unit': 'currency_symbol:$', 'calculator_input_eligible': False}
    return [{**base, 'fact_id': 'north', 'row_header': 'North', 'raw_value': left},
            {**base, 'fact_id': 'total', 'row_header': 'Total', 'raw_value': right}]


@pytest.mark.parametrize('left,right,answer,numerator,denominator,rounded', [
    ('$1', '$3', '≈33.33%', '100', '3', True),
    ('$5', '$6', '≈83.33%', '250', '3', True),
    ('$1', '$8', '12.50%', '25', '2', False),
    ('$-1', '$3', '≈-33.33%', '-100', '3', True),
    ('$2', '$1', '200.00%', '200', '1', False),
    ('$0', '$3', '0.00%', '0', '1', False),
    ('$1', '$32', '≈3.13%', '25', '8', True),
    ('$-1', '$32', '≈-3.13%', '-25', '8', True),
])
def test_percentage_is_exact_rational_with_honest_display_rounding(left,right,answer,numerator,denominator,rounded):
    with localcontext() as context:
        context.prec = 2
        result = annotation_arithmetic(facts(left,right), 'percentage')
    assert result['answer'] == answer
    assert result['exact_fraction'] == {'numerator': numerator, 'denominator': denominator}
    assert result['rounded'] is rounded and result['display_decimal_places'] == 2
    assert result['unit'] == 'percent' and result['scale'] is None
    assert result['physical_calculator_input_eligible'] is False


def test_large_percentage_remains_exact_without_decimal_context_truncation():
    result = annotation_arithmetic(facts('$'+'9'*80, '$0.'+'0'*90+'1'), 'percentage')
    assert result['rounded'] is False
    assert result['answer'] == '9'*80 + '0'*93 + '.00%'


@pytest.mark.parametrize('change', [
    {'unit': 'unknown'}, {'scale': '1000'}, {'page_no': 2}, {'period': '2031'},
    {'source_sha256': 'b'*64}, {'table_id': 'unrelated'}, {'column_header_path': ['Other']},
])
def test_percentage_never_combines_unbound_or_mismatched_operands(change):
    selected = facts()
    selected[1].update(change)
    with pytest.raises(ValueError):
        annotation_arithmetic(selected, 'percentage')


def test_zero_denominator_still_refuses_a_percentage():
    with pytest.raises(ValueError, match='zero_denominator'):
        annotation_arithmetic(facts('$1', '$0'), 'percentage')


def test_absolute_gap_is_distinct_from_signed_difference():
    selected = facts('$1.25', '$5.50')
    assert annotation_arithmetic(selected, 'difference')['answer'] == '$-4.25'
    assert annotation_arithmetic(selected, 'absolute_difference')['answer'] == '$4.25'
    assert annotation_arithmetic(list(reversed(selected)), 'absolute_difference')['answer'] == '$4.25'


def document():
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50,40), 'Cost allocation 2030')
        for index, (name, value) in enumerate([('North', '$5'), ('Total', '$6'), ('Other', '$1')]):
            page.insert_text((50,100+index*25), name, fontsize=10)
            page.insert_text((260,100+index*25), value, fontsize=10)
        return pdf.tobytes()


class Client:
    model, reasoning = 'gpt-6-luna', 'medium'
    def __init__(self, reverse=False):
        self.audit, self.calls, self.reverse = {}, [], reverse
        self.review_context = None
    def generate(self, instructions, context, schema, **options):
        self.calls.append(options['name'])
        self.audit = {'status':'completed','http_status':200,'model_verified':True,
                      'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
        if options['name'] == 'native_table_fact_selection':
            assert 'percentage' in schema['properties']['operation']['enum']
            ids = [fact['selection_id'] for fact in context['native_table_registry'][0]['facts'][:2]]
            return {'abstain':False,'operation':'percentage','fact_ids':list(reversed(ids)) if self.reverse else ids}
        computation = context['server_annotation_computation']
        self.review_context = context
        assert 'exact_fraction' in computation and 'approximation marker' in instructions
        return {key: not self.reverse for key in REVIEW['properties']}


@pytest.mark.parametrize('reverse', [False, True])
def test_percentage_reaches_original_pdf_and_independent_scope_review(tmp_path, reverse):
    client = Client(reverse)
    store = KnowledgeStore(tmp_path/'knowledge', generator=SimpleNamespace(client=client))
    store.ingest(document(), document_id='costs', title='Cost allocation', modality='pdf', filename='costs.pdf')
    result, trace = route_native_table_question(store, 'What percentage of Total cost is North?',
        [SimpleNamespace(metadata={'document_id':'costs'})], document_id='costs')
    assert client.calls == ['native_table_fact_selection','native_table_independent_scope_review']
    if reverse:
        assert result is None and trace['status'] == 'native_table_semantic_scope_review_rejected'
    else:
        assert result['answer'] == '≈83.33%' and result['computation']['rounded'] is True
        assert result['computation']['operands'] == ['$5','$6']
        assert len(result['citations']) == 2
        assert all(c['metadata']['source_sha256'] == store.list_documents()[0]['sha256'] for c in result['citations'])


@pytest.mark.parametrize('question', ['What percentage increase in North cost?', 'North的增长率百分比是多少？'])
def test_a_share_operation_cannot_answer_percent_change_even_if_model_approves(tmp_path, question):
    client = Client()
    store = KnowledgeStore(tmp_path/'knowledge', generator=SimpleNamespace(client=client))
    store.ingest(document(), document_id='costs', title='Cost allocation', modality='pdf', filename='costs.pdf')
    result, trace = route_native_table_question(store, question,
        [SimpleNamespace(metadata={'document_id':'costs'})], document_id='costs')
    assert result is None and trace['status'] == 'native_table_selection_invalid'
    assert client.calls == ['native_table_fact_selection']


@pytest.mark.parametrize('places', range(7))
@pytest.mark.parametrize('sign', [1, -1])
def test_each_percentage_precision_rounds_exact_half_ties_away_from_zero(places, sign):
    with localcontext() as context:
        context.prec = 1
        result = annotation_arithmetic(facts('$'+str(sign*3), '$'+str(200*10**places)),
                                       'percentage', percentage_decimal_places=places)
    expected = Decimal((int(sign < 0), (2,), -places))
    assert result['answer'] == '≈'+format(expected, f'.{places}f')+'%'
    assert result['rounded'] is True
    assert result['display_decimal_places'] == places
    assert result['exact_fraction'] == {'numerator': str(sign*3), 'denominator': str(2*10**places)}


@pytest.mark.parametrize('places', range(7))
def test_values_below_half_ties_do_not_round_up_and_exact_values_need_no_marker(places):
    below = annotation_arithmetic(facts('$2.999', '$'+str(200*10**places)), 'percentage',
                                  percentage_decimal_places=places)
    expected = Decimal((0, (1,), -places))
    assert below['answer'] == '≈'+format(expected, f'.{places}f')+'%'
    exact = annotation_arithmetic(facts('$2', '$'+str(200*10**places)), 'percentage',
                                  percentage_decimal_places=places)
    assert exact['answer'] == format(expected, f'.{places}f')+'%'
    assert exact['rounded'] is False


@pytest.mark.parametrize('token,places', [
    ('zero',0), ('one',1), ('two',2), ('three',3), ('four',4), ('five',5), ('six',6),
    ('0',0), ('1',1), ('6',6),
])
def test_explicit_english_decimal_places_bind_original_request(token, places):
    assert _percentage_decimal_places(f'Round to {token} decimal places.') == places


@pytest.mark.parametrize('question,places', [
    ('保留零位小数',0), ('保留一位小数',1), ('保留二位小数',2), ('保留三位小数',3),
    ('保留四位小数',4), ('保留五位小数',5), ('保留六位小数',6),
    ('保留小数点后一位',1), ('小数位为二',2), ('小数位数：6',6),
    ('保留1位小数，round to one decimal place',1),
])
def test_explicit_chinese_decimal_places_and_agreeing_repetition(question, places):
    assert _percentage_decimal_places(question) == places


@pytest.mark.parametrize('question', [
    'round to seven decimal places', 'round to 7 decimal places',
    'round to -1 decimal places', 'round to 1.5 decimal places',
    'round to many decimal places', 'use decimal precision',
    'round to three decimals', 'do not use one decimal place', '不要保留一位小数',
    '保留七位小数', '保留-1位小数', '保留2.5位小数', '保留若干位小数',
    '保留一位小数和两位小数', 'one decimal place and two decimal places',
    'three significant figures', 'two significant digits', '保留三位有效数字',
])
def test_conflicting_unknown_and_significant_precision_never_defaults_to_two(question):
    with pytest.raises(ValueError, match='precision_'):
        _percentage_decimal_places(question)


@pytest.mark.parametrize('places', [-1, 7, 1.5, '1', True, None])
def test_arithmetic_rejects_non_contract_precision_values(places):
    with pytest.raises(ValueError, match='precision_'):
        annotation_arithmetic(facts(), 'percentage', percentage_decimal_places=places)


@pytest.mark.parametrize('precision_request,places,answer', [
    ('Round to one decimal place.',1,'≈83.3%'),
    ('Round to zero decimal places.',0,'≈83%'),
    ('保留一位小数。',1,'≈83.3%'),
    ('保留零位小数。',0,'≈83%'),
])
def test_real_pdf_percentage_precision_binds_computation_and_independent_review(tmp_path, precision_request, places, answer):
    client = Client()
    store = KnowledgeStore(tmp_path/'knowledge', generator=SimpleNamespace(client=client))
    store.ingest(document(), document_id='costs', title='Cost allocation', modality='pdf', filename='costs.pdf')
    result, trace = route_native_table_question(store, 'What percentage of Total cost is North? '+precision_request,
        [SimpleNamespace(metadata={'document_id':'costs'})], document_id='costs')
    assert result is not None and result['status'] == 'ok'
    assert result['answer'] == answer
    assert result['computation']['display_decimal_places'] == places
    contract = client.review_context['percentage_precision_contract']
    assert contract['decimal_places'] == places
    assert contract['verification'] == 'server_original_question_precision'
    assert trace['percentage_precision_contract'] == contract
    assert result['computation']['exact_fraction'] == {'numerator':'250','denominator':'3'}


@pytest.mark.parametrize('precision_request', [
    'Round to one decimal place and two decimal places.',
    'Round to three significant figures.',
    'Round to seven decimal places.',
    '保留若干位小数。',
])
def test_original_pdf_unsupported_precision_refuses_even_an_approved_selection(tmp_path, precision_request):
    client = Client()
    store = KnowledgeStore(tmp_path/'knowledge', generator=SimpleNamespace(client=client))
    store.ingest(document(), document_id='costs', title='Cost allocation', modality='pdf', filename='costs.pdf')
    result, trace = route_native_table_question(store, 'What percentage of Total cost is North? '+precision_request,
        [SimpleNamespace(metadata={'document_id':'costs'})], document_id='costs')
    assert result is None and trace['status'] == 'native_table_annotation_contract_failed'
    assert client.calls == ['native_table_fact_selection']


@pytest.mark.parametrize('question', [
    'What is the percentage difference between North cost and South cost?',
    'What is the percent difference between North cost and South cost?',
    'North与South的费用百分比差异是多少？',
])
def test_a_percentage_difference_is_not_authorized_as_a_share(question):
    assert _operation_request_supported('percentage', question) is False
    assert _operation_request_supported('absolute_difference',
                                        'What is the absolute difference between North and South?') is True
