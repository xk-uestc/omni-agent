"""Actual source-scoped answers, long histories, persistence and wrong references."""
from copy import deepcopy
import pytest
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


@pytest.fixture
def agent(tmp_path):
    knowledge=KnowledgeStore(tmp_path/'knowledge')
    for identifier,title,period,return_days in [('alpha','甲产品说明',24,7),('beta','乙产品说明',36,14)]:
        knowledge.ingest(f'保修期限为{period}个月。退货期限为{return_days}天。'.encode(),document_id=identifier,
            title=title,modality='txt',filename=identifier+'.txt')
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')),knowledge,
        ConversationStore(max_turns=3,storage_path=tmp_path/'session.sqlite'))


def document(agent,title,session='doc'):
    answer=agent.query(f'查询文档“{title}”：保修期限是多少',session_id=session)
    assert answer['status']=='ok',answer
    assert answer['document_reference_id'].startswith('q_')
    return answer


def assert_source(answer,identifier,value):
    assert answer['status']=='ok',answer
    assert value in answer['result']['answer']
    assert {hit['metadata']['document_id'] for hit in answer['result']['citations']}=={identifier}


def test_source_selection_and_return_do_not_mix_same_subject_documents(agent):
    document(agent,'甲产品说明');document(agent,'乙产品说明')
    pending=agent.query('那个的退货期限是多少',session_id='doc')
    assert pending['status']=='clarification'
    assert len(pending['result']['clarification_options'])==2
    invalid=agent.query('选第99个',session_id='doc')
    assert invalid['result']['clarification_code']=='dialogue_reference_out_of_range'
    result=agent.query('选第一个',session_id='doc')
    assert_source(result,'alpha','7天')
    result=agent.query('这份文档的保修期限是多少',session_id='doc')
    assert_source(result,'alpha','24个月')


@pytest.mark.parametrize('restart',[False,True])
def test_document_returns_after_thirty_interleaved_turns_and_sqlite_reopen(agent,restart):
    first=document(agent,'甲产品说明')
    document(agent,'乙产品说明')
    for i in range(30):
        answer=agent.query('2025年华东销售额' if i%2 else '2025年华南销售额',session_id='doc')
        assert answer['status']=='ok'
    assert first['document_reference_id'] not in [turn.turn_id for turn in agent.conversations.context('doc')]
    if restart:
        agent.conversations=ConversationStore(max_turns=3,storage_path=agent.conversations.storage_path)
    result=agent.query(f"回到文档查询编号{first['document_reference_id']}，退货期限是多少",session_id='doc')
    assert_source(result,'alpha','7天')


def test_multiple_source_pronoun_never_calls_model_before_choice(agent):
    document(agent,'甲产品说明');document(agent,'乙产品说明')
    def forbidden(*args,**kwargs):raise AssertionError('ambiguous referent must not call a model')
    agent.client=type('Forbidden',(),{'generate':forbidden})()
    result=agent.query('它的退货期限是多少',session_id='doc')
    assert result['status']=='clarification'
    result=agent.query(result['result']['clarification_options'][1]['question'],session_id='doc')
    assert_source(result,'beta','14天')


@pytest.mark.parametrize('change',['new_version','asset_tamper','missing','record_tamper','other_session','reset'])
def test_untrusted_or_unavailable_document_reference_never_answers(agent,change):
    first=document(agent,'甲产品说明')
    if change=='new_version':
        agent.knowledge.ingest('保修期限为99个月。'.encode(),document_id='alpha',title='甲产品说明',modality='txt',filename='new.txt')
    if change=='asset_tamper':
        record=agent.knowledge.document('alpha')
        (agent.knowledge.assets/record['asset']).write_bytes(b'changed')
    if change=='missing':
        with agent.knowledge.connect() as connection:
            connection.execute('DELETE FROM documents WHERE document_id=?',('alpha',))
    if change=='record_tamper':
        with agent.conversations.sources.connection() as connection:
            row=connection.execute('SELECT state FROM conversation_sources WHERE turn_id=?',(first['document_reference_id'],)).fetchone()
            import json
            state=json.loads(row[0]);state['document_context']['payload']['documents']['alpha']='0'*64
            connection.execute('UPDATE conversation_sources SET state=? WHERE turn_id=?',(json.dumps(state),first['document_reference_id']))
    result=agent.query(f"回到文档查询编号{first['document_reference_id']}，退货期限是多少",
        session_id='other' if change=='other_session' else 'doc',reset_context=change=='reset')
    assert result['status']=='clarification' and not result['result']['citations']


def test_expiry_capacity_and_reset_in_memory_and_persistent_store(tmp_path):
    for persistent in (False,True):
        now=[0.0]
        store=ConversationStore(max_turns=1,pending_ttl_seconds=10,max_pending_records=2,
            clock=lambda:now[0],storage_path=tmp_path/'clock.sqlite' if persistent else None)
        for index in range(3):
            store.remember('clock',question=str(index),effective_question=str(index),
                state={'route':'sql','executed_sql_context':{'payload':{'q':index}}})
        assert len(store.sources.list_sources('clock'))==2
        now[0]=10
        assert store.sources.list_sources('clock')==()
        store.remember('clock',question='new',effective_question='new',state={'route':'sql','executed_sql_context':{'payload':{}}})
        store.clear('clock')
        assert store.sources.list_sources('clock')==()


