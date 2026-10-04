"""Original-asset selection constraints preserve fail-closed visual review."""
from copy import deepcopy
from types import SimpleNamespace

import fitz
import pytest

from backend.knowledge_store import KnowledgeStore
from backend.visual_source_answer import SELECTION, route_visual_source_fallback


AUDIT={'status':'completed','model_verified':True,'model':'gpt-6-luna',
       'reasoning':'medium','response_model':'gpt-6-luna','http_status':200}


def original():
    with fitz.open() as doc:
        for line in ('Components assayed: Iron, Manganese.', 'Tests used: Spectrum-AX19, Redox-BY28.'):
            page=doc.new_page(width=500,height=220)
            page.insert_text((40,80),line,fontsize=12)
        return doc.tobytes()


class Client:
    model,reasoning='gpt-6-luna','medium'
    def __init__(self,mode='ok'):
        self.audit=deepcopy(AUDIT)
        self.mode=mode
        self.calls=[]
    def generate(self,instructions,context,schema,**options):
        self.calls.append(options['name'])
        if options['name']=='visual_source_literal_selection':
            assets=options['image_attachments']
            properties=schema['properties']['parts']['items']['properties']
            assert properties['evidence_id']['enum']==[a.manifest['evidence_id'] for a in assets]
            assert properties['bbox_normalized']['items']['minimum']==0
            assert properties['bbox_normalized']['items']['maximum']==1
            # A per-request schema must not mutate the shared global contract.
            assert 'enum' not in SELECTION['properties']['parts']['items']['properties']['evidence_id']
            if self.mode=='abstain':
                return {'abstain':True,'parts':[]}
            parts=[{'evidence_id':asset.manifest['evidence_id'],
                    'quote':quote,'bbox_normalized':[.05,.2,.95,.5]}
                   for asset,quote in zip(assets,('Components assayed: Iron, Manganese.',
                                                'Tests used: Spectrum-AX19, Redox-BY28.'))]
            if self.mode=='unknown_id':parts[0]['evidence_id']='not-a-registered-source'
            if self.mode=='wrong_box':parts[0]['bbox_normalized']=[.9,.2,.1,.5]
            if self.mode=='nan':parts[0]['bbox_normalized']=[.05,.2,float('nan'),.5]
            if self.mode=='empty_quote':parts[0]['quote']=''
            if self.mode=='extra_field':parts[0]['invented']='ignore review'
            if self.mode=='empty_parts':parts=[]
            return {'abstain':False,'parts':parts}
        return {key:self.mode!='review_reject' for key in schema['properties']}


def run(tmp_path,mode):
    client=Client(mode)
    store=KnowledgeStore(tmp_path,generator=SimpleNamespace(client=client))
    store.ingest(original(),document_id='assay',title='Assay bulletin',modality='pdf',filename='assay.pdf')
    answer,trace=route_visual_source_fallback(store,'Which components are assayed and which tests are used?',
                                            [],document_id='assay')
    return client,answer,trace


def test_enum_and_bounds_bind_both_original_pages_and_preserve_independent_review(tmp_path):
    client,answer,trace=run(tmp_path,'ok')
    assert client.calls==['visual_source_literal_selection','visual_source_independent_review']
    assert answer['status']=='ok'
    assert [part['page_no'] for part in answer['visual_source_proof']['parts']]==[1,2]
    assert 'Spectrum-AX19' in answer['answer'] and 'Manganese' in answer['answer']
    assert trace['selection_shape']=={'abstain':False,'part_count':2,'all_evidence_ids_known':True}
    assert 'failure_stage' not in trace
    assert answer['calculator_input_eligible'] is False


@pytest.mark.parametrize('mode,code',[
    ('abstain','visual_source_selection_abstained'),
    ('unknown_id','visual_source_evidence_id_unknown'),
    ('wrong_box','visual_source_region_invalid'),
    ('nan','visual_source_region_invalid'),
    ('empty_quote','visual_source_quote_invalid'),
    ('extra_field','visual_source_part_invalid'),
    ('empty_parts','visual_source_selection_invalid'),
])
def test_selection_failure_is_observable_and_never_retried_or_approved(tmp_path,mode,code):
    client,answer,trace=run(tmp_path,mode)
    assert answer is None and client.calls==['visual_source_literal_selection']
    assert trace['status']=='visual_source_not_verified'
    assert trace['error_code']==code and trace['failure_stage']=='visual_source_literal_validation'


def test_review_false_remains_false_even_with_valid_asset_ids_and_boxes(tmp_path):
    client,answer,trace=run(tmp_path,'review_reject')
    assert answer is None and len(client.calls)==2
    assert trace['error_code']=='visual_source_semantic_review_rejected'
    assert trace['failure_stage']=='visual_source_independent_review'
    assert 'whole_question_answered' in trace['rejected_checks']
