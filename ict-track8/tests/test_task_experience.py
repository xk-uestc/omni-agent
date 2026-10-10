"""Real tool execution + independently computed SQL/arithmetics; no model scores."""
from dataclasses import asdict, replace
from datetime import date
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import pytest
from openpyxl import Workbook
from backend.memory.core import MemoryCore,MemoryStore,TrustedScope,encoded
from backend.memory.adapter import MemoryAdapter
from backend.memory.formation import LocalReviewContext,MemoryFormation
from backend.memory.experience import ExperienceFormation,ExperienceSelector
from backend.memory.extraction import digest
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database

QUESTION='根据《指标与计划》目标销售额公式和《区域计划》2026年华东目标增长率，以及数据库2025年华东销售额，计算2026年目标销售额。'
TASKS=[{'id':'f','tool':'document_formula','args':{'document_id':'policy','label':'目标销售额'}},
 {'id':'s','tool':'sql','args':{'question':'2025年华东销售额'}},
 {'id':'g','tool':'document_cell','args':{'document_id':'targets','where':{'地区':'华东','年份':2026},'column':'目标增长率'}},
 {'id':'c','tool':'calculate','args':{'formula':{'ref':'f','path':[]},'parameters':{'基准销售额':{'ref':'s','path':['rows',0,'销售额']},'目标增长率':{'ref':'g','path':[]}}}}]

@pytest.fixture
def env(tmp_path):
    db=initialize_database(tmp_path/'db.sqlite');engine=Nl2SqlEngine(db,reference_date=date(2026,10,9))
    knowledge=KnowledgeStore(tmp_path/'knowledge');knowledge.ingest('目标销售额 = 基准销售额 * (1 + 目标增长率)'.encode(),document_id='policy',title='指标与计划',modality='txt',filename='policy.txt')
    wb=Workbook();wb.active.append(['地区','年份','目标增长率']);wb.active.append(['华东',2026,.12]);wb.active.append(['华南',2026,.10]);raw=BytesIO();wb.save(raw)
    knowledge.ingest(raw.getvalue(),document_id='targets',title='区域计划',modality='xlsx',filename='targets.xlsx')
    scope=TrustedScope('test','project',('database','policy','targets'));store=MemoryStore(tmp_path/'memory.sqlite')
    adapter=MemoryAdapter(MemoryCore(store,scope=scope,enabled=True),engine,knowledge,clock=lambda:'2026-10-10T12:00:00+00:00')
    config=tmp_path/'admin.json';config.write_text(json.dumps({'scope':asdict(scope),'memory_db':str(store.path)}));config.chmod(0o600)
    return adapter,LocalReviewContext.from_config(config)


def independent_verifier(adapter):
    def score(question,tasks,result):
        with sqlite3.connect(adapter.engine.database_path) as db:
            amount=db.execute("SELECT SUM(sales_amount) FROM sales_orders WHERE order_date>='2025-01-01' AND order_date<'2026-01-01' AND region='华东'").fetchone()[0]
        sql=result['results']['s'];calc=result['results']['c'];versions=result['source_validation']['documents']
        valid=(sql['rows'][0]['销售额']==amount and abs(calc['value']-amount*1.12)<.001
               and '华东' in sql['parameters'] and versions=={d['document_id']:d['sha256'] for d in adapter.knowledge.list_documents()}
               and len(result['trace'])==4 and all(t['status']=='complete' for t in result['trace']))
        return dict.fromkeys(('task_success','operation_coverage','result_correct','source_correct'),valid)
    return score


def seed(adapter):
    return ExperienceFormation(adapter).capture_verified_run(QUESTION,TASKS,verifier=independent_verifier(adapter),verifier_id=digest(Path(__file__).read_text()))


def confirm(adapter,authority):
    f=ExperienceFormation(adapter);c=seed(adapter)
    assert f.validate(c['candidate_id'],c['digest'])['valid']
    receipt=f.review(c['candidate_id'],c['digest'],context=authority,request_id='review1',decision='confirm',reason='independent fixture verification')
    return c,receipt


def test_actual_formation_review_reload_and_parameter_free_advice(env):
    adapter,authority=env;c=seed(adapter);selector=ExperienceSelector(adapter)
    assert not selector.select(QUESTION)['selected']
    c,receipt=confirm(adapter,authority)
    adapter.core.store=MemoryStore(adapter.core.store.path)
    decision=ExperienceSelector(adapter).select(QUESTION.replace('2025','2024').replace('华东','华南'))
    assert decision['selected']==[receipt['memory_id']]
    text=encoded(decision['advice'])
    assert all(x not in text for x in ('2025','华东','0.12','SELECT','29584'))
    assert c['binding']=={} and c['experience']['workflow']=='formula_sql'
    assert not MemoryFormation(adapter).candidates()
    assert adapter.prepare('experience_formula_sql')[0]=='experience_formula_sql'


def test_independent_task_failure_never_creates_candidate(env):
    adapter,_=env
    with pytest.raises(ValueError,match='whole-task'):
        ExperienceFormation(adapter).capture_verified_run(QUESTION,TASKS,verifier=lambda *args:{'task_success':True},verifier_id='a'*64)
    assert ExperienceFormation(adapter).candidates()==[]


def test_invalid_graph_or_changed_user_constraints_not_seed(env):
    adapter,_=env
    with pytest.raises(ValueError):
        ExperienceFormation(adapter).capture_verified_run(QUESTION.replace('华东','华南'),TASKS,verifier=independent_verifier(adapter),verifier_id='a'*64)


