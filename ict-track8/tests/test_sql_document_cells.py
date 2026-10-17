"""Ranked-cell authorization guard fixtures, not autonomous model scores."""
from copy import deepcopy
import json
import sqlite3

import pytest

from backend.dependency_agent import DependencyAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.omni_agent import OmniAgent, _normalize_model_tasks
from backend.responses_client import GenerationError
from backend.session import ConversationStore
from tests.test_visual_table_reader import grid_pdf


QUESTION = '先从数据库按工种统计用工统计工时合计，取最高的1个，再从文档《价格手册》中查询该工种的2025 Actual值。'


def graph():
    return [{'id': 'rank', 'tool': 'sql', 'args': {'question': '按工种统计用工统计工时合计，取最高的1个'}},
            {'id': 'cell', 'tool': 'document_cell', 'args': {'document_id': 'grid', 'where': {
                'Region': {'ref': 'rank', 'path': ['dimension_values', '用工统计', '工种', 0]}}, 'column': '2025 Actual'}}]


@pytest.fixture
def environment(tmp_path):
    db = tmp_path / 'labor.sqlite'
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE 用工统计(记录ID INTEGER PRIMARY KEY, 工种 TEXT, 工时 REAL, 日期 TEXT)')
        connection.executemany('INSERT INTO 用工统计 VALUES(?,?,?,?)',
                               [(1, 'West', 80, '2025-01-01'), (2, 'West', 30, '2025-01-02'), (3, 'East', 50, '2025-01-03')])
    catalog = tmp_path / 'catalog.json'
    catalog.write_text(json.dumps({'version': 'guard_fixture', 'metrics': [
        {'id': 'hours', 'table': '用工统计', 'column': '工时', 'function': 'SUM', 'label': '总工时',
         'query_alias': '工时', 'aliases': ['工时'], 'unit': '小时'}]}, ensure_ascii=False))
    engine = Nl2SqlEngine(db, metric_catalog_path=catalog)
    knowledge = KnowledgeStore(tmp_path / 'knowledge')
    knowledge.ingest(grid_pdf(), document_id='grid', title='价格手册', modality='pdf', filename='grid.pdf')
    return engine, knowledge


def run(environment, question=QUESTION, tasks=None):
    return DependencyAgent(*environment).run(graph() if tasks is None else tasks, original_question=question)


@pytest.mark.parametrize('question', [QUESTION, QUESTION.replace('，取', '；取'),
    '找出工时最高的工种，并查询价格手册中该工种的2025 Actual值。',
    '找出工时最高的工种并查询价格手册中该工种的2025 Actual值。',
    '从文档《价格手册》中查询该工种的2025 Actual值；找出工时最高的工种。'])
def test_literal_ranked_lookup_retains_same_semantic_scope(environment, question):
    tasks = graph(); unchanged = deepcopy(tasks)
    result = run(environment, question, tasks)
    assert result['status'] == 'ok', result
    cell = result['results']['cell']
    assert cell['value'] == 3578 and cell['calculator_input_eligible'] is False
    assert cell['dependency_reference_validation']['value'] == 'West'
    assert tasks == unchanged


@pytest.mark.parametrize('question', [
    QUESTION.replace('最高', '最低'), QUESTION.replace('合计', '平均'),
    QUESTION.replace('工时合计', '2024年工时合计'),
    QUESTION.replace('该工种的', '该客户的'),
    QUESTION.replace('该工种的', '仅已批准记录该工种的'),
    QUESTION.replace('2025 Actual值', '2026 Forecast值'),
    QUESTION.replace('价格手册', '其他手册'),
    '仅West，' + QUESTION,
])
def test_changed_meaning_is_not_authorized_by_old_graph(environment, question):
    assert run(environment, question)['status'] != 'ok'


@pytest.mark.parametrize('mutation', [
    lambda t: t[1]['args'].update(document_id='other'),
    lambda t: t[1]['args'].update(column='2026 Forecast'),
    lambda t: t[1]['args'].update(where={'Region': 'West'}),
    lambda t: t[1]['args'].update(where={'Region': {'ref': 'rank', 'path': ['rows', 0, '总工时']}}),
    lambda t: t[1]['args']['where']['Region']['path'].__setitem__(3, True),
    lambda t: t[0]['args'].update(question='按工种统计用工统计工时合计'),
    lambda t: t.pop(1),
])
def test_unrequested_source_target_constant_and_missing_operation_stop(environment, mutation):
    tasks = graph(); mutation(tasks)
    assert run(environment, tasks=tasks)['status'] != 'ok'


def test_all_tied_winners_must_be_consumed(environment):
    engine, knowledge = environment
    with sqlite3.connect(engine.database_path) as connection:
        connection.execute("UPDATE 用工统计 SET 工时=110 WHERE 工种='East'")
    result = run(environment)
    assert result['status'] == 'incomplete' and 'cell' not in result['results']
    tasks = graph()
    second = deepcopy(tasks[1]); second['id'] = 'cell2'; second['args']['where']['Region']['path'][-1] = 1
    result = run(environment, tasks=tasks + [second])
    assert result['status'] == 'ok' and len(result['results']['rank']['rows']) == 2


def test_source_replacement_between_tools_stops_cell(environment):
    engine, knowledge = environment
    def event(item):
        if item.get('tool') == 'sql' and item['status'] == 'complete':
            knowledge.ingest(b'replaced', document_id='grid', title='价格手册', modality='txt', filename='grid.txt')
    result = DependencyAgent(engine, knowledge).run(graph(), original_question=QUESTION, on_event=event)
    assert result['status'] == 'incomplete' and 'cell' not in result['results']


def test_model_cannot_replace_lookup_with_single_source(environment):
    engine, knowledge = environment
    plan = {'route': 'sql', 'effective_question': QUESTION, 'clarification': '', 'tasks_json': '[]'}
    with pytest.raises(GenerationError):
        _normalize_model_tasks(plan, [], engine, knowledge, QUESTION)


def test_bounded_repair_reports_scope_error_instead_of_text_type(environment):
    engine, knowledge = environment
    class ProtocolFixture:
        audit = {'status': 'completed', 'http_status': 200}
        calls = []
        def generate(self, instructions, context, schema, **kwargs):
            self.calls.append(deepcopy(context))
            tasks = graph()
            if len(self.calls) == 1:
                tasks[0]['args']['question'] = '按工种统计用工统计工时合计'
            return {'route': 'fusion', 'effective_question': QUESTION, 'clarification': '',
                    'tasks_json': json.dumps(tasks, ensure_ascii=False)}
    client = ProtocolFixture()
    result = OmniAgent(engine, knowledge, ConversationStore(), client).query(QUESTION)
    assert result['status'] == 'ok' and len(client.calls) == 2
    feedback = client.calls[1]['plan_completion_feedback']
    assert feedback['source_scope_errors'] and 'reference_type_errors' not in feedback
    assert '取最高的1个' in feedback['instruction']