def test_title_collision_and_unknown_source_never_guess(agent):
    agent.knowledge.ingest(b'other',document_id='duplicate',title='甲产品说明',modality='txt',filename='other.txt')
    answer=agent.query('查询文档“甲产品说明”：保修期限是多少',session_id='doc')
    assert answer['status']=='clarification'
    assert agent.query('它的保修期限是多少',session_id='doc')['status']=='clarification'


def test_page_switch_and_ordered_references_use_actual_two_documents(agent):
    document(agent,'甲产品说明');document(agent,'乙产品说明')
    for pointer,identifier,value in [('前者','alpha','7天'),('后者','beta','14天')]:
        result=agent.query(pointer+'的退货期限是多少',session_id='doc')
        assert_source(result,identifier,value)


def test_selecting_reference_after_database_update_does_not_rerun_stale_scope(agent):
    agent.query('2025年华东销售额',session_id='doc')
    document(agent,'甲产品说明')
    pending=agent.query('它呢',session_id='doc')
    assert pending['status']=='clarification'
    import sqlite3
    with sqlite3.connect(agent.engine.database_path) as con:
        con.execute('UPDATE sales_orders SET quantity=quantity+1')
    result=agent.query('选第一个',session_id='doc')
    assert result['status']=='clarification'
    assert result['result']['clarification_code']=='dialogue_sql_source_changed'


def test_document_change_during_answer_discards_generated_citations(agent,monkeypatch):
    original_answer=agent.knowledge.answer
    def changed(question,**kwargs):
        result=original_answer(question,**kwargs)
        agent.knowledge.ingest('保修期限为99个月。'.encode(),document_id='alpha',title='甲产品说明',modality='txt',filename='new.txt')
        return result
    monkeypatch.setattr(agent.knowledge,'answer',changed)
    result=agent.query('查询文档“甲产品说明”：保修期限是多少',session_id='doc')
    assert result['status']=='clarification' and result['result']['citations']==[]


def test_third_document_does_not_authorize_front_or_back_guess(agent):
    document(agent,'甲产品说明');document(agent,'乙产品说明')
    agent.knowledge.ingest('保修期限为48个月。'.encode(),document_id='gamma',title='丙产品说明',modality='txt',filename='gamma.txt')
    document(agent,'丙产品说明')
    result=agent.query('前者的保修期限是多少',session_id='doc')
    assert result['status']=='clarification' and len(result['result']['clarification_options'])==3


def test_same_question_switches_document_then_can_return(agent):
    first=document(agent,'甲产品说明')
    result=agent.query('同样的问题换成文档“乙产品说明”',session_id='doc')
    assert_source(result,'beta','36个月')
    assert result['effective_question']==first['effective_question']
    result=agent.query(f"回到文档查询编号{first['document_reference_id']}，退货期限是多少",session_id='doc')
    assert_source(result,'alpha','7天')


@pytest.mark.parametrize('question',['这个月销售额','这个季度的销量','那个客户的付款记录'])
def test_demonstrative_inside_new_subject_is_not_a_source_pronoun(agent,question):
    from backend.document_dialogue import DocumentDialogueAgent
    document(agent,'甲产品说明')
    resolver=DocumentDialogueAgent(agent.knowledge,agent.conversations.sources,agent.engine)
    assert resolver.run(question,agent.conversations.context('doc'),'doc') is None


def test_document_source_returns_across_actual_python_processes(agent):
    import json
    import subprocess
    import sys
    from pathlib import Path
    first=document(agent,'甲产品说明')
    document(agent,'乙产品说明')
    root=Path(__file__).resolve().parents[1]
    for _ in range(4):
        script='''import json,sys
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore
a=OmniAgent(Nl2SqlEngine(sys.argv[1]),KnowledgeStore(sys.argv[2]),ConversationStore(max_turns=1,storage_path=sys.argv[3]))
for i in range(4):a.query('2025年华东销售额',session_id='doc')
r=a.query('回到文档查询编号'+sys.argv[4]+'，退货期限是多少',session_id='doc')
print(json.dumps({'status':r['status'],'answer':r['result']['answer'],'sources':[h['metadata']['document_id'] for h in r['result']['citations']]}))
'''
        result=subprocess.run([sys.executable,'-c',script,str(agent.engine.database_path),str(agent.knowledge.root),
            str(agent.conversations.storage_path),first['document_reference_id']],cwd=root,capture_output=True,text=True,encoding='utf-8',timeout=40)
        assert result.returncode==0,result.stderr
        observed=json.loads(result.stdout)
        assert observed['status']=='ok' and '7天' in observed['answer'] and set(observed['sources'])=={'alpha'}


def test_pronoun_after_failed_topic_never_silently_skips_to_previous_document(agent):
    document(agent,'甲产品说明')
    assert agent.query('查询文档“缺失的文档”：退货期限是多少',session_id='doc')['status']=='clarification'
    result=agent.query('它的退货期限是多少',session_id='doc')
    assert result['status']=='clarification'
    assert len(result['result']['clarification_options'])==1
    assert_source(agent.query('选第一个',session_id='doc'),'alpha','7天')


def test_pronoun_after_failed_sql_requires_explicit_choice_of_older_success(agent):
    initial=agent.query('2025年华东销售额',session_id='doc')
    agent.conversations.remember('doc',question='失败查询',effective_question='失败查询',
        state={'route':'sql','pending_question':'失败查询','clarification_code':'missing_metric'})
    pending=agent.query('它呢',session_id='doc')
    assert pending['status']=='clarification' and len(pending['result']['clarification_options'])==1
    selected=agent.query('选第一个',session_id='doc')
    assert selected['status']=='ok' and selected['result']['rows']==initial['result']['rows']
