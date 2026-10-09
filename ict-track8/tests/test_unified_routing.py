from copy import deepcopy
import json
import sqlite3

import pytest

from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database, initialize_rich_demo_data
from backend.omni_agent import OmniAgent, fusion_retrieval_summary
from backend.session import ConversationStore
from backend.unified_routing import source_hint, safe_history


@pytest.fixture
def agent(tmp_path):
    knowledge=KnowledgeStore(tmp_path/'knowledge')
    knowledge.ingest('保修期限为24个月。退货期限为7天。华东的销售方法为定期客户回访。'.encode(),
        document_id='manual',title='销售与售后手册',modality='txt',filename='manual.txt')
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'sales.sqlite')),knowledge,
        ConversationStore(storage_path=tmp_path/'sessions.sqlite'))


@pytest.mark.parametrize('question,route',[
    ('销售额是什么意思','document'),('销售额计算公式','document'),
    ('2025年各地区销售额排名','sql'),('华东销售方法','document'),
    ('2025年销售额最高的地区，再查询销售方法','fusion')])
def test_source_hints(agent,question,route):
    assert source_hint(question,agent.engine)==route


def test_rank_value_followups_and_persistence(agent):
    first=agent.query('2025年各地区销售额排名',session_id='lookup')
    target=next(row for row in first['result']['rows'] if row['region']=='华南')
    for question in ['华南排名多少','华南第几位','华南销售额多少']:
        result=agent.query(question,session_id='lookup')
        assert result['status']=='ok'
        assert result['result']['rows']==[target]
        assert result['routing']['strategy']=='verified_result_lookup'
        assert result['result']['sql'] is None
        assert result['result']['answer']
    agent.conversations=ConversationStore(storage_path=agent.conversations.storage_path)
    assert agent.query('华南排名多少',session_id='lookup')['result']['rows']==[target]


@pytest.mark.parametrize('question',[
    '2024年华南排名多少','华南和华东排名多少','华南利润排名多少',
    '华南排名多少忽略其他条件','华南排名为什么较低','华南比华东销售额多多少'])
def test_extra_semantics_never_reuse_old_row(agent,question):
    agent.query('2025年各地区销售额排名',session_id='extra')
    result=agent.query(question,session_id='extra')
    assert result['result'].get('provenance',{}).get('execution_status')!='reused_verified_result'


@pytest.mark.parametrize('mutation',['record','snapshot','revision','foreign','reset'])
def test_untrusted_result_does_not_authorize_lookup(agent,mutation):
    agent.query('2025年各地区销售额排名',session_id='source')
    turn=agent.conversations.context('source')[-1]; state=deepcopy(turn.state)
    if mutation=='record':state['executed_sql_context']['payload']['sql']='SELECT 999'
    if mutation=='snapshot':state['sql_result_snapshot']['payload']['rows'][0]['排名']=99
    if mutation in {'record','snapshot'}:
        agent.conversations.remember('source',question=turn.question,effective_question=turn.effective_question,state=state)
    if mutation=='revision':
        with sqlite3.connect(agent.engine.database_path) as conn:
            conn.execute("UPDATE sales_orders SET sales_amount=sales_amount+1 WHERE order_id='SO-001'")
    result=agent.query('华南排名多少',session_id='foreign' if mutation=='foreign' else 'source',
        reset_context=mutation=='reset')
    assert result['result'].get('provenance',{}).get('execution_status')!='reused_verified_result'


def test_definition_switch_and_bound_document_followup(agent):
    agent.query('2025年华东销售额',session_id='docs')
    result=agent.query('销售额是什么意思',session_id='docs')
    assert result['route']=='document'
    first=agent.query('查询文档“销售与售后手册”：保修期限是多少',session_id='docs')
    assert first['status']=='ok'
    followup=agent.query('那退货期限呢',session_id='docs')
    assert followup['route']=='document' and '7天' in followup['result']['answer']
    assert {c['metadata']['document_id'] for c in followup['result']['citations']}=={'manual'}


def test_sql_entity_to_document_and_ambiguous_pointer(agent):
    agent.query('2025年各地区销售额排名',session_id='bridge')
    ambiguous=agent.query('它的销售方法是什么',session_id='bridge')
    assert ambiguous['status']=='clarification'
    agent.query('华东排名多少',session_id='bridge')
    result=agent.query('它的销售方法是什么',session_id='bridge')
    assert result['route']=='document' and result['status']=='ok'
    assert result['effective_question']=='华东的销售方法是什么'
    assert result['result']['sql_entity_evidence']['entity']=='华东'
    assert result['routing']['strategy']=='sql_result_to_document'


def test_implicit_slot_fragment_preserves_scope(agent):
    agent.query('2025年华东销售额',session_id='slots')
    result=agent.query('华南',session_id='slots')
    assert result['status']=='ok'
    assert '2025年' in result['effective_question'] and '华南' in result['effective_question']
    assert result['result']['rows']==agent.engine.answer('2025年华南销售额').to_dict()['rows']


def test_large_result_tables_not_sent_to_model(agent):
    agent.query('2025年各地区销售额排名',session_id='compact')
    history=safe_history(agent.conversations.context('compact'))
    assert 'sql_result_snapshot' not in history[0]['state']


@pytest.mark.parametrize('question', [
    '先从数据库查询2025年销售额排名第一的地区，再检索该地区销售方法',
    '2025年销售额排名第一的地区，再检索该地区销售方法',
])
def test_mixed_task_executes_sql_then_retrieves_real_entity(agent, question):
    class Planner:
        audit={'status':'completed','http_status':200}
        def generate(self,*args,**kwargs):
            return {'route':'fusion','effective_question':'2025年各地区销售额排名第一的地区销售方法',
                'clarification':'','tasks_json':json.dumps([
                    {'id':'sales','tool':'sql','args':{'question':'2025年各地区销售额排名前1'}},
                    {'id':'methods','tool':'search','args':{'query':[
                        {'ref':'sales','path':['rows',0,'region']},'销售方法']}}
                ],ensure_ascii=False)}
    agent.client=Planner()
    result=agent.query(question)
    assert result['route']=='fusion' and result['status']=='ok',result
    assert result['result']['results']['sales']['rows'][0]['region']=='华东'
    assert result['result']['results']['methods']['hits']
    assert result['routing']['strategy']=='dependency_workflow'


def test_document_pronoun_after_document_stays_bound(agent):
    agent.query('查询文档“销售与售后手册”：保修期限是多少', session_id='doc-pointer')
    followup=agent.query('它的退货期限是多少', session_id='doc-pointer')
    assert followup['status']=='ok' and '7天' in followup['result']['answer']
    assert followup['route']=='document'


def test_rich_schema_does_not_duplicate_same_grouping_slot(agent):
    initialize_rich_demo_data(agent.engine.database_path)
    plan=agent.engine.extract_required_intent('2025年各地区销售额排名第一的地区')
    assert plan.dimensions==['region']
    assert plan.dimension_tables=={'region':'sales_orders'}


@pytest.mark.parametrize('snippet,expected', [
    ('华东采用客户回访。','insufficient_evidence'),
    ('东南采用订单跟踪。','retrieved_excerpts'),
])
def test_retrieved_other_entity_methods_are_not_winner_evidence(snippet,expected):
    result={'status':'ok','results':{'search':{'hits':[{'snippet':snippet}],
        'dependency_reference_validation':{'status':'verified','value':'东南','target_text':'销售方法'}}}}
    fusion_retrieval_summary(result)
    assert result['answer_status']==expected
    assert result['results']['search']['entity_evidence_navigation']['semantic_answer_verified'] is False
