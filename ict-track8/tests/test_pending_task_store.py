"""Long-lived snapshots do not become implicit chat or execution authority."""
import pytest
from backend.session import ConversationStore
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent


@pytest.fixture(params=['memory', 'sqlite'])
def setup(request, tmp_path):
    now = [100.0]
    options = dict(clock=lambda: now[0], ttl_seconds=10, pending_ttl_seconds=100, max_pending_records=4)
    if request.param == 'sqlite':
        options['storage_path'] = tmp_path/'sessions.sqlite'
    store = ConversationStore(**options)
    agent = OmniAgent(Nl2SqlEngine(initialize_database(tmp_path/'db.sqlite')), KnowledgeStore(tmp_path/'knowledge'), store)
    return agent, now, options


def pending(agent):
    return agent.query('2025年华东销售额趋势', session_id='long')['query_reference_id']


def resume(agent, identifier, suffix=''):
    return agent.query(f'继续待补查询编号{identifier}{suffix}', session_id='long')


def test_twenty_intervening_turns_and_independent_history_ttl(setup):
    agent, now, _ = setup
    identifier = pending(agent)
    for index in range(20):
        agent.conversations.remember('long', question=str(index), effective_question=str(index), state={'route':'document'})
    assert len(agent.conversations.context('long')) == 8
    now[0] += 11
    assert agent.conversations.context('long') == ()
    response = resume(agent, identifier, '，按月统计')
    assert response['status'] == 'ok'
    assert response['result']['rows'] == agent.engine.answer('2025年华东销售额趋势 按月').to_dict()['rows']


def test_pending_ttl_is_not_extended_by_restore(setup):
    agent, now, _ = setup
    identifier = pending(agent)
    now[0] += 90
    restored = resume(agent, identifier)
    assert restored['status'] == 'clarification'
    now[0] += 10
    assert resume(agent, restored['query_reference_id'])['result']['clarification_code'] == 'pending_reference_unavailable'


def test_completion_closes_original_and_edit_and_restore_aliases(setup):
    agent, now, _ = setup
    identifier = pending(agent)
    edited = agent.query('时间改成2024年', session_id='long')['query_reference_id']
    restored = resume(agent, identifier)['query_reference_id']
    completed = agent.query('按月统计', session_id='long')
    assert completed['status'] == 'ok'
    for alias in [identifier, edited, restored]:
        assert resume(agent, alias)['result']['clarification_code'] == 'pending_reference_completed'


def test_independent_success_does_not_close_other_pending(setup):
    agent, _, _ = setup
    identifier = pending(agent)
    agent.query('2024年华南订单数', session_id='long')
    assert resume(agent, identifier)['effective_question'] == '2025年华东销售额趋势'


def test_snapshot_not_latest_edit_and_failed_execution_stays_pending(setup, monkeypatch):
    from backend.nl2sql.security import SqlSafetyError
    agent, _, _ = setup
    identifier = pending(agent)
    agent.query('时间改成2024年', session_id='long')
    assert resume(agent, identifier)['effective_question'] == '2025年华东销售额趋势'
    def reject(*args, **kwargs):
        raise SqlSafetyError('rejected')
    monkeypatch.setattr(agent.engine, 'answer', reject)
    assert agent.query('按月统计', session_id='long')['status'] == 'incomplete'
    assert resume(agent, identifier)['status'] == 'clarification'


def test_reset_foreign_session_and_record_bound(setup):
    agent, now, _ = setup
    identifiers = []
    for index in range(5):
        now[0] += 1
        identifiers.append(pending(agent))
    assert resume(agent, identifiers[0])['result']['clarification_code'] == 'pending_reference_unavailable'
    foreign = agent.query(f'继续待补查询编号{identifiers[-1]}', session_id='other')
    assert foreign['result']['clarification_code'] == 'pending_reference_unavailable'
    reset = agent.query(f'继续待补查询编号{identifiers[-1]}', session_id='long', reset_context=True)
    assert reset['result']['clarification_code'] == 'pending_reference_unavailable'


def test_sqlite_new_store_recovers_after_short_history_expiry(setup):
    agent, now, options = setup
    if 'storage_path' not in options:
        pytest.skip('persistent store only')
    identifier = pending(agent)
    now[0] += 11
    agent.conversations = ConversationStore(**options)
    assert agent.conversations.context('long') == ()
    assert resume(agent, identifier, '，按月统计')['status'] == 'ok'


def test_global_session_bound_and_deep_copy(tmp_path):
    store = ConversationStore(max_sessions=1)
    state = {'route':'sql', 'pending_question':'scope', 'clarification_code':'missing_time_grain'}
    store.remember('a', question='a', effective_question='scope', state=state)
    identifier = store.context('a')[-1].turn_id
    turn, _ = store.pending_tasks.resolve('a', identifier)
    turn.state['pending_question'] = 'mutated'
    assert store.pending_tasks.resolve('a', identifier)[0].effective_question == 'scope'
    store.remember('b', question='b', effective_question='scope', state=state)
    assert store.pending_tasks.resolve('a', identifier)[1] == 'unavailable'


def test_independent_process_reads_persisted_snapshot_after_history_expiry(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    path = tmp_path/'sessions.sqlite'
    store = ConversationStore(storage_path=path, clock=lambda: 100, ttl_seconds=10, pending_ttl_seconds=100)
    store.remember('process', question='original', effective_question='scope',
        state={'route':'sql','pending_question':'scope','clarification_code':'missing_time_grain'})
    identifier = store.context('process')[-1].turn_id
    program = '''import sys
from backend.session import ConversationStore
store=ConversationStore(storage_path=sys.argv[1],clock=lambda:111,ttl_seconds=10,pending_ttl_seconds=100)
assert store.context('process')==()
turn,status=store.pending_tasks.resolve('process',sys.argv[2])
assert status=='pending' and turn.effective_question=='scope'
print('persisted snapshot verified')
'''
    result = subprocess.run([sys.executable, '-c', program, str(path), identifier],
        cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
