"""Product receipts remain tied to execution and the user's selected original."""
import json

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.dependency_agent import DependencyAgent
from backend.knowledge_store import KnowledgeStore, SourceIntegrityError
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.presentation_events import presentation_event, tool_receipt
from backend.session import ConversationStore


@pytest.fixture
def product_agent(tmp_path, monkeypatch):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    store = KnowledgeStore(tmp_path / 'knowledge')
    store.ingest('保修期为12个月。\n客单价 = 销售额 / 订单数'.encode(), document_id='chosen',
                 title='所选保修说明', modality='txt', filename='chosen.txt')
    store.ingest('保修期为99个月。'.encode(), document_id='other',
                 title='无关保修说明', modality='txt', filename='other.txt')
    conversations = ConversationStore(storage_path=tmp_path / 'sessions.sqlite')
    monkeypatch.setattr(app_module, 'engine', engine)
    monkeypatch.setattr(app_module, 'knowledge_store', store)
    monkeypatch.setattr(app_module, 'conversation_store', conversations)
    monkeypatch.setattr(app_module, 'generation_client', None)
    return OmniAgent(engine, store, conversations)


def _events(response):
    return [(lines[0][7:], json.loads(lines[1][6:]))
            for block in response.text.split('\n\n') if block.startswith('event: ')
            for lines in [block.splitlines()]]


def test_progress_wraps_actual_calls_and_keeps_legacy_terminal_hooks(product_agent, monkeypatch):
    dependency = DependencyAgent(product_agent.engine, product_agent.knowledge)
    progress, terminal = [], []
    execute = dependency.execute
    def observed(tool, *args, **kwargs):
        assert progress[-1]['tool'] == tool and progress[-1]['status'] == 'running'
        return execute(tool, *args, **kwargs)
    monkeypatch.setattr(dependency, 'execute', observed)
    result = dependency.run([
        {'id': 'formula', 'tool': 'document_formula', 'args': {'document_id': 'chosen', 'label': '客单价'}},
        {'id': 'sales', 'tool': 'sql', 'args': {'question': '2025年华东地区销售额和订单数'}},
        {'id': 'calculate', 'tool': 'calculate', 'args': {
            'formula': {'ref': 'formula', 'path': []}, 'parameters': {
                '销售额': {'ref': 'sales', 'path': ['rows', 0, '销售额']},
                '订单数': {'ref': 'sales', 'path': ['rows', 0, '订单数']}}}},
    ], on_progress=progress.append, on_event=terminal.append)
    assert result['status'] == 'ok'
    assert [event['status'] for event in terminal] == ['complete'] * 3
    assert [event['status'] for event in progress] == ['running', 'complete'] * 3
    calculation = progress[-1]
    assert calculation['dependencies'] == ['formula', 'sales']
    assert calculation['output']['value'] == result['results']['calculate']['value']
    assert calculation['output']['parameters']['销售额']['source_uri'].startswith('sql://')
    envelope = presentation_event(calculation)['presentation']
    assert envelope['type'] == 'calculation' and envelope['depends_on'] == ['formula', 'sales']


def test_failed_tool_never_starts_dependants(product_agent):
    dependency = DependencyAgent(product_agent.engine, product_agent.knowledge)
    progress = []
    result = dependency.run([
        {'id': 'missing', 'tool': 'document_formula', 'args': {'document_id': 'chosen', 'label': '不存在'}},
        {'id': 'later', 'tool': 'calculate', 'args': {'formula': {'ref': 'missing', 'path': []}, 'parameters': {}}},
    ], on_progress=progress.append)
    assert result['status'] == 'incomplete'
    assert [event['task_id'] for event in progress] == ['missing', 'missing']
    assert progress[-1]['status'] == 'failed' and 'output' not in progress[-1]
    assert result['skipped_tasks'] == ['later']


def test_public_receipt_excludes_model_private_fields_and_bounds_results():
    receipt = tool_receipt('sql', {'status': 'ok', 'sql': 'SELECT 1',
        'rows': [{'n': i} for i in range(50)], 'reasoning': 'PRIVATE', 'api_key': 'PRIVATE'})
    assert len(receipt['rows']) == 12 and receipt['row_count'] == 50
    assert receipt['preview_limited'] is True
    assert 'PRIVATE' not in json.dumps(receipt)


def test_selected_document_question_does_not_retrieve_other_source(product_agent, monkeypatch):
    original = product_agent.knowledge.answer
    def answer(question, **kwargs):
        assert question == '保修期多久？'
        assert kwargs['document_id'] == 'chosen'
        return original(question, **kwargs)
    monkeypatch.setattr(product_agent.knowledge, 'answer', answer)
    result = product_agent.query('保修期多久？', document_id='chosen')
    assert result['document_scope']['document_id'] == 'chosen'
    assert all(c['metadata']['document_id'] == 'chosen' for c in result['result']['citations'])
    assert '99' not in result['result'].get('answer', '')


