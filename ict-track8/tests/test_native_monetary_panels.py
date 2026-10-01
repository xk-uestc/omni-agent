"""Native monetary panels preserve local scope and own printed scale only."""
from copy import deepcopy
import hashlib

import fitz
import pytest

from backend.native_text_tables import extract_native_text_tables
from backend.native_table_question import annotation_arithmetic, route_native_table_question, REVIEW
from backend.knowledge_store import KnowledgeStore
from types import SimpleNamespace


def make_pdf(*,suffix_gap=.5,right_suffix=False,overflow=False,rotation=0):
    doc=fitz.open();page=doc.new_page(width=700,height=600)
    page.insert_text((100,35),'FY 2042 Public Assistance to Example Country',fontsize=10)
    for index,(label,amount) in enumerate([('TOTAL FUNDS:','$25.30'),('DONATED COMMODITIES:','$6.00'),('TOTAL FY 2042 ASSISTANCE:','$31.30')]):
        y=70+index*18
        page.insert_text((100,y),label,fontsize=10)
        page.insert_text((320,y),amount,fontsize=10)
        x=320+fitz.get_text_length(amount,fontsize=10)
        page.insert_text((x+suffix_gap,y),'m',fontsize=10)
    for start,label,suffix in [(40,'Program Alpha',''),(360,'Program Beta','m' if right_suffix else '')]:
        page.insert_text((start,180),label,fontsize=10)
        for row,(name,amount) in enumerate([('Housing','$2.10'),('Training','$3.20'),('Medical','$1.00'),('TOTAL PROGRAM','$6.30')]):
            y=205+row*22
            page.insert_text((start if row<3 else start+125,y),name,fontsize=9)
            page.insert_text((start+235,y),amount+suffix,fontsize=10)
            if overflow and start==360:
                page.insert_text((295,y),'CROSSES PANEL BOUNDARY',fontsize=10)
    page.set_rotation(rotation)
    raw=doc.tobytes();doc.close();return raw


def extract(raw):
    return extract_native_text_tables(raw,page_no=1,expected_source_sha256=hashlib.sha256(raw).hexdigest())


def test_parallel_panels_do_not_merge_labels_and_totals_survive_indent():
    report=extract(make_pdf())
    housing=[f for f in report['facts'] if f['row_header']=='Housing']
    totals=[f for f in report['facts'] if f['row_header']=='TOTAL PROGRAM']
    assert len(housing)==len(totals)==2
    assert housing[0]['table_id']!=housing[1]['table_id']
    assert all(f['scale'] is None for f in housing+totals)
    assert all(f['column_header_path']==[] for f in housing)
    assert housing[0]['bbox_display_pt'][2]<housing[1]['row_header_bbox_display_pt'][0]
    with pytest.raises(ValueError,match='table_scope_mismatch'):
        annotation_arithmetic(housing,'sum')


def test_own_adjacent_native_suffix_binds_summary_not_lower_panels():
    report=extract(make_pdf())
    total=next(f for f in report['facts'] if f['row_header']=='TOTAL FY 2042 ASSISTANCE:')
    assert total['raw_value']=='$31.30m' and total['scale']=='1000000'
    assert total['scale_evidence']['binding']=='own_adjacent_currency_suffix_only'
    result=annotation_arithmetic([total],'lookup')
    assert result['answer']=='$31.30m' and result['numeric_result']=='31.30'
    assert result['scale']=='1000000' and result['physical_calculator_input_eligible'] is False
    assert next(f for f in report['facts'] if f['row_header']=='Housing')['scale'] is None


def test_distant_m_is_not_currency_multiplier():
    report=extract(make_pdf(suffix_gap=12))
    assert not any(f['scale'] for f in report['facts'])
    assert not any(f['raw_value'].endswith('m') for f in report['facts'])


def test_scaled_and_unscaled_operands_cannot_mix():
    report=extract(make_pdf(right_suffix=True))
    housing=[f for f in report['facts'] if f['row_header']=='Housing']
    wrong=deepcopy(housing[1]);wrong['table_id']=housing[0]['table_id']
    with pytest.raises(ValueError,match='scope_mismatch'):
        annotation_arithmetic([housing[0],wrong],'sum')


