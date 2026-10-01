import json

import pytest

from backend.plan_requirements import requested_operations, completion_errors
from backend.omni_agent import OmniAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.session import ConversationStore


QUESTION='比较政策历史版本2022-12-31与2023-01-01的维修期限是否变化'


def tasks():
    return [
        {'id':'old','tool':'policy_select','args':{'document_id':'policy','as_of':'2022-12-31','label':'维修期限'}},
        {'id':'new','tool':'policy_select','args':{'document_id':'policy','as_of':'2023-01-01','label':'维修期限'}},
        {'id':'result','tool':'compare','args':{'left':{'ref':'old','path':[]},'right':{'ref':'new','path':[]}}}]


def setup_agent(tmp_path, planner):
    store=KnowledgeStore(tmp_path/'knowledge')
    store.ingest('2022版维修期限为3日,生效区间为2022-01-01至2022-12-31。\n2023版维修期限为6日,自2023-01-01起生效。'.encode(),
        document_id='policy',title='维修政策历史',modality='txt',filename='policy.txt')
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'sales.sqlite')),store,ConversationStore(),planner)


class Planner:
    def __init__(self, responses, *, status=200):
        self.responses=responses
        self.calls=[]
        self.audit={'status':'completed','http_status':status}
    def generate(self,instructions,context,schema,**kwargs):
        self.calls.append({'context':context,'operation':kwargs['name']})
        return self.responses[min(len(self.calls)-1,len(self.responses)-1)]


def plan(route, execution_tasks=None):
    return {'route':route,'effective_question':QUESTION,'clarification':'','tasks_json':json.dumps(execution_tasks or [])}


def test_missing_version_comparison_is_corrected_once_and_actually_executes(tmp_path):
    planner=Planner([plan('document'),plan('fusion',tasks())])
    result=setup_agent(tmp_path,planner).query(QUESTION)
    assert result['status']=='ok' and result['route']=='fusion' and result['planner_source']=='model_validated'
    final=result['result']['results']['result']
    assert final['status']=='different' and final['left']['value']==3 and final['right']['value']==6
    assert final['unit']=='日'
    assert len(planner.calls)==2
    assert planner.calls[1]['operation']=='omni_plan_completion_repair'
    assert 'plan_completion_feedback' in planner.calls[1]['context']
    assert [a['validation'] for a in result['trace'][0]['attempts']]==['requested_operation_missing','complete']


def test_second_incomplete_plan_stops_with_clarification_not_plain_document_success(tmp_path):
    planner=Planner([plan('document')])
    result=setup_agent(tmp_path,planner).query(QUESTION)
    assert result['status']=='clarification' and result['route']=='clarify'
    assert len(planner.calls)==2 and result['planner_source']=='rules_fallback'


@pytest.mark.parametrize('status', [401,403,429,503])
def test_unsuccessful_provider_audit_never_authorizes_a_correction(tmp_path,status):
    planner=Planner([plan('document')],status=status)
    result=setup_agent(tmp_path,planner).query(QUESTION)
    assert len(planner.calls)==1 and result['status']=='clarification'


def test_correct_plan_is_not_requested_again(tmp_path):
    planner=Planner([plan('fusion',tasks())])
    result=setup_agent(tmp_path,planner).query(QUESTION)
    assert result['status']=='ok' and len(planner.calls)==1


@pytest.mark.parametrize('question', [
    '2022年与2023年销售额同比', '维修政策有效期是什么', '2022-12-31与2023-01-01的销售额分别是多少',
    '政策2022-13-31与2023-01-01比较',
])
def test_unrelated_queries_or_invalid_dates_do_not_get_a_version_workflow(question):
    assert requested_operations(question)['version_comparison_dates']==[]


def test_date_selections_without_actual_comparison_are_not_complete():
    requirements=requested_operations(QUESTION)
    assert completion_errors(requirements,'fusion',tasks()[:-1])==['selected_versions_not_actually_compared']
    wrong=tasks()
    wrong[2]['args']['right']={'ref':'old','path':[]}
    assert completion_errors(requirements,'fusion',wrong)==['selected_versions_not_actually_compared']
    wrong=tasks()
    wrong[1]['args']['as_of']='2024-01-01'
    assert completion_errors(requirements,'fusion',wrong)==['requested_effective_dates_not_selected']


def test_chinese_calendar_dates_use_the_same_effective_date_contract():
    assert requested_operations('对比政策2022年12月31日和2023年1月1日是否变化')['version_comparison_dates']==[
        '2022-12-31','2023-01-01']


@pytest.mark.parametrize('field,value', [('label','退款期限'),('document_id','other_policy')])
def test_different_policy_elements_are_not_complete_and_receive_one_repair(tmp_path,field,value):
    wrong=tasks()
    wrong[1]['args'][field]=value
    assert completion_errors(requested_operations(QUESTION),'fusion',wrong)==[
        'version_comparison_requires_same_policy_element']
    planner=Planner([plan('fusion',wrong),plan('fusion',tasks())])
    result=setup_agent(tmp_path,planner).query(QUESTION)
    assert result['status']=='ok' and len(planner.calls)==2
    assert result['result']['results']['result']['right']['label']=='维修期限'


def test_policy_value_projection_is_not_complete_evidence():
    wrong=tasks()
    wrong[2]['args']['left']['path']=['value']
    assert completion_errors(requested_operations(QUESTION),'fusion',wrong)==[
        'version_comparison_requires_complete_policy_evidence']
