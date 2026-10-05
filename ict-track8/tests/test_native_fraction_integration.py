from copy import deepcopy
from types import SimpleNamespace

import fitz
import pytest

from backend.knowledge_store import KnowledgeStore
from backend.native_text_tables import extract_native_text_tables
from backend.native_table_question import annotation_arithmetic, route_native_table_question, REVIEW
from backend.native_fraction import replay_native_fraction_cell


def document(header='Attendance',values=('5/6','6/6','1/6'),labels=('Person A','Person B','Person C')):
    with fitz.open() as pdf:
        page=pdf.new_page()
        page.insert_text((40,35),'Board meetings during financial year 2030')
        page.insert_text((40,65),'Name')
        page.insert_text((250,65),header)
        for i,(label,value) in enumerate(zip(labels,values)):
            page.insert_text((40,95+25*i),label)
            page.insert_text((260,95+25*i),value)
        return pdf.tobytes()


def manifest(**kwargs):
    return extract_native_text_tables(document(**kwargs),page_no=1)


def test_count_fraction_table_has_six_source_bindings_and_replays():
    raw=document(); report=extract_native_text_tables(raw,page_no=1)
    assert len(report['facts'])==3 and len(report['tables'])==1
    fact=report['facts'][0]
    assert fact['value_kind']=='native_count_fraction_literal'
    assert fact['column_header_path']==['Attendance'] and fact['unit']=='count_fraction'
    assert fact['unit_evidence'] is None and fact['scale_evidence'] is None
    assert replay_native_fraction_cell(raw,fact['fraction_proof'])==fact['fraction_proof']
    result=annotation_arithmetic([fact],'fraction_percentage')
    assert result['answer']=='≈83.33%' and result['operands']==['5/6']
    assert result['operand_periods']==[None]


@pytest.mark.parametrize('kwargs',[
    {'header':'Date'}, {'header':'Version'}, {'header':'Odds'}, {'header':'Attendance %'},
    {'header':'Attendance ratio'}, {'header':'Attendance date'},
    {'header':'Attendance USD'}, {'header':'Attendance thousands'},
    {'values':('5/0','6/6','1/6')}, {'values':('7/6','6/6','1/6')},
    {'values':('5/6/2030','6/6/2030','1/6/2030')},
    {'labels':('Same person','Same person','Other')},
])
def test_ambiguous_or_invalid_fraction_tables_never_emit_facts(kwargs):
    assert not manifest(**kwargs)['facts']


@pytest.mark.parametrize('operation', ['lookup','sum','ratio','difference','absolute_difference','percentage'])
def test_decimal_operations_never_accept_fraction_facts(operation):
    selected=manifest()['facts'][:1 if operation=='lookup' else 2]
    with pytest.raises(ValueError): annotation_arithmetic(selected,operation)


def test_fraction_operation_rejects_multiple_mixed_or_forged_operands():
    facts=manifest()['facts']
    with pytest.raises(ValueError): annotation_arithmetic(facts[:2],'fraction_percentage')
    for key,value in [('unit','unknown'),('raw_value','6/6'),('source_sha256','b'*64),
                      ('column_header_path',['Other']),('value_kind','native_aligned_cell_literal')]:
        changed=deepcopy(facts[0]); changed[key]=value
        with pytest.raises(ValueError): annotation_arithmetic([changed],'fraction_percentage')
    mixed=deepcopy(facts[1]); mixed.pop('fraction_proof'); mixed.update(unit='currency_symbol:$',raw_value='$6')
    with pytest.raises(ValueError): annotation_arithmetic([facts[0],mixed],'percentage')


class Client:
    model='gpt-6-luna'; reasoning='medium'
    def __init__(self,approve=True): self.audit={}; self.calls=[]; self.approve=approve
    def generate(self,instructions,context,schema,**options):
        self.calls.append(options['name'])
        self.audit={'status':'completed','http_status':200,'model_verified':True,
            'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
        if options['name']=='native_table_fact_selection':
            fact=context['native_table_registry'][0]['facts'][0]
            assert fact['fraction_proof']['raw_value']=='5/6'
            assert 'fraction_percentage' in instructions
            return {'abstain':False,'operation':'fraction_percentage','fact_ids':[fact['selection_id']]}
        assert context['server_annotation_computation']['operation']=='fraction_percentage'
        self.review_context=context
        return {key:self.approve for key in REVIEW['properties']}


def prepare(tmp_path,approve=True):
    client=Client(approve)
    store=KnowledgeStore(tmp_path/'knowledge',generator=SimpleNamespace(client=client))
    store.ingest(document(),document_id='attendance',title='Attendance',modality='pdf',filename='attendance.pdf')
    return store,client,[SimpleNamespace(metadata={'document_id':'attendance'})]


@pytest.mark.parametrize('approve',[True,False])
def test_fraction_route_requires_independent_whole_question_review(tmp_path,approve):
    store,client,hits=prepare(tmp_path,approve)
    result,trace=route_native_table_question(store,'What percentage of meetings did Person A attend?',hits)
    assert client.calls==['native_table_fact_selection','native_table_independent_scope_review']
    if approve:
        assert result['answer']=='≈83.33%' and len(result['citations'])==1
        assert result['calculator_input_eligible'] is False
    else:
        assert result is None and trace['status']=='native_table_semantic_scope_review_rejected'


def test_fraction_route_precision_bound_to_original_question(tmp_path):
    store,client,hits=prepare(tmp_path)
    result,_=route_native_table_question(store,'Person A attendance percentage, round to one decimal place',hits)
    assert result['answer']=='≈83.3%'
    assert client.review_context['percentage_precision_contract']['decimal_places']==1


@pytest.mark.parametrize('question',[
    'Person A attendance percentage, seven decimal places',
    'Person A attendance percentage, three significant figures',
    'Person A attendance percentage increase',
])
def test_fraction_route_rejects_precision_and_growth_before_review(tmp_path,question):
    store,client,hits=prepare(tmp_path)
    result,_=route_native_table_question(store,question,hits)
    assert result is None and len(client.calls)==1


def test_fraction_route_requires_extra_native_token_replay(tmp_path,monkeypatch):
    import backend.native_table_question as module
    store,client,hits=prepare(tmp_path)
    def rejected(*args,**kwargs): raise ValueError('changed cell geometry')
    monkeypatch.setattr(module,'replay_native_fraction_cell',rejected)
    result,trace=route_native_table_question(store,'Person A attendance percentage',hits)
    assert result is None and trace['status']=='native_table_fraction_literal_proof_replay_failed'
    assert len(client.calls)==2
