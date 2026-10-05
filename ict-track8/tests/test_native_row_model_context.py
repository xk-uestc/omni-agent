"""Full-page/field preservation and isolation of model input from source proof."""
from copy import deepcopy
import hashlib
import fitz
from backend.native_row_registry import extract_rows, model_registries, model_rows, model_context_audit


def registry(identifier):
    with fitz.open() as doc:
        for pno in range(2):
            page=doc.new_page(width=700,height=350)
            page.insert_text((30,35),'Record ID: '+identifier,fontsize=9)
            for x,label in zip((30,230,390,550),('Description','Result','Operator','Method')):
                page.insert_text((x,70),label,fontsize=9)
            for index in range(3):
                row=pno*3+index
                values=['Item '+str(row), '<20 UG/L' if row==0 else '* 17.50 UG/L' if row==5 else '35 UG/L',
                    'PERSON_A' if row!=4 else 'PERSON_B', 'METHOD_AB' if row==2 else 'METHOD_A']
                for x,value in zip((30,230,390,550),values):
                    page.insert_text((x,88+index*18),value,fontsize=9)
        return extract_rows(doc.tobytes(),document_id=identifier)


def test_every_source_page_row_column_and_qualifier_survives_in_order():
    originals=[registry('a'),registry('b')]
    projected=model_registries(originals)
    assert len(projected)==2
    for before,after in zip(originals,projected):
        assert after['document_id']==before['document_id']
        assert after['source_sha256']==before['source_sha256']
        assert len(after['pages'])==2 and len(after['records'])==6
        for a,b in zip(before['pages'],after['pages']):
            assert b['text']==a['text'] and b['text_sha256']==a['text_sha256']
            assert hashlib.sha256(b['text'].encode()).hexdigest()==b['text_sha256']
        for a,b in zip(before['records'],after['records']):
            assert b['row_id']==a['row_id'] and b['row_text']==a['row_text']
            assert b['chain_index']==a['chain_index'] and b['page_no']==a['page_no']
            assert len(a['fields'])==len(b['fields'])
            for original,field in zip(a['fields'],b['fields']):
                for key in ('column_index','header','text','numeric_annotation','calculator_input_eligible'):
                    assert field[key]==original[key]
        assert after['records'][0]['fields'][1]['numeric_annotation']['qualifier']=='<'
        assert after['records'][5]['fields'][1]['numeric_annotation']['qualifier']=='*'
        assert after['records'][2]['fields'][3]['text']=='METHOD_AB'
        assert after['records'][4]['fields'][2]['text']=='PERSON_B'


def test_model_projection_cannot_mutate_server_proof_or_other_request():
    originals=[registry('a')];snapshot=deepcopy(originals)
    first=model_registries(originals);second=model_registries(originals)
    first[0]['pages'].clear()
    first[0]['records'][0]['fields'][1]['numeric_annotation']['unit']='invented'
    assert originals==snapshot
    assert second==model_registries(snapshot)
    selected=model_rows(originals[0]['records'][:1])
    selected[0]['fields'][0]['text']='invented'
    assert originals==snapshot


def test_geometry_removed_from_model_only_not_original_source_receipt():
    originals=[registry('a')];projected=model_registries(originals)
    assert originals[0]['pages'][0]['members']
    assert 'members' not in projected[0]['pages'][0]
    assert 'bbox_pt' in originals[0]['records'][0]['fields'][0]
    assert 'bbox_pt' not in projected[0]['records'][0]['fields'][0]
    assert 'header_bbox_pt' not in projected[0]['records'][0]['fields'][0]
    audit=model_context_audit(originals,projected)
    assert audit['sources']==1 and audit['candidate_pages']==2 and audit['candidate_rows']==6
    assert audit['model_registry_characters']<audit['full_registry_characters']/2


def test_geometry_does_not_become_semantic_or_global_closure_approval():
    original=registry('a');projected=model_registries([original])[0]
    assert projected['header_alignment_verified'] is True
    for flag in ('semantic_sample_identity_verified','exhaustive_table_closure_verified','calculator_input_eligible'):
        assert projected[flag] is False
    assert all(f['calculator_input_eligible'] is False for r in projected['records'] for f in r['fields'])
