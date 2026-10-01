"""Exact rational percentages, question-bound display, pinned PDF replay."""
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
from types import SimpleNamespace

import pytest

from backend.visual_charts import compute_chart_annotations
from backend.visual_chart_routing import route_visual_chart_question, _percentage_display_policy
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.grounded_generation import GroundedGenerator
from tests.test_visual_charts import chart_pdf


def facts(before='90',after='60'):
    base={'source_sha256':'native-source','page_no':1,'chart_id':'native-chart','series':'Alpha',
          'unit':'unknown','scale':None}
    return [{**base,'fact_id':'baseline','year':2030,'raw_value':before,'numeric_value':str(Decimal(before))},
            {**base,'fact_id':'later','year':2035,'raw_value':after,'numeric_value':str(Decimal(after))}]


def exact(result):
    f=result['exact_fraction'];return Fraction(int(f['numerator']),int(f['denominator']))


def test_nonterminating_decline_keeps_rational_and_discloses_default_rounding():
    result=compute_chart_annotations(facts(),'percentage_decline')
    assert result['answer']=='33.33%' and exact(result)==Fraction(100,3)
    assert result['numeric_result_kind']=='rounded_percentage_display'
    assert result['display_is_rounded'] is True
    assert result['display_policy']['decimal_places']==2
    assert result['display_policy']['rounding_mode']=='ROUND_HALF_UP'
    assert result['denominator_fact_id']=='baseline' and result['denominator_raw_value']=='90'
    assert result['operand_unit']=='unknown' and result['unit']=='percent'
    assert result['calculator_input_eligible'] is False


@pytest.mark.parametrize('operation,before,after,answer,rational',[
    ('percentage_change','90','60','-33.33%',Fraction(-100,3)),
    ('percentage_change','60','90','50.00%',Fraction(50)),
    ('percentage_decline','80','30','62.50%',Fraction('62.5')),
    ('percentage_decline','80','80','0.00%',Fraction(0)),
    ('percentage_decline','80','0','100.00%',Fraction(100)),
])
def test_direction_is_explicit_not_absolute_or_later_denominator(operation,before,after,answer,rational):
    result=compute_chart_annotations(facts(before,after),operation)
    assert result['answer']==answer and exact(result)==rational


@pytest.mark.parametrize('question,places,mode',[
    ('Give the percentage decline to three decimal places.',3,'ROUND_HALF_UP'),
    ('Round the percentage change to 0 decimal places.',0,'ROUND_HALF_UP'),
    ('下降百分比，保留两位小数并四舍五入。',2,'ROUND_HALF_UP'),
    ('Percentage decline rounded to the nearest tenth.',1,'ROUND_HALF_UP'),
    ('Percentage change rounded to 2 decimal places using half-even.',2,'ROUND_HALF_EVEN'),
    ('Truncate the decline percentage to 2 decimal places.',2,'ROUND_DOWN'),
    ('Round down the percentage change to 2 decimal places.',2,'ROUND_FLOOR'),
    ('Round up the percentage change to 2 decimal places.',2,'ROUND_CEILING'),
    ('Keep 3 decimals using ROUND_HALF_EVEN.',3,'ROUND_HALF_EVEN'),
    ('下降百分比保留小数点后四位。',4,'ROUND_HALF_UP'),
    ('Keep 3 digits after the decimal point using ROUND_DOWN.',3,'ROUND_DOWN'),
    ('Give 2 decimal places using half_even.',2,'ROUND_HALF_EVEN'),
    ('Give 2 decimal places using half_up.',2,'ROUND_HALF_UP'),
])
def test_format_policy_bound_to_literal_question_only(question,places,mode):
    p=_percentage_display_policy(question)
    assert p['decimal_places']==places and p['rounding_mode']==mode
    assert p['precision_source']=='literal_question'
    assert p['question_instruction_quotes']


@pytest.mark.parametrize('question',[
    'Give 2 decimal places but preserve 3 decimal places.',
    'Give 20 decimal places.','Use -1 decimal places.',
    'Give two significant figures.','精确到两位有效数字。',
    'Use half-even and half-up rounding.',
    'No rounding; return the decline exactly.','保留十二位小数。',
    'Round to several decimal places.',
    'Round to 2 decimal places using half-down.',
    'Use rounding mode stochastic for the percentage.',
    '下降百分比保留-1位小数。','下降百分比保留负一位小数。',
    'Give twenty-one decimal places.','Give twenty two decimal places.',
    'Give 2 or 3 decimal places.','Give 2.5 decimal places.',
    'Give 1e2 decimal places.','Give 1,000 decimal places.',
    'Give −1 decimal places.','Give negative one decimal place.',
    '保留一到三位小数。','保留2.5位小数。','保留1e2位小数。',
    'Give 2 decimal places and 2.5 decimal places.',
    'Give 2 decimal places using ROUND_UP.',
    'Give 2 decimal places using ROUND_05UP.',
    'Give 2 decimal places using round-half-towards-zero.',
    'Give 2 decimal places, away from zero.',
    'Give 2 decimal places, towards positive infinity.',
    'Give 2 decimal places using half-ceiling.',
    'Give 2 decimal places using half-floor.',
    'Give 1/2 decimal places.','Give 2:3 decimal places.',
    'Give 2*3 decimal places.','保留2/3位小数。','保留百分之一位小数。',
    'Give 2 / 3 decimal places.','保留2 / 3位小数。',
])
def test_conflicting_or_unhandled_precision_does_not_silently_default(question):
    with pytest.raises(ValueError):_percentage_display_policy(question)


