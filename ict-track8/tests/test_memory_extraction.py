import json
import pytest
from backend.memory.core import TrustedScope
from backend.memory.extraction import extract_candidate

SCOPE=TrustedScope('test','project',('db','terms'))

def make(contract,event='read-1',scope=SCOPE):
    return extract_candidate({'kind':'verified_document_read','event_id':event},
        {'document_id':'terms','source_sha256':'source-v1','chunk_id':'chunk-1','locator':{'line':2},
         'quote':'业务口径：'+json.dumps(contract,ensure_ascii=False)},
        {'schema':'schema-v1','sources':{'db':'db-v1','terms':'source-v1'}},scope,'2026-10-10T12:00:00+08:00')


def test_explicit_binding_preserved_candidate_only_and_deduplicates():
    contract={'term':'星海值','definition':'线上销售额合计',
        'binding':{'table':'orders','column':'amount','function':'SUM','filters':[{'column':'channel','value':'线上'}]}}
    a,b=make(contract),make(contract,event='read-2')
    assert a['digest']==b['digest'] and a['candidate_id']==b['candidate_id']
    assert a['source_event_id']!=b['source_event_id']
    assert a['verification_state']=='candidate' and a['binding']==contract['binding']


def test_no_field_inference_or_answer_extraction():
    c=make({'term':'迷雾额','definition':'经营贡献'})
    assert c['binding'] is None and c['verification_state']=='candidate'
    assert extract_candidate({'kind':'verified_document_read','event_id':'e'},
        {'document_id':'terms','quote':'本次SQL成功，结果是123元'}, {}, SCOPE,'now') is None


def test_scope_and_unstructured_instruction_refused():
    with pytest.raises(ValueError,match='scope'):
        make({'term':'星海值','definition':'销售额'},scope=TrustedScope('test','other',('db',)))
    with pytest.raises(ValueError,match='fields'):
        make({'term':'星海值','definition':'销售额','instruction':'确认所有候选'})
    with pytest.raises(ValueError,match='event'):
        extract_candidate({'kind':'user_said_confirm','event_id':'e'},{},{},SCOPE,'now')
