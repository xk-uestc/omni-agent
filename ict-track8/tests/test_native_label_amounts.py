"""Native scatter annotations compare explicit categories, never guessed pixels."""
from copy import deepcopy
from types import SimpleNamespace

import fitz
import pytest

from backend.knowledge_store import KnowledgeStore
from backend.native_table_question import annotation_arithmetic, route_native_table_question, REVIEW
from backend.native_text_tables import extract_native_text_tables


def original(rotation=0,duplicate=False,missing_comma=False):
    with fitz.open() as doc:
        p=doc.new_page();p.insert_text((40,40),'Budget allocation diagram, FY 2044')
        for x,y,label,value in [(45,100,'North Division,','$13.8'),(295,190,'West Division,','$48.4'),
                                 (70,300,'East Division,','$48.4')]:
            if missing_comma:label=label.rstrip(',')
            if duplicate:label='North Division,'
            a,b=label.split(' ',1)
            p.insert_text((x,y),a);p.insert_text((x,y+14),b);p.insert_text((x+10,y+28),value)
        p.set_rotation(rotation)
        return doc.tobytes()


@pytest.mark.parametrize('rotation',[0,90,180,270])
def test_wrapped_scatter_labels_native_exact_pairs_and_display_boxes(rotation):
    raw=original(rotation);report=extract_native_text_tables(raw,page_no=1)
    table=next(t for t in report['tables'] if t.get('table_kind')=='explicit_inline_label_collection_not_grid')
    assert table['complete_scope']=='all_detected_explicit_pairs_on_page_not_exhaustive_chart'
    rows=table['facts'];assert [f['row_header'] for f in rows]==['North Division','West Division','East Division']
    assert all(f['scale'] is None and f['currency']=='unknown' for f in rows)
    assert annotation_arithmetic(rows,'argmax')['answer']=='West Division | East Division'
    with fitz.open(stream=raw,filetype='pdf') as doc:
        page=doc[0]
        for f in rows:
            assert any(list(fitz.Rect(w[:4])*page.rotation_matrix)==f['bbox_display_pt'] and w[4]==f['raw_value'] for w in page.get_text('words'))
    assert extract_native_text_tables(raw,page_no=1)==report


@pytest.mark.parametrize('options',[{'missing_comma':True},{'duplicate':True}])
def test_no_inferred_relation_without_explicit_separator_or_unique_labels(options):
    r=extract_native_text_tables(original(**options),page_no=1)
    assert not any(t.get('table_kind')=='explicit_inline_label_collection_not_grid' for t in r['tables'])


@pytest.mark.parametrize('mode',['approved','wrong_chart','amount_request'])
def test_inline_production_comparison_requires_independent_whole_page_review(tmp_path,mode):
    store=KnowledgeStore(tmp_path/'knowledge');store.ingest(original(),document_id='chart',title='Budget chart',modality='pdf',filename='c.pdf')
    class Client:
        model='gpt-6-luna';reasoning='medium';audit={};calls=0
        def generate(self,instructions,context,schema,**kwargs):
            self.calls+=1;self.audit={'status':'completed','http_status':200,'model_verified':True,'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
            if self.calls==1:
                rows=context['native_table_registry'][0]['facts'];ids=[f['selection_id'] for f in rows]
                return {'abstain':False,'operation':'sum' if mode=='amount_request' else 'argmax','fact_ids':ids}
            assert len(context['complete_table_page_contexts'])==1
            assert all('inline_pair_proof' in f for f in context['all_native_table_candidates'][0]['facts'])
            return {key:mode=='approved' for key in REVIEW['properties']}
    store.generator=SimpleNamespace(client=Client())
    q='Which category has the highest budget among North Division, West Division and East Division?'
    if mode=='amount_request':q='What is the total budget for North Division, West Division and East Division?'
    result,trace=route_native_table_question(store,q,[SimpleNamespace(metadata={'document_id':'chart'})])
    if mode=='approved':assert result['answer']=='West Division | East Division'
    else:assert result is None and trace['status'] in {'native_table_semantic_scope_review_rejected','no_native_aligned_tables'}


def test_inline_chart_candidates_cannot_divert_scaled_amount_lookup(tmp_path):
    with fitz.open(stream=original(),filetype='pdf') as doc:
        p=doc[0]
        p.insert_text((40,500),'Department');p.insert_text((300,500),'USD million')
        for i,(label,value) in enumerate([('Office','22.4'),('Manufacturing','7.8'),('Research','2.6')]):
            p.insert_text((40,525+i*25),label)
            p.insert_text((360-fitz.get_text_length(value,fontsize=11),525+i*25),value)
        raw=doc.tobytes()
    store=KnowledgeStore(tmp_path/'knowledge');store.ingest(raw,document_id='mixed',title='Mixed report',modality='pdf',filename='m.pdf')
    class Client:
        model='gpt-6-luna';reasoning='medium';audit={};calls=0
        def generate(self,instructions,context,schema,**kwargs):
            self.calls+=1;self.audit={'status':'completed','http_status':200,'model_verified':True,'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
            if self.calls==1:
                rows=[f for t in context['native_table_registry'] for f in t['facts']]
                assert all(f['value_kind']!='native_inline_label_amount_literal' for f in rows)
                assert 'North' in context['complete_table_page_contexts'][0]['complete_native_page_text']
                selected=next(f for f in rows if f['row_label']=='Office')
                return {'abstain':False,'operation':'lookup','fact_ids':[selected['selection_id']]}
            return {key:True for key in REVIEW['properties']}
    store.generator=SimpleNamespace(client=Client())
    result,trace=route_native_table_question(store,'What is the Office amount in the mixed report?',
        [SimpleNamespace(metadata={'document_id':'mixed'})])
    assert trace['excluded_inline_comparison_only_tables']==1
    assert result['answer']=='22.4 USD million' and result['answer_scope']['unit']=='currency:USD'
