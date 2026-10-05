"""Unified query progress is actual execution, never a fabricated tool plan."""
import json

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.omni_agent import OmniAgent
from backend.knowledge_store import KnowledgeStore
from backend.knowledge_store import SourceIntegrityError
from backend.dependency_agent import DependencyAgent
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.session import ConversationStore


@pytest.fixture
def local_agent(tmp_path, monkeypatch):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    knowledge = KnowledgeStore(tmp_path / 'knowledge')
    knowledge.ingest('保修期为12个月'.encode(), document_id='policy', title='保修', modality='txt', filename='policy.txt')
    conversations = ConversationStore(storage_path=tmp_path / 'sessions.sqlite')
    monkeypatch.setattr(app_module, 'engine', engine)
    monkeypatch.setattr(app_module, 'knowledge_store', knowledge)
    monkeypatch.setattr(app_module, 'conversation_store', conversations)
    monkeypatch.setattr(app_module, 'generation_client', None)
    return OmniAgent(engine, knowledge, conversations)


def events(response):
    result = []
    for block in response.text.split('\n\n'):
        if block.startswith('event: '):
            lines = block.splitlines()
            result.append((lines[0][7:], json.loads(lines[1][6:])))
    return result


def test_sql_running_precedes_actual_execution_and_success_contains_real_sql(local_agent, monkeypatch):
    observed = []
    ordinary = local_agent.engine.answer
    def execute(*args, **kwargs):
        assert observed[-1]['tool'] == 'nl2sql' and observed[-1]['status'] == 'running'
        return ordinary(*args, **kwargs)
    monkeypatch.setattr(local_agent.engine, 'answer', execute)
    result = local_agent.query('2025年华东地区销售额', trace_callback=observed.append)
    assert result['status'] == 'ok'
    sql = [item for item in observed if item['tool'] == 'nl2sql']
    assert [item['status'] for item in sql] == ['running', 'success']
    assert sql[0]['call_id'] == sql[1]['call_id']
    assert sql[1]['output']['sql'] == result['result']['sql']
    assert sql[1]['output']['row_count'] == len(result['result']['rows'])
    assert not any(item['tool'] in {'fusion.execute', 'knowledge.answer'} for item in observed)


def test_document_real_call_emits_attention_for_insufficient_evidence(local_agent, monkeypatch):
    monkeypatch.setattr(local_agent.knowledge, 'answer', lambda question: {
        'status': 'insufficient_evidence', 'answer': '没有充分证据', 'citations': [], 'trace': []})
    observed = []
    result = local_agent.query('保修期多久', trace_callback=observed.append)
    assert result['status'] == 'insufficient_evidence'
    document = [item for item in observed if item['tool'] == 'knowledge.answer']
    assert [item['status'] for item in document] == ['running', 'attention']
    assert document[-1]['executed'] is False
    assert observed[-1]['status'] == 'attention'


def test_clarification_does_not_claim_database_tool_execution(local_agent, monkeypatch):
    monkeypatch.setattr(local_agent, 'basic_plan', lambda *args: {
        'route': 'clarify', 'effective_question': args[0], 'tasks_json': '[]', 'clarification': '请确认范围'})
    observed = []
    response = local_agent.query('请帮忙', trace_callback=observed.append)
    assert response['status'] == 'clarification'
    assert not any(item['tool'] in {'nl2sql', 'fusion.execute', 'knowledge.answer'} for item in observed)
    assert observed[-1]['tool'] == 'query.complete' and observed[-1]['status'] == 'attention'


def test_endpoint_final_snapshot_is_query_result_without_extra_session_turn(local_agent):
    client = TestClient(app_module.app)
    response = client.post('/api/v1/omni/query/stream', json={
        'question': '2025年华东地区销售额', 'session_id': 'stream-session'})
    assert response.status_code == 200 and 'text/event-stream' in response.headers['content-type']
    emitted = events(response)
    assert emitted[-1][0] == 'done'
    final = emitted[-1][1]
    assert final['status'] == 'ok' and final['session_id'] == 'stream-session'
    assert final['result']['rows'][0]['销售额'] == 29584
    assert len(local_agent.conversations.context('stream-session')) == 1
    assert final['query_reference_id'] == local_agent.conversations.context('stream-session')[-1].turn_id
    assert any(kind == 'trace' and value['tool'] == 'nl2sql' and value['status'] == 'running'
               for kind, value in emitted)
    assert not any(kind == 'error' for kind, _ in emitted)


