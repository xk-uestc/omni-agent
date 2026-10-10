import json
from fastapi.testclient import TestClient
from .test_memory_adapter import setup
from .test_memory_formation import flow


def test_http_sse_actual_document_events_extract_without_promotion(flow,monkeypatch):
    import backend.app as app
    a,m,f,ctx,_=flow
    for name,obj in [('engine',a.engine),('knowledge_store',a.knowledge),('conversation_store',a.conversations),('memory_adapter',m),('generation_client',None)]:monkeypatch.setattr(app,name,obj)
    client=TestClient(app.app)
    http=client.post('/api/v1/omni/query',json={'question':'云杉额是什么','session_id':'A-http','operator':'admin','confirm':True})
    assert http.status_code==200 and http.json()['memory']['formation']['candidate_ids']
    stream=client.post('/api/v1/omni/query/stream',json={'question':'云杉额是什么','session_id':'A-sse'})
    done=json.loads(next(b.splitlines()[1][6:] for b in stream.text.split('\n\n') if b.startswith('event: done')))
    assert done['memory']['formation']['candidate_ids']==http.json()['memory']['formation']['candidate_ids']
    assert len(f.candidates())==1 and f.candidates()[0]['verification_state']=='candidate'
    r=client.post('/api/v1/omni/query',json={'question':'2025年华东云杉额','session_id':'B-independent'}).json()
    assert not r['memory']['consumed'] and not r['result'].get('sql')
