from copy import deepcopy
from types import SimpleNamespace
import fitz
import pytest
from backend.evidence_scope import page_scope
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.source_answer_dossier import build_dossier
from backend.native_row_selection import bind_selection
from backend.native_row_registry import extract_rows
def source():
    with fitz.open() as document:
        for pno in range(2):
            page = document.new_page(width=700, height=400)
            xs = [30, 230, 350, 470, 590]
            page.insert_text((30, 35), 'Field record set')
            for x, label in zip(xs, ['Description', 'Result', 'When', 'Operator', 'Method']):
                page.insert_text((x, 70), label, fontsize=9)
            for i in range(3):
                index = pno*3+i
                for x, cell in zip(xs, ['Item '+str(index), '35 UG/L', '2025-01-01',
                                       'PERSON_A', 'ISO_AB' if index == 2 else 'ISO_A']):
                    page.insert_text((x, 86+i*16), cell, fontsize=9)
        return document.tobytes()


SHA='a'*64


def receipt(page): return {'document_id':'d','source_sha256':SHA,'page_no':page}


def test_scanning_all_pages_does_not_mean_all_evidence_supplied():
    declared=[{'document_id':'d','source_sha256':SHA,'pages':[1,2,3]}]
    scope=page_scope(declared,[receipt(i) for i in (1,2,3)],[receipt(1)],required_conditions=['units'])
    assert scope['status']=='incomplete' and len(scope['unsupplied_pages'])==2
    assert scope['unverified_conditions']==['units']
    full=page_scope(declared,[receipt(i) for i in (1,2,3)],[receipt(i) for i in (1,2,3)],required_conditions=['units'],verified_conditions=['units'])
    assert full['status']=='complete_for_declared_scope' and 'not_proved' in full['semantic_sufficiency']


@pytest.mark.parametrize('kwargs,status',[
    ({'ambiguous':True},'ambiguous'),({'source_changed':True},'source_changed'),
    ({'omitted':[{'reason':'scan_budget'}]},'budget_exhausted')])
def test_scope_failure_states(kwargs,status):
    scope=page_scope([{'document_id':'d','source_sha256':SHA,'pages':[1]}],[receipt(1)],[],**kwargs)
    assert scope['status']==status


def test_scope_rejects_wrong_source_and_unscanned_supply():
    declaration=[{'document_id':'d','source_sha256':SHA,'pages':[1]}]
    with pytest.raises(ValueError): page_scope(declaration,[{**receipt(1),'source_sha256':'b'*64}],[])
    with pytest.raises(ValueError): page_scope(declaration,[],[receipt(1)])


def pdf(pages):
    with fitz.open() as d:
        for i in range(pages):
            p=d.new_page();p.insert_text((40,40),f'Inspection {i+1}. Complete native records and units for this page only.')
        return d.tobytes()


def test_long_document_ledger_marks_unscanned_pages_and_budget(tmp_path):
    s=KnowledgeStore(tmp_path);d=s.ingest(pdf(40),document_id='long',title='Record',modality='pdf',filename='r.pdf')
    hits=[SimpleNamespace(metadata={'document_id':'long','page_no':20,'source_sha256':d['sha256']})]
    pages,a=build_dossier(s,'inspection records',hits)
    scope=a['evidence_scope']
    assert len(pages)==3 and len(scope['unscanned_pages'])==37
    assert scope['status']=='incomplete' and len(scope['declared_sources'][0]['pages'])==40
    # Retrieval-selected short source exceeds the 6-page evidence budget.
    s.ingest(pdf(8),document_id='short',title='Record',modality='pdf',filename='s.pdf')
    _,a=build_dossier(s,'inspection records',[SimpleNamespace(metadata={'document_id':'short','page_no':1})])
    assert a['evidence_scope']['status']=='budget_exhausted'


def test_dossier_rejects_stale_hit_before_reading_new_version(tmp_path):
    s=KnowledgeStore(tmp_path);d=s.ingest(pdf(2),document_id='p',title='Record',modality='pdf',filename='r.pdf')
    s.ingest(pdf(3),document_id='p',title='Record',modality='pdf',filename='r.pdf')
    with pytest.raises(SourceRevisionError): build_dossier(s,'inspection',[SimpleNamespace(metadata={'document_id':'p','page_no':1,'source_sha256':d['sha256']})])


def selection(rows):
    return {'abstain':False,'document_id':'sample','chain_index':0,'projection_mode':'whole_fields',
            'predicates':[{'column_index':4,'operator':'contains_token','literal':'ISO_A'}],
            'column_indices':[0,1],'fragments':[]}


def test_cross_page_enumeration_and_duplicate_or_foreign_row_rejected():
    reg=extract_rows(source(),document_id='sample')
    _,rows,projection,status,_=bind_selection('List all matching items',selection(reg['records']),[reg])
    assert status=='ok' and len(rows)==5 and {r['page_no'] for r in rows}=={1,2}
    assert len(projection)==5
    bad=deepcopy(reg);bad['records'][1]['row_id']=bad['records'][0]['row_id']
    with pytest.raises(ValueError,match='duplicate_row'): bind_selection('List all items',selection([]),[bad])
    bad=deepcopy(reg);bad['records'][1]['document_id']='another'
    with pytest.raises(ValueError,match='source_changed'): bind_selection('List all items',selection([]),[bad])
