"""Real PDF provenance, rejection and coordinates for visual fallback."""
from copy import deepcopy
from types import SimpleNamespace
import fitz
import pytest

from backend.knowledge_store import KnowledgeStore
from backend.visual_source_answer import locate_parts, route_visual_source_fallback
from backend.visual_evidence import render_pdf_evidence


AUDIT={'status':'completed','model_verified':True,'model':'gpt-6-luna',
    'reasoning':'medium','response_model':'gpt-6-luna','http_status':200}


def pdf():
    with fitz.open() as doc:
        page=doc.new_page(width=400,height=200)
        page.insert_text((40,70),'Iron and Manganese are tested.',fontsize=12)
        return doc.tobytes()


class Client:
    model='gpt-6-luna'
    reasoning='medium'
    def __init__(self,approved=True):
        self.audit=deepcopy(AUDIT);self.calls=[];self.approved=approved
    def generate(self,instructions,context,schema,**options):
        self.calls.append(options['name'])
        images=options['image_attachments']
        assert images and 'native_words' not in str(context)
        if options['name']=='visual_source_literal_selection':
            return {'abstain':False,'parts':[{'evidence_id':images[0].manifest['evidence_id'],
                'quote':'Iron and Manganese are tested.','bbox_normalized':[0.08,0.25,0.65,0.4]}]}
        return {k:self.approved for k in schema['properties']}


def setup(tmp_path,approved=True):
    client=Client(approved)
    store=KnowledgeStore(tmp_path,generator=SimpleNamespace(client=client))
    store.ingest(pdf(),document_id='source',title='Assay methods',modality='pdf',filename='assay.pdf')
    return store,client,store.search('Iron Manganese',document_id='source')


def test_reviewed_quotes_keep_actual_page_and_source_geometry(tmp_path):
    store,client,hits=setup(tmp_path)
    result,trace=route_visual_source_fallback(store,'Which elements are tested?',hits,document_id='source')
    assert result['status']=='ok' and result['calculator_input_eligible'] is False
    assert result['answer']=='Iron and Manganese are tested.'
    locator=result['citations'][0]['metadata']['locator']
    assert locator['page_no']==1 and locator['bbox_display_pt']==[32,50,260,80]
    assert client.calls==['visual_source_literal_selection','visual_source_independent_review']
    assert 'not_formal' in result['visual_source_proof']['semantic_verification']


def test_rejected_partial_visual_answer_does_not_replace_text_result(tmp_path):
    store,client,hits=setup(tmp_path,approved=False)
    result,trace=route_visual_source_fallback(store,'Which elements are tested?',hits)
    assert result is None and trace['status']=='visual_source_not_verified'
    assert len(client.calls)==2


@pytest.mark.parametrize('box',[[0.5,0,0.4,1],[0,0,2,1],[0,0,float('nan'),1]])
def test_invalid_visual_regions_never_authorize_source_quotes(box):
    asset=render_pdf_evidence(pdf(),page_no=1)
    with pytest.raises(ValueError):
        locate_parts({'abstain':False,'parts':[{'evidence_id':asset.manifest['evidence_id'],
            'quote':'Iron','bbox_normalized':box}]},[asset])


def test_visual_fallback_does_not_retry_failed_provider(tmp_path):
    store,client,hits=setup(tmp_path)
    client.audit={**AUDIT,'status':'failed','http_status':401}
    result,trace=route_visual_source_fallback(store,'Which elements are tested?',hits)
    assert result is None and not client.calls


def test_explicit_short_pdf_without_retrievable_words_uses_original_pages(tmp_path):
    store,client,_=setup(tmp_path)
    result,trace=route_visual_source_fallback(store,'Which elements are tested?',[],document_id='source')
    assert result is not None and result['answer_mode']=='visual_source_model_reviewed'
    assert result['citations'][0]['metadata']['page_no']==1


def test_source_replacement_during_visual_review_cannot_publish_stale_quote(tmp_path):
    from backend.knowledge_store import SourceRevisionError
    store,client,hits=setup(tmp_path)
    generate=client.generate
    def replace(*args,**kwargs):
        output=generate(*args,**kwargs)
        store.ingest(b'Replaced source',document_id='source',title='Replacement',modality='txt',filename='new.txt')
        return output
    client.generate=replace
    with pytest.raises(SourceRevisionError):
        route_visual_source_fallback(store,'Which elements are tested?',hits)