def test_source_change_and_revoke_refuse_reload(env):
    adapter,authority=env;c,r=confirm(adapter,authority);f=ExperienceFormation(adapter)
    record=adapter.core.store.scan(adapter.core.scope)[0][0]
    f.revoke(record.memory_id,digest(asdict(record)),context=authority,request_id='revoke1',reason='withdrawn')
    assert ExperienceSelector(adapter).select(QUESTION)['selected']==[]
    replay=f.review(c['candidate_id'],c['digest'],context=authority,request_id='review1',decision='confirm',reason='independent fixture verification')
    assert replay==r and not ExperienceSelector(adapter).select(QUESTION)['selected']
    with adapter.core.store.connect() as db:assert db.execute('SELECT COUNT(*) FROM memory_history').fetchone()[0]>=2


@pytest.mark.parametrize('change',['raw_source','schema','trace','record','tool'])
def test_current_validity_rechecked_before_advice(env,change,monkeypatch):
    adapter,authority=env;c,r=confirm(adapter,authority)
    if change=='raw_source':
        path,_=adapter.knowledge.original('policy');path.write_text('changed')
    elif change=='schema':
        with sqlite3.connect(adapter.engine.database_path) as db:db.execute('ALTER TABLE sales_orders ADD COLUMN new_field TEXT')
    elif change=='trace':
        with adapter.core.store.connect() as db:db.execute("UPDATE memory_source_events SET payload='{}'")
    elif change=='record':
        record=adapter.core.store.scan(adapter.core.scope)[0][0];adapter.core.store.put(replace(record,experience={**record.experience,'answer':42}))
    else:
        from backend.dependency_agent import DependencyAgent
        monkeypatch.setattr(DependencyAgent,'TOOLS',DependencyAgent.TOOLS-{'calculate'})
    assert not ExperienceSelector(adapter).select(QUESTION)['selected']


def test_scope_irrelevant_and_illegal_oracle_choice(env):
    adapter,authority=env;confirm(adapter,authority)
    assert not ExperienceSelector(adapter).select('2025年华东销售额')['selected']
    result=ExperienceSelector(adapter).select(QUESTION,choose=lambda legal:'unapproved-id')
    assert result['selected']==[] and result['state']['failure']=='experience_unavailable'
    adapter.core.scope=TrustedScope('test','other-project',('database','policy','targets'))
    assert not ExperienceSelector(adapter).select(QUESTION)['selected']


def test_local_review_required_and_stale_validation_not_approval(env):
    adapter,authority=env;f=ExperienceFormation(adapter);c=seed(adapter);f.validate(c['candidate_id'],c['digest'])
    with pytest.raises(PermissionError):f.review(c['candidate_id'],c['digest'],context='admin',request_id='x',decision='confirm',reason='yes')
    path,_=adapter.knowledge.original('policy');path.write_text('changed')
    with pytest.raises(ValueError,match='promotion refused'):f.review(c['candidate_id'],c['digest'],context=authority,request_id='x',decision='confirm',reason='yes')


def test_planner_receives_request_local_advice_and_generates_new_parameters(env):
    from copy import deepcopy
    from backend.omni_agent import OmniAgent
    from backend.session import ConversationStore
    adapter,authority=env;confirm(adapter,authority)
    calls=[]
    class Planner:
        audit={'status':'completed','http_status':200}
        def generate(self,instructions,context,schema,**kwargs):
            calls.append(deepcopy(context))
            tasks=json.loads(json.dumps(TASKS,ensure_ascii=False).replace('华东','华南').replace('2025','2024'))
            return {'route':'fusion','effective_question':context['question'],'tasks_json':json.dumps(tasks,ensure_ascii=False),'clarification':''}
    agent=OmniAgent(adapter.engine,adapter.knowledge,ConversationStore(),Planner(),experience=ExperienceSelector(adapter))
    result=agent.query(QUESTION.replace('华东','华南').replace('2025','2024'),session_id='new')
    assert result['context_turns']==0 and result['status']=='ok', result
    assert calls[0]['task_experience']['kind']=='advisory_task_experience'
    assert result['result']['results']['s']['parameters']!=['2025','华东']
    assert '华南' in result['result']['results']['s']['parameters']
    assert not agent.conversations.context('unrelated')
    assert not hasattr(adapter.engine,'task_experience')


def test_advice_does_not_bypass_planner_or_authorize_wrong_plan(env):
    from backend.omni_agent import OmniAgent
    from backend.session import ConversationStore
    adapter,authority=env;confirm(adapter,authority)
    no_model=OmniAgent(adapter.engine,adapter.knowledge,ConversationStore(),experience=ExperienceSelector(adapter)).query(QUESTION)
    assert no_model['status']=='clarification'
    class BadPlanner:
        audit={'status':'completed','http_status':200}
        def generate(self,instructions,context,schema,**kwargs):
            return {'route':'fusion','effective_question':context['question'],'tasks_json':json.dumps(TASKS),'clarification':''}
    response=OmniAgent(adapter.engine,adapter.knowledge,ConversationStore(),BadPlanner(),experience=ExperienceSelector(adapter)).query(QUESTION.replace('华东','华南'))
    assert response['status']!='ok'


def test_request_context_does_not_leak_between_calls(env):
    from backend.omni_agent import OmniAgent
    from backend.session import ConversationStore
    adapter,authority=env;confirm(adapter,authority);calls=[]
    class Planner:
        audit={'status':'completed','http_status':200}
        def generate(self,instructions,context,schema,**kwargs):
            calls.append(context)
            return {'route':'clarify','effective_question':context['question'],'tasks_json':'[]','clarification':'test'}
    agent=OmniAgent(adapter.engine,adapter.knowledge,ConversationStore(),Planner(),experience=ExperienceSelector(adapter))
    agent.query(QUESTION);agent.query('请解释资料中的其他方法')
    assert 'task_experience' in calls[0] and 'task_experience' not in calls[-1]