def test_endpoint_publishes_exact_returned_snapshot(monkeypatch):
    snapshot = {'status': 'clarification', 'route': 'clarify', 'session_id': 's',
                'result': {'clarification': '请确认年份'}, 'trace': [], 'arbitrary_field': {'kept': True}}
    def query(self, question, **kwargs):
        kwargs['trace_callback']({'tool': 'context.resolve', 'status': 'attention', 'executed': False})
        return snapshot
    monkeypatch.setattr(OmniAgent, 'query', query)
    emitted = events(TestClient(app_module.app).post('/api/v1/omni/query/stream', json={'question': '请确认'}))
    assert emitted[-1] == ('done', snapshot)


def test_tool_exception_emits_error_without_fake_success_or_done(local_agent, monkeypatch):
    def failing(*args, **kwargs):
        raise RuntimeError('SECRET INTERNAL PAYLOAD')
    monkeypatch.setattr(local_agent.engine, 'answer', failing)
    emitted = events(TestClient(app_module.app).post('/api/v1/omni/query/stream', json={'question': '2025年华东地区销售额'}))
    assert emitted[-1][0] == 'error'
    assert not any(kind == 'done' for kind, _ in emitted)
    sql_events = [value for kind, value in emitted if kind == 'trace' and value['tool'] == 'nl2sql']
    assert [item['status'] for item in sql_events] == ['running', 'error']
    assert all('SECRET' not in json.dumps(value) for _, value in emitted)


def test_invalid_session_is_rejected_before_stream_starts(local_agent):
    response = TestClient(app_module.app).post('/api/v1/omni/query/stream', json={
        'question': '2025年华东地区销售额', 'session_id': '../escape'})
    assert response.status_code == 422


def test_fusion_observations_forward_only_actual_completed_tasks(local_agent, monkeypatch):
    monkeypatch.setattr(local_agent, 'basic_plan', lambda question, *args: {
        'route': 'fusion', 'effective_question': question, 'tasks_json': '[]', 'clarification': ''})
    observed = []
    def run(self, tasks, *, original_question, on_event):
        assert observed[-1]['tool'] == 'fusion.execute' and observed[-1]['status'] == 'running'
        on_event({'task_id': 'read-one', 'tool': 'search', 'status': 'complete', 'latency_ms': 1})
        on_event({'task_id': 'rejected', 'tool': 'sql', 'status': 'failed', 'latency_ms': 2})
        return {'status': 'incomplete', 'trace_id': 'fake-observation', 'trace': [], 'results': {}}
    monkeypatch.setattr(DependencyAgent, 'run', run)
    result = local_agent.query('请检索并比较资料', trace_callback=observed.append)
    assert result['status'] == 'incomplete'
    tasks = [item for item in observed if item['stage'] == 'fusion_task']
    assert [(item['task_id'], item['status'], item['executed']) for item in tasks] == [
        ('read-one', 'success', True), ('rejected', 'error', False)]
    fusion = [item for item in observed if item['tool'] == 'fusion.execute']
    assert [item['status'] for item in fusion] == ['running', 'attention']


def test_integrity_error_stream_is_safe_error_not_success(local_agent, monkeypatch):
    def changed(*args, **kwargs):
        raise SourceIntegrityError('PRIVATE SOURCE DETAILS')
    monkeypatch.setattr(local_agent.knowledge, 'answer', changed)
    emitted = events(TestClient(app_module.app).post('/api/v1/omni/query/stream', json={'question': '保修期多久'}))
    assert emitted[-1][0] == 'error'
    assert emitted[-1][1]['detail']['code'] == 'evidence_integrity_failed'
    assert not any(kind == 'done' for kind, _ in emitted)
    assert all('PRIVATE' not in json.dumps(payload) for _, payload in emitted)
