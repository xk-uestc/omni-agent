from io import BytesIO

import pytest
from openpyxl import Workbook

from backend.dependency_agent import DependencyAgent, DependencyPlanError
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


@pytest.fixture
def agent(tmp_path):
    store = KnowledgeStore(tmp_path / 'corpus')
    store.ingest('客单价 = 销售额 / 订单数\n目标销售额 = 基准销售额 * (1 + 目标增长率)\n目标地区：华东'.encode(), document_id='policy', title='指标与计划', modality='txt', filename='policy.txt')
    wb = Workbook()
    wb.active.append(['地区', '年份', '目标增长率'])
    wb.active.append(['华东', 2026, 0.12])
    wb.active.append(['华南', 2026, 0.10])
    stream = BytesIO()
    wb.save(stream)
    store.ingest(stream.getvalue(), document_id='targets', title='区域计划', modality='xlsx', filename='targets.xlsx')
    return DependencyAgent(Nl2SqlEngine(initialize_database(tmp_path / 'demo.sqlite')), store)


def test_document_formula_actually_binds_sql_cell_and_edges(agent):
    tasks = [
        {'id': 'formula', 'tool': 'document_formula', 'args': {'document_id': 'policy', 'label': '客单价'}},
        {'id': 'sql', 'tool': 'sql', 'args': {'question': '2025年华东地区销售额和订单数'}},
        {'id': 'calculate', 'tool': 'calculate', 'args': {'formula': {'ref': 'formula', 'path': []}, 'parameters': {
            '销售额': {'ref': 'sql', 'path': ['rows', 0, '销售额']}, '订单数': {'ref': 'sql', 'path': ['rows', 0, '订单数']}}}},
    ]
    events = []
    result = agent.run(tasks, on_event=events.append)
    assert result['status'] == 'ok'
    assert result['results']['calculate']['value'] == pytest.approx(29584 / 3)
    assert {'from': 'sql', 'to': 'calculate'} in result['edges']
    assert result['results']['calculate']['parameters']['销售额']['source_uri'].startswith('sql://')
    assert [event['task_id'] for event in events] == ['formula', 'sql', 'calculate']


def test_document_value_changes_subsequent_sql_filter(agent):
    result = agent.run([
        {'id': 'region', 'tool': 'document_fact', 'args': {'document_id': 'policy', 'label': '目标地区'}},
        {'id': 'sales', 'tool': 'sql', 'args': {'question': ['2025年', {'ref': 'region', 'path': ['value']}, '地区销售额']}},
    ])
    assert result['status'] == 'ok'
    assert '华东' in result['results']['sales']['parameters']
    assert result['results']['sales']['rows'][0]['销售额'] == 29584


def test_three_source_forecast_uses_actual_excel_parameter(agent):
    result = agent.run([
        {'id': 'definition', 'tool': 'document_formula', 'args': {'document_id': 'policy', 'label': '目标销售额'}},
        {'id': 'base', 'tool': 'sql', 'args': {'question': '2025年华东地区销售额'}},
        {'id': 'growth', 'tool': 'document_cell', 'args': {'document_id': 'targets', 'where': {'地区': '华东', '年份': 2026}, 'column': '目标增长率'}},
        {'id': 'forecast', 'tool': 'calculate', 'args': {'formula': {'ref': 'definition', 'path': []}, 'parameters': {'基准销售额': {'ref': 'base', 'path': ['rows', 0, '销售额']}, '目标增长率': {'ref': 'growth', 'path': []}}}},
    ])
    assert result['status'] == 'ok'
    assert result['results']['forecast']['value'] == pytest.approx(29584 * 1.12)
    assert 'sheet:' in result['results']['forecast']['parameters']['目标增长率']['locator']


@pytest.mark.parametrize('tasks', [
    [{'id': 'a', 'tool': 'search', 'args': {'query': {'ref': 'b', 'path': []}}}],
    [{'id': 'a', 'tool': 'search', 'args': {'query': {'ref': 'a', 'path': []}}}],
    [{'id': 'a', 'tool': 'delete', 'args': {}}],
    [{'id': 'a', 'tool': 'search', 'args': {}}, {'id': 'a', 'tool': 'search', 'args': {}}],
])
def test_invalid_dags_rejected_before_execution(agent, tasks):
    with pytest.raises(DependencyPlanError):
        agent.run(tasks)


def test_failure_stops_dependants_without_inventing_numeric_value(agent):
    result = agent.run([
        {'id': 'missing', 'tool': 'document_cell', 'args': {'document_id': 'targets', 'where': {'年份': 2026}, 'column': '目标增长率'}},
        {'id': 'never', 'tool': 'sql', 'args': {'question': ['销售额', {'ref': 'missing', 'path': ['value']}]}}
    ])
    assert result['status'] == 'incomplete'
    assert result['failed_task'] == 'missing'
    assert 'never' not in result['results']


def test_literal_formula_cannot_claim_document_provenance(agent):
    result = agent.run([{'id': 'fake', 'tool': 'calculate', 'args': {'formula': {'expression': '1+1', 'source_uri': 'made-up', 'locator': 'made-up'}, 'parameters': {}}}])
    assert result['status'] == 'incomplete'


@pytest.mark.parametrize('base_year,target_year,expected', [(2025, 2026, 'ok'), (2024, 2026, 'incomplete'), (2025, 2027, 'incomplete')])
def test_forecast_enforces_document_years(agent, base_year, target_year, expected):
    agent.knowledge_store.ingest('2026年目标增长率为12%，预测基准为2025年销售额。\n目标销售额 = 基准销售额 * (1 + 目标增长率)'.encode(), document_id='scoped', title='预测', modality='txt', filename='forecast.txt')
    # Add a real 2027 spreadsheet row so the mismatch reaches the temporal guard.
    wb = Workbook()
    wb.active.append(['地区', '年份', '目标增长率'])
    wb.active.append(['华东', 2026, .12])
    wb.active.append(['华东', 2027, .20])
    stream = BytesIO()
    wb.save(stream)
    agent.knowledge_store.ingest(stream.getvalue(), document_id='scoped-targets', title='目标', modality='xlsx', filename='targets.xlsx')
    result = agent.run([
        {'id': 'definition', 'tool': 'document_formula', 'args': {'document_id': 'scoped', 'label': '目标销售额'}},
        {'id': 'base', 'tool': 'sql', 'args': {'question': f'{base_year}年华东地区销售额'}},
        {'id': 'growth', 'tool': 'document_cell', 'args': {'document_id': 'scoped-targets', 'where': {'地区': '华东', '年份': target_year}, 'column': '目标增长率'}},
        {'id': 'result', 'tool': 'calculate', 'args': {'formula': {'ref': 'definition', 'path': []}, 'parameters': {'基准销售额': {'ref': 'base', 'path': ['rows', 0, '销售额']}, '目标增长率': {'ref': 'growth', 'path': []}}}},
    ])
    assert result['status'] == expected
    if expected == 'ok':
        assert result['results']['result']['temporal_validation'] == {'status': 'verified', 'base_year': 2025, 'target_year': 2026}
    else:
        assert result['failed_task'] == 'result'
        assert 'result' not in result['results']