def test_overview_is_real_bounded_source_extract_without_model(product_agent, monkeypatch):
    monkeypatch.setattr(product_agent.knowledge, 'answer', lambda *a, **k: pytest.fail('Overview does not call a model'))
    result = product_agent.query('帮我分析这个文件。', document_id='chosen', session_id='overview')
    assert result['status'] == 'ok' and result['route'] == 'document'
    assert result['result']['answer_mode'] == 'document_structure_overview'
    coverage = result['result']['coverage']
    assert coverage['full_document_semantic_analysis'] is False
    assert coverage['excerpt_count'] == len(result['result']['citations'])
    assert all(c['metadata']['document_id'] == 'chosen' for c in result['result']['citations'])
    assert '12个月' in result['result']['answer'] and '99个月' not in result['result']['answer']
    assert result['document_reference_id'] == product_agent.conversations.context('overview')[-1].turn_id


def test_selected_document_retains_safety_and_original_hash_checks(product_agent):
    refused = product_agent.query('DELETE FROM sales_orders', document_id='chosen')
    assert refused['status'] == 'clarification'
    assert refused['result']['clarification_code'] == 'read_only_query_required'
    path, _ = product_agent.knowledge.original('chosen')
    path.write_bytes(b'tampered')
    with pytest.raises(SourceIntegrityError):
        product_agent.query('帮我分析这个文件', document_id='chosen')


def test_stream_keeps_legacy_events_and_adds_presentation_envelope(product_agent):
    response = TestClient(app_module.app).post('/api/v1/omni/query/stream', json={
        'question': '帮我分析这个文件', 'document_id': 'chosen'})
    assert response.status_code == 200
    events = _events(response)
    assert events[-1][0] == 'done'
    assert {kind for kind, _ in events} == {'trace', 'done'}
    traces = [value for kind, value in events if kind == 'trace']
    assert all(value['presentation']['version'] == 'agent-presentation-v1' for value in traces)
    source = [value for value in traces if value['tool'] == 'document.scope']
    assert [value['status'] for value in source] == ['running', 'success']
    assert source[0]['call_id'] == source[1]['call_id']
    assert source[1]['presentation']['receipt']['document_id'] == 'chosen'
    assert events[-1][1]['result']['coverage']['full_document_semantic_analysis'] is False


def test_invalid_selected_id_rejected_before_stream(product_agent):
    response = TestClient(app_module.app).post('/api/v1/omni/query/stream', json={
        'question': '帮我分析这个文件', 'document_id': '../private'})
    assert response.status_code == 422


def test_removed_document_selection_has_actionable_error(product_agent):
    client = TestClient(app_module.app)
    payload = {'question': '帮我分析这个文件', 'document_id': 'removed'}
    response = client.post('/api/v1/omni/query', json=payload)
    assert response.status_code == 404
    assert response.json()['detail']['code'] == 'selected_document_missing'
    streamed = _events(client.post('/api/v1/omni/query/stream', json=payload))
    assert streamed[-1][0] == 'error'
    assert streamed[-1][1]['detail']['code'] == 'selected_document_missing'
    assert not any(kind == 'done' for kind, _ in streamed)


@pytest.mark.parametrize('question', [
    '回到文档查询编号 q_0123456789abcdef0123456789abcdef，保修期限是多少？',
    '选择对话来源编号q_0123456789abcdef0123456789abcdef',
    '回到倒数第二次SQL查询，那华南呢',
    '恢复待补查询编号q_0123456789abcdef0123456789abcdef',
    '查询文档“无关保修说明”：保修期多久？',
    '那份文档的保修期限是多少？',
])
def test_explicit_scope_never_overrides_history_or_other_document_reference(product_agent, monkeypatch, question):
    prior = product_agent.query('帮我分析这个文件', document_id='chosen', session_id='scope-conflict')
    history = product_agent.conversations.context('scope-conflict')
    monkeypatch.setattr(product_agent.knowledge, 'answer', lambda *a, **k: pytest.fail('Conflicting sources must not retrieve'))
    result = product_agent.query(question, document_id='chosen', session_id='scope-conflict')
    assert result['status'] == 'clarification'
    assert result['result']['clarification_code'] == 'selected_source_reference_conflict'
    assert result['result']['citations'] == [] and result['state']['context_preserved'] is True
    assert product_agent.conversations.context('scope-conflict') == history
    assert prior['document_reference_id'] == history[-1].turn_id
