"""Real file/chunk provenance and threshold-comparison regression."""
from io import BytesIO

import pytest
from openpyxl import Workbook

from backend.dependency_agent import DependencyAgent
from backend.evidence_fact import extract_search_fact
from backend.knowledge_store import KnowledgeStore, SourceIntegrityError


@pytest.fixture
def store(tmp_path):
    knowledge = KnowledgeStore(tmp_path / 'corpus')
    knowledge.ingest('一般工单应在24小时内首次响应，紧急工单应在2小时内首次响应。'.encode(),
                     document_id='notice', title='工单首次响应时间', modality='txt', filename='notice.txt')
    wb = Workbook()
    wb.active.append(['工单优先级', '首次响应小时', '适用版本'])
    wb.active.append(['紧急', 2, '2025'])
    stream = BytesIO()
    wb.save(stream)
    knowledge.ingest(stream.getvalue(), document_id='standards', title='工单响应标准', modality='xlsx', filename='standards.xlsx')
    return knowledge


def tasks(operator='le'):
    return [
        {'id': 'search', 'tool': 'search', 'args': {'query': '紧急工单首次响应时间'}},
        {'id': 'fact', 'tool': 'search_fact', 'args': {'evidence': {'ref': 'search', 'path': []},
                                                   'scope': '紧急工单', 'label': '首次响应', 'unit': '小时'}},
        {'id': 'standard', 'tool': 'document_cell', 'args': {'document_id': 'standards',
                                                          'where': {'工单优先级': '紧急', '适用版本': '2025'}, 'column': '首次响应小时'}},
        {'id': 'comparison', 'tool': 'compare', 'args': {'left': {'ref': 'fact', 'path': []},
                                                       'right': {'ref': 'standard', 'path': []}, 'operator': operator}},
    ]


def evidence(store):
    return {'hits': [hit.to_dict() for hit in store.search('紧急工单首次响应时间')]}


def test_search_to_fact_to_excel_comparison_keeps_both_sources(store):
    result = DependencyAgent(None, store).run(tasks())
    assert result['status'] == 'ok'
    final = result['results']['comparison']
    assert final['matched'] is True and final['operator'] == 'le'
    assert final['left']['value'] == final['right']['value'] == 2
    assert final['unit'] == '小时' and final['difference'] == 0
    assert final['left']['document_id'] == 'notice'
    assert final['left']['quote'] == '紧急工单应在2小时内首次响应'
    assert final['right']['locator'].endswith('/column:首次响应小时')
    assert {'from': 'search', 'to': 'fact'} in result['edges']
    assert {'from': 'fact', 'to': 'comparison'} in result['edges']


@pytest.mark.parametrize('operator,matched', [('eq', True), ('ne', False), ('lt', False), ('le', True), ('gt', False), ('ge', True)])
def test_typed_comparison_operators(store, operator, matched):
    result = DependencyAgent(None, store).run(tasks(operator))
    assert result['status'] == 'ok'
    assert result['results']['comparison']['matched'] is matched


@pytest.mark.parametrize('text', [
    '紧急工单：首次响应时间为3小时。',
    '紧急工单：首次响应为1至2小时。',
    '紧急工单：首次响应一般为2小时，特殊为3小时。\n紧急工单首次响应2小时或3小时。',
])
def test_conflict_or_ambiguous_fact_stops_comparison(store, text):
    store.ingest(text.encode(), document_id='conflict', title='工单首次响应时间', modality='txt', filename='conflict.txt')
    result = DependencyAgent(None, store).run(tasks())
    assert result['status'] == 'incomplete' and result['failed_task'] == 'fact'
    assert 'comparison' not in result['results']


@pytest.mark.parametrize('scope,label,unit', [('重大工单', '首次响应', '小时'), ('紧急工单', '关闭时间', '小时'), ('紧急工单', '首次响应', '分钟')])
def test_wrong_scope_label_or_unit_never_borrows_other_number(store, scope, label, unit):
    with pytest.raises(ValueError):
        extract_search_fact(store, evidence(store), scope=scope, label=label, unit=unit)


def test_missing_reference_cannot_claim_provenance(store):
    plan = tasks()
    plan[1]['args']['evidence'] = evidence(store)
    result = DependencyAgent(None, store).run(plan)
    assert result['status'] == 'incomplete' and result['failed_task'] == 'fact'


@pytest.mark.parametrize('mutation', ['snippet', 'sha256', 'locator', 'source_uri'])
def test_forged_search_excerpt_cannot_become_a_typed_fact(store, mutation):
    data = evidence(store)
    hit = next(hit for hit in data['hits'] if hit['metadata']['document_id'] == 'notice')
    if mutation == 'snippet':
        hit['snippet'] = '紧急工单首次响应999小时'
    elif mutation == 'sha256':
        hit['metadata']['source_sha256'] = 'forged'
    elif mutation == 'locator':
        hit['metadata']['source_locator'] = 'made-up'
    else:
        hit['source_uri'] = 'made-up'
    with pytest.raises(ValueError, match='不一致'):
        extract_search_fact(store, data, scope='紧急工单', label='首次响应', unit='小时')


def test_source_changed_between_search_and_fact_abstains(store):
    data = evidence(store)
    path, _ = store.original('notice')
    path.write_bytes('紧急工单首次响应999小时'.encode())
    with pytest.raises(SourceIntegrityError):
        extract_search_fact(store, data, scope='紧急工单', label='首次响应', unit='小时')


@pytest.mark.parametrize('text', ['紧急工单首次响应不是2小时。', '紧急工单首次响应至少2小时。', '紧急工单首次响应2小时吗？'])
def test_negated_lower_bound_or_question_is_not_a_numeric_assertion(store, text):
    store.ingest(text.encode(), document_id='notice', title='工单首次响应时间', modality='txt', filename='notice.txt')
    with pytest.raises(ValueError):
        extract_search_fact(store, evidence(store), scope='紧急工单', label='首次响应', unit='小时')


def test_mismatched_units_stop_numeric_comparison(store):
    agent = DependencyAgent(None, store)
    left = {'value': 2, 'unit': '小时', 'source_uri': 'real', 'locator': 'real'}
    right = {'value': 120, 'unit': '分钟', 'source_uri': 'real', 'locator': 'real'}
    refs = {'left': {'ref': 'l', 'path': []}, 'right': {'ref': 'r', 'path': []}, 'operator': 'le'}
    with pytest.raises(ValueError, match='单位'):
        agent.execute('compare', {'left': left, 'right': right, 'operator': 'le'}, refs, {'l': left, 'r': right})