def test_native_word_crossing_panel_boundary_is_not_silently_truncated():
    report=extract(make_pdf(overflow=True))
    assert not any(f['row_header']=='Housing' for f in report['facts'])
    assert any(f['raw_value']=='$31.30m' for f in report['facts'])


def test_rotated_page_keeps_printed_scale_proof_in_display_coordinates():
    report=extract(make_pdf(rotation=90))
    total=next(f for f in report['facts'] if f['row_header']=='TOTAL FY 2042 ASSISTANCE:')
    assert total['scale_evidence']['bbox_display_pt']==total['bbox_display_pt']
    assert annotation_arithmetic([total],'lookup')['answer']=='$31.30m'


def test_exact_scaled_sum_preserves_own_literal_domain_without_guessing_iso_currency():
    total=next(f for f in extract(make_pdf())['facts'] if f['raw_value']=='$31.30m')
    left=deepcopy(total);left.update(fact_id='contribution-1',row_header='Contribution One')
    right=deepcopy(total);right.update(fact_id='contribution-2',row_header='Contribution Two')
    result=annotation_arithmetic([left,right],'sum')
    assert result['answer']=='$62.60m' and result['numeric_result']=='62.60'
    assert result['scale']=='1000000' and result['physical_calculator_input_eligible'] is False


def test_printed_total_never_added_to_its_component_even_with_same_scale():
    facts=[f for f in extract(make_pdf())['facts'] if f['scale']=='1000000']
    with pytest.raises(ValueError,match='total_components_unsupported'):
        annotation_arithmetic([facts[0],facts[1]],'sum')


@pytest.mark.parametrize('change',[{'scale':'1000'},{'scale_evidence':None},{'raw_value':'$31.30bn'},
                                   {'bbox_display_pt':[0,0,1,1]}])
def test_unproven_or_mutated_scale_rejected(change):
    fact=next(f for f in extract(make_pdf())['facts'] if f['row_header']=='TOTAL FY 2042 ASSISTANCE:')
    fact.update(change)
    with pytest.raises(ValueError,match='scale_unbound'):
        annotation_arithmetic([fact],'lookup')


class LookupClient:
    model='gpt-6-luna';reasoning='medium'
    def __init__(self):self.calls=0;self.audit={}
    def generate(self,instructions,context,schema,**kwargs):
        self.calls+=1
        self.audit={'status':'completed','http_status':200,'model_verified':True,
                    'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
        if self.calls==1:
            self.context=context
            chosen=[f['selection_id'] for t in context['native_table_registry'] for f in t['facts']
                    if f['row_label']=='TOTAL FY 2042 ASSISTANCE:']
            assert len(chosen)==1
            return {'abstain':False,'operation':'lookup','fact_ids':chosen}
        return {key:True for key in REVIEW['properties']}


def test_scaled_total_lookup_has_two_reviews_and_original_source_replay(tmp_path):
    store=KnowledgeStore(tmp_path/'knowledge');raw=make_pdf()
    store.ingest(raw,document_id='native-panels',title='Assistance',modality='pdf',filename='panels.pdf')
    client=LookupClient();store.generator=SimpleNamespace(client=client)
    hits=[SimpleNamespace(metadata={'document_id':'native-panels'})]
    result,trace=route_native_table_question(store,'What is the total FY 2042 assistance budget?',hits)
    assert result['status']=='ok' and result['answer']=='$31.30m'
    assert client.calls==2 and trace['model_requests_attempted']==2
    assert result['answer_scope']['scale']=='1000000' and result['answer_scope']['currency']=='unknown'
    proof=result['citations'][0]['metadata']['fact']
    assert proof['source_sha256']==hashlib.sha256(raw).hexdigest()
    fresh=extract(raw)
    assert proof==next(f for f in fresh['facts'] if f['fact_id']==proof['fact_id'])
