from types import SimpleNamespace
from copy import deepcopy
from decimal import Decimal

import fitz
import pytest

from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.native_table_question import route_native_table_question, annotation_arithmetic, REVIEW
from backend.responses_client import GenerationError


def pdf(labels=('Formula','Diapers','Equipment'), period='July 2030 - June 2031'):
    d=fitz.open();p=d.new_page()
    p.insert_text((50,40),'Budget Narrative');p.insert_text((50,65),period)
    for index,label in enumerate(labels):
        p.insert_text((50,100+index*25),label,fontsize=10,fontname='china-s' if any(ord(c)>127 for c in label) else 'helv')
        p.insert_text((260,100+index*25),['$1,000.10','$2,000.20','$3,000.00'][index],fontsize=10)
    raw=d.tobytes();d.close();return raw


class Client:
    model='gpt-6-luna';reasoning='medium'
    def __init__(self,mode='sum',hook=None):self.mode=mode;self.hook=hook;self.calls=0;self.audit={}
    def generate(self,instructions,context,schema,**kwargs):
        self.calls+=1
        self.audit={'status':'completed','http_status':200,'model_verified':True,
                    'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
        if self.hook:self.hook(self.calls)
        if self.mode=='failure':
            self.audit.update(status='failed',http_status=503)
            raise GenerationError('provider failed')
        if self.mode=='bad_http':self.audit['http_status']=401
        if self.calls==1:
            rows=context['native_table_registry']
            ids=[f['selection_id'] for f in rows[0]['facts'][:2]]
            self.selection_schema=deepcopy(schema);self.selection_context=deepcopy(context)
            if self.mode=='crossdoc':ids=[rows[0]['facts'][0]['selection_id'],rows[1]['facts'][1]['selection_id']]
            return {'abstain':self.mode=='abstain','operation':'sum','fact_ids':ids}
        return {key:self.mode!='reject' for key in REVIEW['properties']}


def prepare(tmp_path,mode='sum',copies=1,labels=('Formula','Diapers','Equipment')):
    store=KnowledgeStore(tmp_path/'knowledge');raw=pdf(labels)
    for n in range(copies):store.ingest(raw,document_id='budget'+str(n),title='Budget',modality='pdf',filename='b.pdf')
    client=Client(mode);store.generator=SimpleNamespace(client=client)
    hits=[SimpleNamespace(metadata={'document_id':'budget'+str(n)}) for n in range(copies)]
    return store,client,hits


def test_exact_same_table_decimal_sum_and_audits(tmp_path):
    store,client,hits=prepare(tmp_path)
    result,trace=route_native_table_question(store,'Total budget for Formula and Diapers',hits)
    assert result['status']=='ok' and result['answer']=='$3,000.30'
    assert result['computation']['numeric_result']=='3000.30'
    assert trace['model_requests_attempted']==2 and len(trace['model_audits'])==2
    assert result['answer_scope']['currency']=='unknown'
    assert result['calculator_input_eligible'] is False
    assert {c['document_id'] for c in result['citations']}=={'budget0'}


def test_production_answer_routes_native_pdf_to_exact_sum(tmp_path):
    store,client,_=prepare(tmp_path)
    result=store.answer('Total budget for Formula and Diapers',document_id='budget0')
    assert result['answer']=='$3,000.30'
    assert result['answer_mode']=='native_table_model_reviewed'
    assert result['computation']['operands']==['$1,000.10','$2,000.20']
    assert client.calls==2


@pytest.mark.parametrize('mode,calls,status',[
    ('abstain',1,'native_table_whole_question_unsupported'),
    ('reject',2,'native_table_semantic_scope_review_rejected'),
    ('failure',1,'native_table_model_unavailable'),
    ('bad_http',1,'native_table_selection_invalid')])
def test_failed_or_partial_model_path_falls_back_and_keeps_request_audits(tmp_path,mode,calls,status):
    store,client,hits=prepare(tmp_path,mode)
    result,trace=route_native_table_question(store,'Formula budget',hits)
    assert result is None and trace['status']==status
    assert trace['model_requests_attempted']==calls and len(trace['model_audits'])==calls


def test_identical_pdf_registered_twice_cannot_mix_operands(tmp_path):
    store,client,hits=prepare(tmp_path,'crossdoc',copies=2)
    result,trace=route_native_table_question(store,'Formula and Diapers total budget',hits)
    assert result is None and trace['status']=='native_table_competing_document_operands'
    assert client.calls==1


def test_chinese_row_hint_routes_to_model(tmp_path):
    store,client,hits=prepare(tmp_path,labels=('设备费用','服务费用','其他费用'))
    result,trace=route_native_table_question(store,'设备费用和服务费用合计',hits)
    assert trace['model_requests_attempted']==2 and result['status']=='ok'


def test_explicit_page_scans_one_page_even_when_whole_pdf_exceeds_budget(tmp_path,monkeypatch):
    store,client,hits=prepare(tmp_path)
    original=store.list_documents
    monkeypatch.setattr(store,'list_documents',lambda:[{**d,'stats':{'page_count':1001}} for d in original()])
    result,trace=route_native_table_question(store,'Formula budget',hits,document_id='budget0',page_no=1)
    assert result['status']=='ok'
    result,trace=route_native_table_question(store,'Formula budget',hits)
    assert result['clarification_code']=='native_table_candidate_budget_exceeded'


def test_fresh_replay_parser_exception_is_safe_fallback(tmp_path,monkeypatch):
    import backend.native_table_question as module
    store,client,hits=prepare(tmp_path);original=module.extract_native_text_tables;calls=0
    def parser(*args,**kwargs):
        nonlocal calls
        calls+=1
        if calls==2:raise RuntimeError('native parser failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'extract_native_text_tables',parser)
    result,trace=route_native_table_question(store,'Formula budget',hits)
    assert result is None and trace['status']=='native_table_literal_proof_replay_parser_failed'


def test_source_mutation_after_model_request_is_not_accepted(tmp_path):
    store,client,hits=prepare(tmp_path)
    def changed(call):
        if call==1:store.ingest(pdf(period='July 2040 - June 2041'),document_id='budget0',title='Budget',modality='pdf',filename='b.pdf')
    client.hook=changed
    with pytest.raises(SourceRevisionError):route_native_table_question(store,'Formula budget',hits)


def test_arithmetic_rejects_cross_period_table_or_unit_scope():
    a={'fact_id':'a','source_sha256':'x','page_no':1,'table_id':'t1','unit':'currency_symbol:$','raw_value':'$1'}
    for key,value in [('page_no',2),('table_id','t2'),('unit','currency_symbol:€'),('source_sha256','other')]:
        b={**a,'fact_id':'b',key:value}
        with pytest.raises(ValueError,match='scope_mismatch'):annotation_arithmetic([a,b],'sum')


def test_total_and_components_can_be_rejected_by_independent_review(tmp_path):
    store,client,hits=prepare(tmp_path,'sum',labels=('TOTAL','Formula','Diapers'))
    result,trace=route_native_table_question(store,'TOTAL and Formula budget',hits)
    assert result is None and trace['status']=='native_table_annotation_contract_failed'
    assert client.calls==1


def test_same_table_different_column_periods_cannot_sum():
    a={'fact_id':'a','source_sha256':'x','page_no':1,'table_id':'t','unit':'currency_symbol:$','raw_value':'$1',
       'column_header_path':['2025'],'period':'2025'}
    b={**a,'fact_id':'b','column_header_path':['2026'],'period':'2026'}
    with pytest.raises(ValueError,match='column_period'):annotation_arithmetic([a,b],'sum')


def test_wire_registry_is_compact_unique_enum_and_page_scope_complete(tmp_path):
    store,client,hits=prepare(tmp_path,copies=2)
    result,trace=route_native_table_question(store,'Total budget for Formula and Diapers',hits)
    registry=client.selection_context['native_table_registry']
    keys=[f['selection_id'] for table in registry for f in table['facts']]
    assert keys==['F001','F002','F003','F004','F005','F006']
    assert client.selection_schema['properties']['fact_ids']['items']['enum']==keys
    assert 'fact_id' not in registry[0]['facts'][0] and 'bbox_display_pt' not in registry[0]['facts'][0]
    assert registry[0]['scope']['period_scope_text']==['July 2030 - June 2031']
    pages=client.selection_context['complete_table_page_contexts']
    assert len(pages)==2 and 'Equipment' in pages[0]['complete_native_page_text']
    assert result['citations'][0]['metadata']['fact']['fact_id'] not in keys


def test_plan_cannot_return_original_fact_id_instead_of_short_enum(tmp_path):
    store,client,hits=prepare(tmp_path);original=client.generate
    def invalid(*args,**kwargs):
        plan=original(*args,**kwargs)
        if client.calls==1:plan['fact_ids']=['arbitrary-original-long-fact-id']
        return plan
    client.generate=invalid
    result,trace=route_native_table_question(store,'Formula budget',hits)
    assert result is None and trace['status']=='native_table_selection_invalid'


def ratio_facts(left,right):
    base={'source_sha256':'x','page_no':1,'table_id':'t','unit':'currency_symbol:$',
          'column_header_path':[],'period':None,'scale':None,'row_header':'item'}
    return [{**base,'fact_id':'a','raw_value':'$'+left},{**base,'fact_id':'b','raw_value':'$'+right}]


def test_ratio_exact_finite_decimal_and_no_physical_authorization():
    result=annotation_arithmetic(ratio_facts('7.5','2'),'ratio')
    assert result['answer']=='3.75' and result['unit']=='ratio'
    assert result['physical_calculator_input_eligible'] is False
    assert result['operands']==['$7.5','$2']


@pytest.mark.parametrize('denominator,code',[('0','zero_denominator'),('3','nonterminating_decimal')])
def test_ratio_never_rounds_or_divides_by_zero(denominator,code):
    with pytest.raises(ValueError,match=code):annotation_arithmetic(ratio_facts('1',denominator),'ratio')


def test_sum_exponent_gap_keeps_last_small_fraction_under_short_global_context():
    from decimal import localcontext
    tiny='0.'+'0'*40+'1'
    with localcontext() as context:
        context.prec=2
        result=annotation_arithmetic(ratio_facts('1',tiny),'sum')
    assert result['numeric_result']=='1.'+'0'*40+'1'


def test_sum_carry_and_cancellation_keep_every_original_decimal_place():
    from itertools import permutations
    giant='9'*50
    tiny='0.'+'0'*40+'1'
    facts=ratio_facts(giant,'-'+giant)
    facts.append({**facts[0],'fact_id':'c','raw_value':'$'+tiny})
    for ordered in permutations(facts):
        assert annotation_arithmetic(list(ordered),'sum')['numeric_result']==tiny
    assert annotation_arithmetic(ratio_facts(giant,'1'),'sum')['numeric_result']=='1'+'0'*50


@pytest.mark.parametrize('literal',['-12345678901234567890.'+'0'*40+'1','0.'+'0'*45])
def test_lookup_numeric_result_is_literal_exact_even_with_short_context(literal):
    from decimal import localcontext
    with localcontext() as context:
        context.prec=2
        result=annotation_arithmetic(ratio_facts(literal,'1')[:1],'lookup')
    assert result['numeric_result']==literal and result['answer']=='$'+literal


def test_ratio_order_requires_independent_scope_approval(tmp_path):
    store,client,hits=prepare(tmp_path,'reject');original=client.generate
    def ratio(*args,**kwargs):
        response=original(*args,**kwargs)
        if client.calls==1:
            response['operation']='ratio'
            # The identical ratio of 1000.10/2000.20 is finite; reversal yields
            # a different exact number and MUST still receive independent review.
            response['fact_ids'].reverse()
        return response
    client.generate=ratio
    result,trace=route_native_table_question(store,'Formula to Diapers budget ratio',hits)
    assert result is None and trace['status']=='native_table_semantic_scope_review_rejected'
    assert trace['model_requests_attempted']==2


def test_whole_page_context_budget_refuses_truncation(tmp_path,monkeypatch):
    store,client,hits=prepare(tmp_path)
    original=fitz.Page.get_text
    def verbose(page,*args,**kwargs):
        if args and args[0]=='text':return 'Original entity and scope '*300
        return original(page,*args,**kwargs)
    monkeypatch.setattr(fitz.Page,'get_text',verbose)
    result,trace=route_native_table_question(store,'Formula budget',hits)
    assert result['clarification_code']=='native_table_complete_page_context_budget_exceeded'
    assert client.calls==0
