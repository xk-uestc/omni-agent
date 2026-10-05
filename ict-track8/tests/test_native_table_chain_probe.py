"""Independent inputs test navigation only; no semantic promotion allowed."""
from pathlib import Path
import sys

import fitz
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from probe_native_table_continuation import probe
from backend.native_table_chain import page_context
from backend.answer_contract import native_source_only_contract_valid


def source(*,changed_header=False,shift=0,intermediate=False,duplicate=False):
    with fitz.open() as document:
        for index in range(2):
            if index and intermediate:
                document.new_page().insert_text((40,40),'Different report, unrelated content.')
            page=document.new_page(width=650,height=720)
            page.insert_text((40,40),f'Specimen X-{index+1}. Qualifications differ by page.')
            for start in ([90,300] if duplicate else [90]):
                for x,value in zip([40,220,365,510],
                    ['Parameter','Result','Operator','Other' if changed_header and index else 'Protocol']):
                    page.insert_text((x+(shift if index else 0),start),value,fontsize=10)
                for row in range(3):
                    for x,value in zip([40,220,365,510],[f'Label-{index}-{row}',f'<{row+7} mg/L','Avery','Procedure Q']):
                        page.insert_text((x+(shift if index else 0),start+24+row*24),value,fontsize=10)
                page.insert_text((40,start+110),'These figures are provisional. Do not combine specimens.')
        return document.tobytes()


def test_chain_keeps_each_page_scope_and_does_not_authorize_sample_merge():
    result=probe(source())
    assert len(result['candidates'])==1 and result['model_calls']==0
    candidate=result['candidates'][0]
    assert not candidate['semantic_sample_identity_verified']
    assert not candidate['calculator_input_eligible']
    assert not candidate['exhaustive_table_closure_verified']
    for index,page in enumerate(candidate['pages']):
        assert f'Specimen X-{index+1}' in page['text']
        assert 'Do not combine specimens.' in page['text']
        assert f'Label-{index}-2' in page['text']
        assert all(member['lines'] for member in page['members'])


@pytest.mark.parametrize('options',[{'changed_header':True},{'shift':20},{'intermediate':True},{'duplicate':True}])
def test_changed_competing_or_nonadjacent_tables_not_linked(options):
    assert not probe(source(**options))['candidates']


def test_budget_limits_never_return_a_shortened_chain():
    assert not probe(source(),max_pages=1)['candidates']
    assert not probe(source(),max_chars=100)['candidates']


def test_page_context_pins_one_page_and_retains_negative_scope():
    result=page_context(source(),1,'Label-0-2',3200)
    assert result and native_source_only_contract_valid(result)
    assert 'Specimen X-1' in result['text'] and 'Do not combine specimens.' in result['text']
    assert 'Label-1-0' not in result['text']
    assert page_context(source(),1,'Nonexistent anchor',3200) is None
    result['table_chain']['semantic_sample_identity_verified']=True
    assert not native_source_only_contract_valid(result)


def test_sibling_navigation_uses_literal_header_not_reordered_block_number(tmp_path):
    from backend.knowledge_store import KnowledgeStore
    from backend.cross_source import DocumentHit
    store=KnowledgeStore(tmp_path/'knowledge')
    store.ingest(source(),document_id='chain',title='Two specimens',modality='pdf',filename='chain.pdf')
    header=next(r for r in store.records() if r.metadata['page_no']==2 and r.content.startswith('Parameter'))
    hit=DocumentHit(header.document_id,header.title,1,(),header.content,header.source_uri,header.metadata).to_dict()
    expanded,trace=store._table_chain_citations([{'citation_id':1,**hit}])
    assert trace['added_pages']==[{'document_id':'chain','page_no':1}]
    assert len(expanded)==2
    assert expanded[1]['snippet'].startswith('Parameter')
    assert expanded[1]['metadata']['navigation_only']=='native_repeated_header_sibling_page'
    assert store._table_chain_citations(expanded)[1]['added_pages']==[]
