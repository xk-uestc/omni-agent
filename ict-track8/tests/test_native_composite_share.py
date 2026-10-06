"""Composite shares bind original cells, complete candidates and all roles."""
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
from types import SimpleNamespace

import fitz
import pytest

from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.native_table_question import annotation_arithmetic, route_native_table_question, bind_explicit_composite_addend, _operation_request_supported, REVIEW
from backend.native_text_tables import extract_native_text_tables


def original(values=None):
    values=values or [str(i+1) for i in range(20)]
    with fitz.open() as doc:
        p=doc.new_page(height=1000)
        p.insert_text((40,40),'Equipment budget FY 2045')
        for i,value in enumerate([*values,str(sum(Decimal(v) for v in values))]):
            p.insert_text((40,100+i*25),f'Unit{i}' if i<len(values) else 'Total')
            literal='$'+value
            p.insert_text((340-fitz.get_text_length(literal,fontsize=11),100+i*25),literal)
        return doc.tobytes()


def facts(values=None):
    return extract_native_text_tables(original(values),page_no=1)['tables'][0]['facts']


def test_complete_twenty_member_comparison_and_composite_exact_fraction():
    rows=facts();maximum=annotation_arithmetic(rows[:-1],'argmax')
    assert maximum['winner_labels']==['Unit19'] and maximum['comparison_candidate_count']==20
    plan=[*rows[:-1],rows[6],rows[-1]]
    result=annotation_arithmetic(plan,'max_plus_percentage')
    assert result['numeric_result']=='12.86' and result['answer']=='≈12.86%'
    assert result['exact_fraction']=={'numerator':'90','denominator':'7'}
    assert result['winner_fact_ids']==[rows[19]['fact_id']]
    assert result['distinct_numerator_fact_ids']==[rows[19]['fact_id'],rows[6]['fact_id']]
    assert result['executed_steps']==['argmax','sum','percentage']
    assert result['denominator_fact_id']==rows[-1]['fact_id']


@pytest.mark.parametrize('places',[0,1,2,4,6])
def test_combined_named_component_share_uses_exact_rational_and_bound_precision(places):
    rows=facts();r=annotation_arithmetic([rows[0],rows[1],rows[-1]],'sum_percentage',percentage_decimal_places=places)
    assert Fraction(int(r['exact_fraction']['numerator']),int(r['exact_fraction']['denominator']))==Fraction(10,7)
    assert r['display_decimal_places']==places and r['numerator_sum']['numeric_result']=='3'
    assert r['unit']=='percent' and r['scale'] is None


@pytest.mark.parametrize('mutation',[
    lambda f:f[-1].update(raw_value='$0'),
    lambda f:f[-1].update(table_id='other'),
    lambda f:f[-1].update(source_sha256='other'),
    lambda f:f[-1].update(column_header_path=['other']),
    lambda f:f[-1].update(period='2044'),
    lambda f:f[0].update(raw_value='$999m',scale=None),
])
def test_composite_rejects_zero_denominator_or_unbound_original_scope(mutation):
    rows=deepcopy(facts());mutation(rows)
    with pytest.raises(ValueError):annotation_arithmetic([*rows[:-1],rows[6],rows[-1]],'max_plus_percentage')


def test_tie_winner_addend_and_total_candidate_never_double_count():
    rows=facts(['8','8','2']);plan=[*rows[:-1],rows[2],rows[-1]]
    with pytest.raises(ValueError,match='unique_winner'):annotation_arithmetic(plan,'max_plus_percentage')
    rows=facts(['8','3','2'])
    with pytest.raises(ValueError,match='duplicate_or_total'):annotation_arithmetic([*rows[:-1],rows[0],rows[-1]],'max_plus_percentage')
    rows[-1]['raw_value']='$1'
    with pytest.raises(ValueError,match='duplicate_or_total'):annotation_arithmetic([*rows,rows[1],rows[-1]],'max_plus_percentage')
    with pytest.raises(ValueError,match='distinct_operands'):annotation_arithmetic([rows[1],rows[1],rows[-1]],'sum_percentage')


@pytest.mark.parametrize('mode',['success','omitted_candidate','changed_source','wrong_named_role'])
def test_production_composite_review_complete_set_and_source_replay(tmp_path,mode):
    store=KnowledgeStore(tmp_path/'knowledge');raw=original()
    store.ingest(raw,document_id='budget',title='Equipment budget',modality='pdf',filename='b.pdf')
    class Client:
        model='gpt-6-luna';reasoning='medium';audit={};calls=0
        def generate(self,instructions,context,schema,**kwargs):
            self.calls+=1
            self.audit={'status':'completed','http_status':200,'model_verified':True,'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
            if self.calls==1:
                rows=context['native_table_registry'][0]['facts'];ids=[f['selection_id'] for f in rows]
                candidates=ids[:-1] if mode!='omitted_candidate' else ids[:-2]
                return {'abstain':False,'operation':'max_plus_percentage','fact_ids':[*candidates,ids[-2] if mode=='wrong_named_role' else ids[6],ids[-1]]}
            if mode=='changed_source':store.ingest(original(['9','3','2']),document_id='budget',title='Changed',modality='pdf',filename='b.pdf')
            return {key:mode!='omitted_candidate' for key in REVIEW['properties']}
    store.generator=SimpleNamespace(client=Client())
    args=(store,'What percentage of the equipment budget total for FY 2045 is the combined highest unit and Unit6?',
          [SimpleNamespace(metadata={'document_id':'budget'})])
    if mode=='changed_source':
        with pytest.raises(SourceRevisionError):route_native_table_question(*args)
    else:
        result,trace=route_native_table_question(*args)
        if mode in {'success','wrong_named_role'}:
            assert result['answer']=='≈12.86%' and len(result['citations'])==22
            assert result['trace'][-1]['tool']=='calculate'
            assert result['computation']['comparison']['comparison_candidate_count']==20
            assert trace['explicit_named_addend_binding']['corrected_model_role_reference']==(mode=='wrong_named_role')
        else:assert result is None and trace['status']=='native_table_semantic_scope_review_rejected'


def test_named_addend_original_literal_binding_rejects_missing_ambiguous_and_substrings():
    rows=facts();inventory={f'F{i}':({'document_id':'budget'},row) for i,row in enumerate(rows)}
    plan={'abstain':False,'operation':'max_plus_percentage','fact_ids':[*(f'F{i}' for i in range(20)),'F19','F20']}
    bound,proof=bind_explicit_composite_addend('Highest unit and Unit6 combined share?',plan,inventory)
    assert bound['fact_ids'][-2]=='F6' and plan['fact_ids'][-2]=='F19'
    assert proof['question_literal']=='Unit6' and proof['bound_row_label']=='Unit6'
    for q in ('Highest unit and Unit60 combined share?','Highest unit and Unit6 or Unit7 combined share?',
              'Highest unit and the other unit combined share?','Highest unit and Unit6, repeat Unit6 combined share?'):
        with pytest.raises(ValueError,match='uniquely_literal_bound'):bind_explicit_composite_addend(q,plan,inventory)


def test_share_request_cannot_silently_skip_or_invent_extremum_step():
    q='What percentage of the total is the highest unit and Unit6 combined?'
    assert _operation_request_supported('max_plus_percentage',q)
    assert not _operation_request_supported('sum_percentage',q)
    assert not _operation_request_supported('max_plus_percentage',q.replace('highest','lowest'))
    assert not _operation_request_supported('max_plus_percentage','What percentage of the total is Unit5 and Unit6 combined?')
