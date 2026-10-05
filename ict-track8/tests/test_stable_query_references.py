import sqlite3
from dataclasses import replace
import pytest
from backend.session import ConversationStore,ConversationTurn
from backend.history_reference import ConversationReferenceAgent
from backend.conversation_comparison import parse_request


@pytest.mark.parametrize('persistent',[False,True])
def test_ids_disambiguate_repeated_queries_survive_restart_and_do_not_cross_sessions(tmp_path,persistent):
    path=tmp_path/'sessions.sqlite' if persistent else None
    store=ConversationStore(storage_path=path,max_turns=3)
    for unused in range(2):store.remember('one',question='同一问题',effective_question='同一问题',state={'route':'sql'})
    first,second=[turn.turn_id for turn in store.context('one')]
    assert first!=second
    if persistent:store=ConversationStore(storage_path=path,max_turns=3)
    agent=ConversationReferenceAgent()
    assert agent.select(f'回到SQL查询编号{first}，那华南呢',store.context('one')).history_index==0
    assert agent.select(f'回到SQL查询编号{second}，那华南呢',store.context('one')).history_index==1
    store.remember('two',question='同一问题',effective_question='同一问题',state={'route':'sql'})
    assert agent.select(f'回到SQL查询编号{first}，那华南呢',store.context('two')).reason=='requested_sql_reference_not_available'
    for i in range(3):store.remember('one',question=str(i),effective_question=str(i),state={'route':'sql'})
    assert agent.select(f'回到SQL查询编号{first}，那华南呢',store.context('one')).reason=='requested_sql_reference_not_available'


def test_legacy_database_migration_assigns_ids_once_without_changing_queries(tmp_path):
    path=tmp_path/'legacy.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript('CREATE TABLE conversation_sessions(session_id TEXT PRIMARY KEY,touched_at REAL NOT NULL);'
            'CREATE TABLE conversation_turns(session_id TEXT NOT NULL,sequence INTEGER NOT NULL,question TEXT NOT NULL,'
            'effective_question TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(session_id,sequence));'
            "INSERT INTO conversation_sessions VALUES('one',100);"
            "INSERT INTO conversation_turns VALUES('one',0,'旧问题','旧问题',100);")
    store=ConversationStore(storage_path=path,clock=lambda:100)
    turn=store.context('one')[0]
    assert turn.question=='旧问题' and turn.state=={} and turn.turn_id.startswith('q_')
    again=ConversationStore(storage_path=path,clock=lambda:100).context('one')[0]
    assert again==turn


def test_document_id_invalid_id_and_duplicate_id_never_pick_an_unrelated_query():
    agent=ConversationReferenceAgent();identifier='q_'+'a'*32
    document=ConversationTurn('document','document',0,{'route':'document'},identifier)
    assert agent.select(f'回到SQL查询编号{identifier}，那华南呢',[document]).reason=='requested_sql_reference_not_available'
    sql=replace(document,state={'route':'sql'})
    assert agent.select(f'回到SQL查询编号{identifier}，那华南呢',[sql,sql]).reason=='requested_sql_reference_ambiguous'
    assert agent.select('回到SQL查询编号q_short，那华南呢',[sql]).reason=='requested_sql_reference_expression_unsupported'


def test_comparison_id_references_share_selection_and_reject_malformed_ids():
    first='q_'+'a'*32;second='q_'+'b'*32
    request=parse_request(f'比较SQL查询编号{first}和{second}')
    assert [ref.turn_id for ref in request.references]==[first,second]
    assert parse_request('比较SQL查询编号q_bad和q_bad').reference_error


@pytest.mark.parametrize('persistent',[False,True])
def test_expiration_and_reset_never_reassign_an_old_id_to_a_new_query(tmp_path,persistent):
    now=[100.]
    store=ConversationStore(storage_path=tmp_path/'ttl.sqlite' if persistent else None,
        ttl_seconds=10,clock=lambda:now[0])
    store.remember('one',question='旧问题',effective_question='旧问题',state={'route':'sql'})
    old=store.context('one')[0].turn_id
    now[0]=111
    store.remember('one',question='新问题',effective_question='新问题',state={'route':'sql'})
    assert store.context('one')[0].turn_id!=old
    assert ConversationReferenceAgent().select(f'回到SQL查询编号{old}，那华南呢',store.context('one')).reason=='requested_sql_reference_not_available'
    current=store.context('one')[0].turn_id
    store.clear('one')
    store.remember('one',question='新问题',effective_question='新问题',state={'route':'sql'})
    assert store.context('one')[0].turn_id!=current
