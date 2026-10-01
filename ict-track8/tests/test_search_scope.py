import fitz
import pytest

from backend.dependency_agent import DependencyAgent, DependencyPlanError
from backend.knowledge_store import KnowledgeStore


@pytest.fixture
def agent(tmp_path):
    store=KnowledgeStore(tmp_path/'knowledge')
    for name,hours in [('service-playbook',2),('other',99)]:
        store.ingest(f'紧急工单首次响应要求为{hours}小时。'.encode(),document_id=name,title=name,modality='txt',filename=name+'.txt')
    doc=fitz.open()
    for text in ['Response hours first page.', 'Response hours second page.']:
        doc.new_page().insert_text((40,40),text)
    raw=doc.tobytes();doc.close()
    store.ingest(raw,document_id='pdf-policy',title='Response',modality='pdf',filename='policy.pdf')
    return DependencyAgent(None,store)


def task(args):
    return {'id':'search','tool':'search','args':args}


def test_scope_is_accepted_in_planning_and_preserved_in_execution(agent):
    args={'query':'紧急工单首次响应小时数及其要求','document_id':'service-playbook'}
    agent.validate([task(args)])
    result=agent.execute('search',args,args,{})
    assert {h['metadata']['document_id'] for h in result['hits']}=={'service-playbook'}
    assert result['search_scope']['document_id']=='service-playbook'
    assert result['search_scope']['source_sha256']==agent.knowledge_store.document('service-playbook')['sha256']


def test_pdf_page_scope_is_passed_to_store(agent):
    args={'query':'Response hours','document_id':'pdf-policy','page_no':2}
    agent.validate([task(args)])
    result=agent.execute('search',args,args,{})
    assert result['hits'] and all(h['metadata']['page_no']==2 for h in result['hits'])
    assert result['search_scope']['mode']=='explicit_document_page'


@pytest.mark.parametrize('extra',[
    {'document_id':'missing'}, {'document_id':'../escape'}, {'document_id':None},
    {'document_id':{'ref':'x','path':['value']}}, {'document_id':['service-playbook']},
    {'page_no':1}, {'document_id':'service-playbook','page_no':1},
    {'document_id':'pdf-policy','page_no':True}, {'document_id':'pdf-policy','page_no':0},
    {'document_id':'pdf-policy','page_no':3}, {'document_id':'pdf-policy','page_no':'2'},
    {'document_id':'pdf-policy','page_no':None}, {'top_k':20}, {'query':{}},
    {'query':['Response',float('nan')]}, {'query':['Response',float('inf')]},
])
def test_invalid_scope_is_rejected_before_and_during_execution(agent,extra):
    args={'query':'Response',**extra}
    with pytest.raises(DependencyPlanError): agent.validate([task(args)])
    with pytest.raises(DependencyPlanError): agent.execute('search',args,args,{})


def test_explicit_scope_no_hits_does_not_fallback(agent):
    args={'query':'zzzzzzunrelated','document_id':'service-playbook'}
    with pytest.raises(DependencyPlanError,match='没有文档证据'):
        agent.execute('search',args,args,{})


def test_foreign_source_hit_is_rejected(agent,monkeypatch):
    hit=agent.knowledge_store.search('紧急工单',document_id='other')[0]
    monkeypatch.setattr(agent.knowledge_store,'search',lambda *args,**kwargs:[hit])
    args={'query':'紧急工单','document_id':'service-playbook'}
    with pytest.raises(DependencyPlanError,match='scope_mismatch'):
        agent.execute('search',args,args,{})


def test_wrong_page_hit_is_rejected(agent,monkeypatch):
    hit=agent.knowledge_store.search('Response',document_id='pdf-policy',page_no=1)[0]
    monkeypatch.setattr(agent.knowledge_store,'search',lambda *args,**kwargs:[hit])
    args={'query':'Response','document_id':'pdf-policy','page_no':2}
    with pytest.raises(DependencyPlanError,match='scope_mismatch'):
        agent.execute('search',args,args,{})


def test_source_changes_between_lookup_and_search_are_rejected(agent,monkeypatch):
    original=agent.knowledge_store.search
    def changed(*args,**kwargs):
        agent.knowledge_store.ingest('紧急工单首次响应要求变更为8小时。'.encode(),document_id='service-playbook',title='Updated',modality='txt',filename='a.txt')
        return original(*args,**kwargs)
    monkeypatch.setattr(agent.knowledge_store,'search',changed)
    args={'query':'紧急工单','document_id':'service-playbook'}
    with pytest.raises(DependencyPlanError,match='scope_mismatch'):
        agent.execute('search',args,args,{})


def test_literal_source_does_not_remove_query_reference_dependencies(agent):
    tasks=[{'id':'prior','tool':'document_fact','args':{'document_id':'service-playbook','label':'label'}},
           task({'query':['首次响应',{'ref':'prior','path':['value']}],'document_id':'service-playbook'})]
    ordered,dependencies=agent.validate(tasks)
    assert dependencies['search']=={'prior'}
    with pytest.raises(DependencyPlanError):
        agent.execute('search',tasks[1]['args'],tasks[1]['args'],{})
