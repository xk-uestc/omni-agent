"""HTTP and SSE share the server-owned memory boundary."""
import json
import pytest
from fastapi.testclient import TestClient
from .test_memory_adapter import setup, value


@pytest.mark.parametrize('enabled',[False,True])
def test_http_sse_same_execution_and_server_scope(setup,monkeypatch,enabled):
    import backend.app as app
    a,m,add=setup;m.core.enabled=enabled
    for name,obj in [('engine',a.engine),('knowledge_store',a.knowledge),('conversation_store',a.conversations),('memory_adapter',m),('generation_client',None)]:
        monkeypatch.setattr(app,name,obj)
    client=TestClient(app.app)
    payload={'question':'2025年华东晨光额','session_id':'http','scope':{'project':'evil'},'memory_enabled':not enabled}
    normal=client.post('/api/v1/omni/query',json=payload)
    assert normal.status_code==200
    stream=client.post('/api/v1/omni/query/stream',json={**payload,'session_id':'sse'})
    assert stream.status_code==200
    events=[(b.splitlines()[0][7:],json.loads(b.splitlines()[1][6:])) for b in stream.text.split('\n\n') if b.startswith('event: ')]
    assert events[-1][0]=='done'
    r1,r2=normal.json(),events[-1][1]
    assert r1['status']==r2['status'] and r1['result'].get('rows')==r2['result'].get('rows')
    if enabled:
        assert value(r1)==value(r2)==120
        assert r1['memory']['consumed']==r2['memory']['consumed']==['cost']
        assert len(m.core.store.events())==2
        traces=[v for k,v in events if k=='trace']
        assert sum(e['tool']=='memory.recall' for e in traces)==1
        assert sum(e['tool']=='memory.observe' for e in traces)==1
        assert traces[-2]['tool']=='memory.observe' and traces[-1]['tool']=='query.complete'
        assert all(e['verification']['independent_task_verified'] is False for e in m.core.store.events())
    else:
        assert 'memory' not in r1 and 'memory' not in r2 and m.core.store.events()==[]