def test_floor_space_is_not_a_rounding_instruction():
    policy=_percentage_display_policy('What is the percentage change in floor space?')
    assert policy['rounding_mode']=='ROUND_HALF_UP'
    assert policy['rounding_source']=='default_half_up'


@pytest.mark.parametrize('mode,expected',[
    ('ROUND_HALF_UP','12.35%'),('ROUND_HALF_EVEN','12.34%'),('ROUND_DOWN','12.34%'),
    ('ROUND_FLOOR','12.34%'),('ROUND_CEILING','12.35%')])
def test_exact_tie_rounding_is_integer_rational_not_decimal_context(mode,expected):
    with localcontext() as c:
        c.prec=2
        result=compute_chart_annotations(facts('20000','17531'),'percentage_decline',
            display_policy={'decimal_places':2,'rounding_mode':mode})
    assert result['answer']==expected and exact(result)==Fraction(2469,200)


@pytest.mark.parametrize('mode,expected',[
    ('ROUND_HALF_UP','-12.35%'),('ROUND_HALF_EVEN','-12.34%'),('ROUND_DOWN','-12.34%'),
    ('ROUND_FLOOR','-12.35%'),('ROUND_CEILING','-12.34%')])
def test_negative_percentage_rounding_and_direction(mode,expected):
    result=compute_chart_annotations(facts('20000','17531'),'percentage_change',
        display_policy={'decimal_places':2,'rounding_mode':mode})
    assert result['answer']==expected and exact(result)==Fraction(-2469,200)


@pytest.mark.parametrize('change',[
    {'series':'Beta'},{'year':2030},{'year':2029},{'year':True},
    {'chart_id':'other'},{'unit':'count'},{'scale':1000},
    {'source_sha256':'other'},{'page_no':2},
])
def test_cross_series_period_chart_unit_scale_source_rejected_before_review(change):
    operands=facts();operands[1].update(change)
    with pytest.raises(ValueError):compute_chart_annotations(operands,'percentage_decline')


@pytest.mark.parametrize('before',['0','-1'])
def test_undefined_or_negative_baseline_not_assigned_a_convention(before):
    with pytest.raises(ValueError,match='positive_baseline'):
        compute_chart_annotations(facts(before,'0'),'percentage_change')


@pytest.mark.parametrize('policy',[{}, {'decimal_places':True,'rounding_mode':'ROUND_HALF_UP'},
    {'decimal_places':11,'rounding_mode':'ROUND_HALF_UP'},
    {'decimal_places':2,'rounding_mode':'MODEL_CHOSEN'},
    {'decimal_places':-1,'rounding_mode':'ROUND_HALF_UP'}])
def test_invalid_display_policy_cannot_relabel_an_exact_fraction(policy):
    with pytest.raises(ValueError):
        compute_chart_annotations(facts(),'percentage_decline',display_policy=policy)


def test_reversing_periods_or_calling_growth_a_decline_cannot_manufacture_answer():
    with pytest.raises(ValueError,match='temporal_scope'):
        compute_chart_annotations(list(reversed(facts())),'percentage_decline')
    with pytest.raises(ValueError,match='decline_direction'):
        compute_chart_annotations(facts('60','90'),'percentage_decline')


def test_percentage_of_percentage_labels_is_relative_change_not_percentage_points():
    operands=facts('60','30')
    for f in operands:f.update(raw_value=f['raw_value']+'%',unit='percent')
    result=compute_chart_annotations(operands,'percentage_decline')
    assert result['answer']=='50.00%' and result['operand_unit']=='percent'


