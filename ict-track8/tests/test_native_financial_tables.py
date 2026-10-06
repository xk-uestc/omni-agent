"""Grouped finance parsing preserves literal scope/sign, never guesses gaps."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
import fitz
import pytest

from backend.native_text_tables import extract_native_text_tables
from backend.native_table_question import annotation_arithmetic, _operation_request_supported, route_native_table_question, REVIEW
from backend.native_table_question import share_registry_evidence, expand_registry_evidence
from backend.knowledge_store import KnowledgeStore


def financial_pdf(*, group=True, unit="RM'000", years=('2008','2007','2008','2007'),
                  profit=('245,721','100,140','23,620','804,944'), rotate=0, overlap=False):
    with fitz.open() as doc:
        p=doc.new_page(width=650,height=550)
        def write(x,y,text):p.insert_text((x,y),text,fontsize=10)
        write(45,40,'Income Statements for the year ended 31 December 2008')
        if group:
            for center,label in [(350,'Group'),(510,'Company')]:
                write(center-fitz.get_text_length(label,fontsize=10)/2,85,label)
        write(230,105,'Note')
        for right,year in zip((320,400,480,560),years):
            write(right-fitz.get_text_length(year,fontsize=10),105,year)
            write(right-fitz.get_text_length(unit,fontsize=10),125,unit)
        rows=[('Revenue',('500,000','400,000','100,000','90,000')),
              ('Cost of sales',('(200,000)','(150,000)','-','-')),
              ('Profit for the year',profit),
              ('Administrative expenses',('(50,000)','(45,000)','(10,000)','(9,000)')),
              ('Profit for the year',profit)]
        for r,(label,values) in enumerate(rows):
            y=155+r*28
            write(45,y,label)
            if r==0:write(237,y,'20')
            for right,value in zip((320,400,480,560),values):
                write(right-fitz.get_text_length(value,fontsize=10),y,value)
            if overlap and r==2:write(304,y,'999')
        p.set_rotation(rotate)
        return doc.tobytes()


def parsed(raw):
    return extract_native_text_tables(raw,page_no=1,expected_source_sha256=hashlib.sha256(raw).hexdigest())


def financial(raw):
    return [t for t in parsed(raw)['tables'] if t.get('table_kind')=='native_grouped_financial_statement']


def profit_facts(raw):
    return [f for f in financial(raw)[0]['facts'] if f['row_header']=='Profit for the year'][:2]


def test_native_group_year_unit_and_notes_all_bind_without_matrix_strings():
    table=financial(financial_pdf())[0]
    facts=table['facts']
    assert len(facts)==18 and len(table['missing_cells'])==2
    assert table['local_geometry_proposal']=='pymupdf_text_strategy_revalidated_native_word_geometry'
    p=[f for f in facts if f['row_header']=='Profit for the year']
    assert p[0]['column_header_path']==['Group','2008',"RM'000"]
    assert p[2]['column_header_path']==['Company','2008',"RM'000"]
    assert p[0]['currency']=='RM' and p[0]['scale']=='1000'
    assert p[0]['native_row_id']!=p[4]['native_row_id']
    assert all(f['calculator_input_eligible'] is False for f in facts)
    assert not any(f['raw_value']=='20' or '20' in f['row_header'] for f in facts)


def test_parentheses_and_missing_dash_keep_original_literals():
    table=financial(financial_pdf())[0]
    negative=next(f for f in table['facts'] if f['raw_value']=='(200,000)')
    assert negative['sign_evidence']['negative'] is True
    assert all(m['meaning']=='missing_or_not_applicable_not_zero' for m in table['missing_cells'])
    assert not any(f['raw_value']=='-' for f in table['facts'])
    result=annotation_arithmetic([negative],'lookup')
    assert result['numeric_result']=='-200000'
    assert result['answer']=="(200,000) RM thousand"


def test_wide_parenthesized_decimals_use_actual_empty_gutters_not_header_midpoints():
    raw=financial_pdf(profit=('(800,000.00)','(700,000.00)','23,620.25','804,944.30'))
    table=financial(raw)[0]
    facts=[f for f in table['facts'] if f['row_header']=='Profit for the year'][:2]
    assert [f['raw_value'] for f in facts]==['(800,000.00)','(700,000.00)']
    assert annotation_arithmetic(facts,'decrease_check')['comparison']['signed_change']=='-100000.00'


@pytest.mark.parametrize('changes',[{'group':False},{'unit':"'000"},{'years':('2008','2008','2008','2007')}])
def test_missing_group_currency_or_unique_year_rejects_financial_binding(changes):
    assert not financial(financial_pdf(**changes))


def test_overlapping_token_row_is_not_a_financial_fact():
    table=financial(financial_pdf(overlap=True))[0]
    assert len([f for f in table['facts'] if f['row_header']=='Profit for the year'])==4


@pytest.mark.parametrize('rotation',[90,180,270])
def test_declared_rotation_maps_financial_boxes_without_changing_value(rotation):
    raw=financial_pdf(rotate=rotation);table=financial(raw)[0]
    with fitz.open(stream=raw,filetype='pdf') as d:
        assert all(fitz.Rect(f['bbox_display_pt']) in d[0].rect for f in table['facts'])
    assert table['facts'][0]['raw_value']=='500,000'


@pytest.mark.parametrize('profit,operation,matched,direction,change',[
    (('245,721','100,140','23,620','804,944'),'increase_check',True,'increased','145581'),
    (('100,140','245,721','23,620','804,944'),'increase_check',False,'decreased','-145581'),
    (('100,140','245,721','23,620','804,944'),'decrease_check',True,'decreased','-145581'),
    (('100,140','100,140','23,620','804,944'),'increase_check',False,'unchanged','0'),
    (('(10,000)','(30,000)','23,620','804,944'),'increase_check',True,'increased','20000'),
])
def test_boolean_and_amount_share_exact_ordered_operands(profit,operation,matched,direction,change):
    result=annotation_arithmetic(profit_facts(financial_pdf(profit=profit)),operation)
    assert result['comparison']['matched'] is matched
    assert result['comparison']['actual_direction']==direction
    assert result['comparison']['signed_change']==change
    assert result['comparison']['target_period']=='2008' and result['comparison']['baseline_period']=='2007'
    assert result['answer'].startswith('Yes' if matched else 'No')


def test_repeated_same_label_different_native_row_cannot_mix_years():
    facts=[f for f in financial(financial_pdf())[0]['facts'] if f['row_header']=='Profit for the year']
    with pytest.raises(ValueError,match='column_period_scope'):
        annotation_arithmetic([facts[0],facts[5]],'increase_check')


def test_group_currency_and_sign_evidence_mutations_are_rejected():
    originals=profit_facts(financial_pdf(profit=('(10,000)','(30,000)','23,620','804,944')))
    for mutate in [lambda f:f[0].pop('sign_evidence'),lambda f:f[0]['sign_evidence'].update(negative=False),
                   lambda f:f[0]['sign_evidence'].update(negative=1),
                   lambda f:f[1]['column_header_path'].__setitem__(0,'Company'),
                   lambda f:f[1].update(currency='USD',unit='currency:USD')]:
        facts=deepcopy(originals);mutate(facts)
        with pytest.raises(ValueError):annotation_arithmetic(facts,'increase_check')


def test_registry_compaction_is_lossless_and_scoped_to_each_table():
    import json
    tables=[]
    for country, currency in [('A',"RM'000"),('B',"USD'000")]:
        original=financial(financial_pdf(unit=currency))[0]
        tables.append({'document_id':country,'facts':[{'selection_id':str(i),
            'raw_value':f['raw_value'],'row_label':f['row_header'],
            **{key:deepcopy(f.get(key)) for key in ('column_header_path','unit','currency','scale',
              'period','period_status','scale_evidence','value_kind')}} for i,f in enumerate(original['facts'])]})
    before=deepcopy(tables)
    compact=share_registry_evidence(tables)
    assert expand_registry_evidence(compact)==before and tables==before
    assert len(json.dumps(compact))<len(json.dumps(before))
    assert compact[0]['column_contexts']['C001']['currency']=='RM'
    assert compact[1]['column_contexts']['C001']['currency']=='USD'
    compact[0]['facts'][0]['column_context_ref']='C999'
    with pytest.raises(KeyError):expand_registry_evidence(compact)


def test_registry_expansion_never_overwrites_a_conflicting_literal():
    data=[{'facts':[{'raw_value':'1','unit':'currency:RM','scale_evidence':None}]}]
    compact=share_registry_evidence(data);compact[0]['facts'][0]['unit']='currency:USD'
    with pytest.raises(ValueError,match='collision'):expand_registry_evidence(compact)


@pytest.mark.parametrize('question',[
    'Did profit not increase, and by how much?',
    'Did profit increase, and why?',
    'Did profit increase, and what was its percentage change?',
    'Profit increased; explain the reasons.',
    '利润是否没有增长，增长多少？',
])
def test_unsupported_polarity_or_explanation_not_approved_as_numeric_change(question):
    assert not _operation_request_supported('increase_check',question)


def test_literal_increase_composite_is_supported():
    assert _operation_request_supported('increase_check',"Did the Group's profit increase in 2008 compared to 2007, and by how much?")
    assert _operation_request_supported('decrease_check','集团利润是否下降，下降多少？')


def test_whole_question_independent_review_and_fresh_source_replay(tmp_path):
    store=KnowledgeStore(tmp_path/'knowledge');raw=financial_pdf()
    store.ingest(raw,document_id='finance',title='Income statement',modality='pdf',filename='finance.pdf')
    class Client:
        model='gpt-6-luna';reasoning='medium';audit={};calls=0
        def generate(self,instructions,context,schema,**kwargs):
            self.calls+=1
            self.audit={'status':'completed','http_status':200,'model_verified':True,'model':self.model,
                        'reasoning':self.reasoning,'response_model':self.model}
            if self.calls==1:
                rows=[f for t in context['native_table_registry'] for f in t['facts']
                      if f['row_label']=='Profit for the year' and f['column_header_path'][0]=='Group'][:2]
                return {'abstain':False,'operation':'increase_check','fact_ids':[f['selection_id'] for f in rows]}
            assert context['server_annotation_computation']['comparison']['matched'] is True
            return {k:True for k in REVIEW['properties']}
    client=Client();store.generator=SimpleNamespace(client=client)
    result,trace=route_native_table_question(store,"Did the Group's profit increase in 2008 compared to 2007, and by how much?",[SimpleNamespace(metadata={'document_id':'finance'})])
    assert result['answer']=='Yes, it increased by 145,581 RM thousand.'
    assert client.calls==2 and result['semantic_review']['whole_question_answered'] is True
    assert [c['metadata']['fact']['raw_value'] for c in result['citations']]==['245,721','100,140']
