"""HTTP/SSE wiring test; deterministic fake planner, not a real-model score."""
import json
from fastapi.testclient import TestClient
from .test_task_experience import env,confirm,QUESTION,TASKS
from backend.memory.experience import ExperienceSelector
from backend.session import ConversationStore


def test_http_sse_share_server_owned_experience_and_new_history(env,monkeypatch):
    import backend.app as app
    adapter,authority=env;_,review=confirm(adapter,authority)
    seen=[]
    class Planner:
        audit={'status':'completed','http_status':200}
        def generate(self,instructions,context,schema,**kwargs):
            seen.append(context)
            return {'route':'fusion','effective_question':context['question'],'tasks_json':json.dumps(TASKS,ensure_ascii=False),'clarification':''}
    for key,value in [('engine',adapter.engine),('knowledge_store',adapter.knowledge),('memory_adapter',adapter),
                      ('experience_selector',ExperienceSelector(adapter)),('generation_client',Planner()),('conversation_store',ConversationStore())]:
        monkeypatch.setattr(app,key,value)
    client=TestClient(app.app)
    body={'question':QUESTION,'session_id':'http-new','experience_id':'forged','scope':{'project':'attacker'}}
    response=client.post('/api/v1/omni/query',json=body)
    assert response.status_code==200
    normal=response.json()
    stream=client.post('/api/v1/omni/query/stream',json={**body,'session_id':'sse-new'})
    done=json.loads(next(b.splitlines()[1][6:] for b in stream.text.split('\n\n') if b.startswith('event: done')))
    assert normal['status']==done['status']=='ok'
    assert normal['context_turns']==done['context_turns']==0
    assert normal['result']['results']['c']['value']==done['result']['results']['c']['value']
    assert all(c['task_experience']['memory_id']==review['memory_id'] for c in seen)
    decisions=[e for e in adapter.core.store.events() if e.get('kind')=='task_experience_decision']
    assert len(decisions)==2 and all(e['feedback']['independent_task_verified'] is False for e in decisions)