class Client:
    model='gpt-6-luna';reasoning='medium'
    def __init__(self,*,rows=(0,2),operation='percentage_decline',reject=None,abstain=False,hook=None):
        self.rows,self.operation,self.reject,self.abstain,self.hook=rows,operation,reject,abstain,hook
        self.calls=[];self.audit={}
    def generate(self,instructions,context,schema,**kwargs):
        self.calls.append((instructions,deepcopy(context),deepcopy(schema)))
        self.audit={'status':'completed','http_status':200,'model_verified':True,
                    'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
        if self.hook:self.hook(len(self.calls))
        if len(self.calls)==1:
            registry=context['all_native_chart_candidates'][0]['facts']
            return {'abstain':self.abstain,'operation':self.operation,
                    'fact_ids':[registry[i]['selection_id'] for i in self.rows]}
        return {key:key!=self.reject for key in schema['properties']}


def prepare(tmp_path,**kwargs):
    client=Client(**kwargs);store=KnowledgeStore(tmp_path/'knowledge',generator=GroundedGenerator(client))
    store.ingest(chart_pdf(values=((90,75,60),(70,55,45))),document_id='chart',title='Activity',modality='pdf',filename='a.pdf')
    return store,client


def route(store,question='What is the percentage decline in Alpha from 2021 to 2025, to 3 decimal places?'):
    return route_visual_chart_question(store,question,[SimpleNamespace(metadata={'document_id':'chart'})],document_id='chart')


def test_actual_native_pdf_percentage_two_reviews_and_exact_fraction_replay(tmp_path):
    store,client=prepare(tmp_path)
    result,trace=route(store)
    assert result['status']=='ok' and result['answer']=='33.333%'
    assert exact(result['computation'])==Fraction(100,3)
    assert len(client.calls)==trace['model_requests_attempted']==2
    assert result['answer_scope']['display_policy']['precision_source']=='literal_question'
    review=client.calls[1][1]
    assert review['question']==client.calls[0][1]['question']
    assert review['all_native_chart_candidates']==client.calls[0][1]['all_native_chart_candidates']
    assert review['complete_chart_page_contexts']==client.calls[0][1]['complete_chart_page_contexts']
    assert review['server_computation']['denominator_raw_value']=='90'
    assert result['semantic_review']['relative_percentage_denominator_correct'] is True
    assert result['semantic_review']['precision_and_rounding_follow_question'] is True
    assert all(c['metadata']['fact']['source_sha256'] for c in result['citations'])


@pytest.mark.parametrize('reject',['whole_question_answered','relative_percentage_denominator_correct',
                                 'precision_and_rounding_follow_question','operation_and_direction_correct'])
def test_independent_review_rejects_partial_or_wrong_baseline_precision_direction(tmp_path,reject):
    store,client=prepare(tmp_path,reject=reject)
    result,_=route(store)
    assert result['status']=='incomplete' and result['answer'] is None and len(client.calls)==2


def test_wrong_requested_baseline_is_not_replaced_by_a_convenient_year(tmp_path):
    store,client=prepare(tmp_path,reject='relative_percentage_denominator_correct')
    result,_=route(store,'What is the percentage decline in Alpha from 2023 to 2025?')
    assert result['status']=='incomplete' and result['answer'] is None
    assert client.calls[1][1]['server_computation']['baseline_year']==2021


@pytest.mark.parametrize('rows',[(2,0),(0,3)])
def test_reverse_year_or_cross_series_selection_never_reaches_review(tmp_path,rows):
    store,client=prepare(tmp_path,rows=rows)
    result,_=route(store)
    assert result['status']=='incomplete' and len(client.calls)==1


def test_percentage_points_are_not_silently_relative_percentage(tmp_path):
    store,client=prepare(tmp_path)
    result,_=route(store,'How many percentage points did Alpha decline from 2021 to 2025?')
    assert result['clarification_code']=='chart_percentage_relative_change_not_percentage_points'
    assert len(client.calls)==1


def test_compound_question_stays_abstained_not_partial_percentage(tmp_path):
    store,client=prepare(tmp_path,abstain=True)
    result,_=route(store,'What was the total Alpha in 2021 and its percentage decline to 2025?')
    assert result['status']=='incomplete' and result['answer'] is None and len(client.calls)==1


def test_question_conflicting_precision_refuses_before_second_model(tmp_path):
    store,client=prepare(tmp_path)
    result,_=route(store,'Percentage decline in Alpha from 2021 to 2025, use 2 decimal places and 4 decimal places.')
    assert result['status']=='incomplete' and len(client.calls)==1


@pytest.mark.parametrize('instruction',['保留-1位小数','to twenty-one decimal places',
    'to 2 or 3 decimal places','to 2 decimal places using ROUND_UP'])
def test_invalid_display_instruction_never_reaches_review_or_returns_answer(tmp_path,instruction):
    store,client=prepare(tmp_path)
    result,trace=route(store,'Percentage decline in Alpha from 2021 to 2025, '+instruction)
    assert result['status']=='incomplete' and result['answer'] is None
    assert len(client.calls)==trace['model_requests_attempted']==1


def test_source_mutation_during_percentage_review_never_returns_value(tmp_path):
    store,client=prepare(tmp_path)
    def mutate(call):
        if call==2:store.ingest(chart_pdf(),document_id='chart',title='New',modality='pdf',filename='a.pdf')
    client.hook=mutate
    with pytest.raises(SourceRevisionError):route(store)


def test_fresh_parser_manifest_change_after_percentage_review_refuses(tmp_path,monkeypatch):
    import backend.visual_chart_routing as module
    store,client=prepare(tmp_path);original=module.extract_pdf_charts;calls=0
    def changed(*args,**kwargs):
        nonlocal calls
        calls+=1;manifest=original(*args,**kwargs)
        if calls==2:manifest['status']='changed'
        return manifest
    monkeypatch.setattr(module,'extract_pdf_charts',changed)
    result,_=route(store)
    assert result['clarification_code']=='chart_arithmetic_native_replay_failed' and result['answer'] is None
