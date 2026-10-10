import pytest
from backend.dependency_agent import DependencyAgent, DependencyPlanError
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.failure_trace import attach, for_code
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


def test_first_planner_protocol_failure_kept_even_when_fallback_succeeds():
    r={'status':'ok','result':{},'trace':[{'stage':'planning','error':'JSONDecodeError','rejection_code':None}]}
    attach(r)
    assert r['failure_trace'][0]['category']=='planner'
    assert r['task_verification']['independent_whole_question_success']=='not_verified_by_status'


@pytest.mark.parametrize('code,category',[
    ('source_missing','source_binding'),('evidence_revision_changed','source_binding'),
    ('parameter_binding_failed','source_binding'),('evidence_missing','retrieval'),
    ('timeout','provider_transport'),('model_provider_failed','provider_transport'),
    ('model_output_protocol','planner'),('tool_contract_failed','executor')])
def test_distinct_failure_categories(code,category):
    assert for_code(code)['category']==category


def test_completed_subtask_does_not_hide_failed_remaining_task(tmp_path):
    engine=Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite'))
    agent=DependencyAgent(engine,KnowledgeStore(tmp_path/'k'))
    tasks=[{'id':'sql','tool':'sql','args':{'question':'2024年华东销售额'}},
           {'id':'missing','tool':'document_formula','args':{'document_id':'absent','label':'目标'}}]
    events=[];r=agent.run(tasks,on_event=events.append)
    assert r['status']=='incomplete' and 'sql' in r['results'] and r['failed_task']=='missing'
    assert r['trace'][-1]['failure']['code']=='source_missing'
    assert r['trace'][-1]['failure']['category']=='source_binding'


def test_invalid_dag_observed_as_planner_failure_and_still_raises(tmp_path):
    agent=DependencyAgent(Nl2SqlEngine(initialize_database(tmp_path/'d.sqlite')),KnowledgeStore(tmp_path/'k'))
    events=[]
    with pytest.raises(DependencyPlanError): agent.run([{'id':'x','tool':'unknown','args':{}}],on_event=events.append)
    assert events[0]['failure']['category']=='planner'


def test_parameter_binding_is_distinct_from_tool_execution(tmp_path):
    agent=DependencyAgent(Nl2SqlEngine(initialize_database(tmp_path/'d.sqlite')),KnowledgeStore(tmp_path/'k'))
    tasks=[{'id':'sql','tool':'sql','args':{'question':'2024年华东销售额'}},
           {'id':'read','tool':'search','args':{'query':{'ref':'sql','path':['missing_column']}}}]
    r=agent.run(tasks)
    assert r['error_code']=='parameter_binding_failed'


def test_trace_not_claiming_independent_task_success_on_offline_sql(tmp_path):
    engine=Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite'))
    agent=OmniAgent(engine,KnowledgeStore(tmp_path/'k'),ConversationStore())
    r=agent.query('2024年华东销售额')
    assert r['status']=='ok' and not r['failure_trace']
    assert r['task_verification']['independent_whole_question_success']=='not_verified_by_status'
