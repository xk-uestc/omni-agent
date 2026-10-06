"""Extrema are server comparisons; model selection must still pass full review."""
from copy import deepcopy
from types import SimpleNamespace
import hashlib
import fitz
import pytest
from backend.native_table_question import annotation_arithmetic, _operation_request_supported, route_native_table_question, REVIEW
from backend.native_text_tables import extract_native_text_tables
from backend.knowledge_store import KnowledgeStore

def original(values=('$3.00','$5.00','$5.00')):
    with fitz.open() as doc:
        p=doc.new_page();p.insert_text((40,40),'Budget for FY 2041')
        for i,(name,value) in enumerate(zip(('North','West','East'),values)):
            p.insert_text((40,100+i*30),name);p.insert_text((300,100+i*30),value)
        return doc.tobytes()

def facts(values=('$3.00','$5.00','$5.00')):
    raw=original(values)
    return extract_native_text_tables(raw,page_no=1,expected_source_sha256=hashlib.sha256(raw).hexdigest())['tables'][0]['facts']

@pytest.mark.parametrize('op,values,winners,number',[
    ('argmax',('$3.00','$5.00','$5.00'),['West','East'],'5.00'),
    ('argmin',('$3.00','$5.00','$5.00'),['North'],'3.00'),
    ('argmax',('$-8.00','$-3.00','$-6.00'),['West'],'-3.00'),
    ('argmin',('$-8.00','$-3.00','$-6.00'),['North'],'-8.00'),
    ('argmax',('$0.00','$0.00','$0.00'),['North','West','East'],'0.00'),
])
def test_extrema_exact_values_negative_zero_and_all_ties(op,values,winners,number):
    selected=facts(values);result=annotation_arithmetic(selected,op)
    assert result['winner_labels']==winners and result['numeric_result']==number
    assert result['answer']==' | '.join(winners) and result['comparison_candidate_count']==3
    assert result['winner_fact_ids']==[f['fact_id'] for f in selected if f['row_header'] in winners]

def test_comparison_scope_mutations_and_duplicate_rows_reject():
    for mutate in (lambda f:f[1].update(table_id='other'),lambda f:f[1].update(source_sha256='a'*64),
                   lambda f:f[1].update(period='2042'),lambda f:f[1].update(column_header_path=['other']),
                   lambda f:f[1].update(row_header=f[0]['row_header']),lambda f:f[1].update(currency='USD')):
        selected=deepcopy(facts());mutate(selected)
        with pytest.raises(ValueError):annotation_arithmetic(selected,'argmax')
    with pytest.raises(ValueError):annotation_arithmetic(facts()[:1],'argmax')

def test_named_subset_compares_only_selected_candidates_not_unrequested_outlier():
    selected=facts(('$9.00','$5.00','$6.00'))[1:]
    assert annotation_arithmetic(selected,'argmax')['winner_labels']==['East']

@pytest.mark.parametrize('question',[
    'According to the financial report for FY 2008, did profit increase in 2008 compared to 2007, and by how much?',
    'Based on the report, did profit increase in 2008 compared to 2007, and by how much?',
])
def test_explicit_source_preamble_preserves_boolean_classification(question):
    assert _operation_request_supported('increase_check',question)
    assert not _operation_request_supported('decrease_check',question)
    assert not _operation_request_supported('increase_check',question.replace('did profit increase','did profit not increase'))

def test_entity_extremum_cannot_be_a_literal_value_lookup():
    q='Which counterparty posted the highest amount?'
    assert _operation_request_supported('argmax',q)
    assert not _operation_request_supported('lookup',q)
    assert not _operation_request_supported('argmin',q)
    assert not _operation_request_supported('argmax',q+' Explain why.')
    assert not _operation_request_supported('argmax',q+' And by how much?')

@pytest.mark.parametrize('approved',[True,False])
def test_production_selection_complete_independent_review_and_literal_replay(tmp_path,approved):
    store=KnowledgeStore(tmp_path/'knowledge');store.ingest(original(),document_id='budget',title='Budget',modality='pdf',filename='budget.pdf')
    class Client:
        model='gpt-6-luna';reasoning='medium';audit={};calls=0
        def generate(self,instructions,context,schema,**kwargs):
            self.calls+=1;self.audit={'status':'completed','http_status':200,'model_verified':True,'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
            if self.calls==1:
                return {'abstain':False,'operation':'argmax','fact_ids':[f['selection_id'] for t in context['native_table_registry'] for f in t['facts']]}
            assert context['server_annotation_computation']['winner_labels']==['West','East']
            return {key:approved for key in REVIEW['properties']}
    client=Client();store.generator=SimpleNamespace(client=client)
    result,trace=route_native_table_question(store,'Which units have the highest budget for FY 2041 among North, West and East? Include all ties.',[SimpleNamespace(metadata={'document_id':'budget'})])
    if approved:
        assert result['answer']=='West | East' and len(result['citations'])==3
    else:
        assert result is None and trace['status']=='native_table_semantic_scope_review_rejected'
    assert client.calls==2
