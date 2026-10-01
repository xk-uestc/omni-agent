from copy import deepcopy
from io import BytesIO

import pytest
from openpyxl import Workbook

from backend.dependency_agent import DependencyAgent, DependencyPlanError
from backend.fusion_normalization import normalize_fusion_tasks
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database


def task(identifier, tool, **args):
    return {'id': identifier, 'tool': tool, 'args': args}


def ref(identifier, *path):
    return {'ref': identifier, 'path': list(path)}


@pytest.fixture
def agent(tmp_path):
    store = KnowledgeStore(tmp_path / 'corpus')
    texts = {
        'formulas': '目标销售额 = 基准销售额 * (1 + 目标增长率)\n客单价 = 销售额 / 订单数',
        'service': '一般工单应在24小时内首次响应，紧急工单应在2小时内首次响应。',
        'policy': '2024版期限为5日，生效区间为2024-01-01至2024-12-31。\n2025版期限为7日，自2025-01-01起生效。',
        'facts': '标准名称：紧急响应',
    }
    for identifier, text in texts.items():
        store.ingest(text.encode(), document_id=identifier, title=identifier, modality='txt', filename=identifier+'.txt')
    book = Workbook()
    book.active.append(['地区', '年份', '目标增长率', '首次响应小时'])
    book.active.append(['华东', 2026, .12, 2])
    stream = BytesIO()
    book.save(stream)
    store.ingest(stream.getvalue(), document_id='targets', title='区域目标', modality='xlsx', filename='targets.xlsx')
    return DependencyAgent(Nl2SqlEngine(initialize_database(tmp_path / 'demo.sqlite')), store)


def test_actual_sql_document_formula_and_cell_restore_whole_evidence(agent):
    tasks = [task('formula', 'document_formula', document_id='formulas', label='目标销售额'),
             task('base', 'sql', question='2025年华东地区销售额'),
             task('growth', 'document_cell', document_id='targets', where={'地区': '华东', '年份': 2026}, column='目标增长率'),
             task('result', 'calculate', formula=ref('formula', 'expression'), parameters={
                 '基准销售额': ref('base', 'rows', 0, '销售额'), '目标增长率': ref('growth', 'value')})]
    original = deepcopy(tasks)
    assert agent.run(tasks)['status'] == 'incomplete'
    normalized, notes = normalize_fusion_tasks(tasks)
    assert tasks == original
    assert len(notes) == 2
    assert normalized[-1]['args']['parameters']['基准销售额'] == ref('base', 'rows', 0, '销售额')
    result = agent.run(normalized)
    assert result['status'] == 'ok'
    assert result['results']['result']['value'] == pytest.approx(29584 * 1.12)
    assert result['results']['result']['parameters']['基准销售额']['source_uri'].startswith('sql://')
    assert result['results']['result']['parameters']['目标增长率']['source_uri'].endswith('/targets/original')
    assert result['results']['result']['formula_source'].endswith('/formulas/original')
    second, repeat_notes = normalize_fusion_tasks(normalized)
    assert second == normalized and repeat_notes == []


def test_actual_search_fact_and_document_cell_comparison_keep_provenance(agent):
    tasks = [task('search', 'search', query='紧急工单首次响应时间'),
             task('fact', 'search_fact', evidence=ref('search'), scope='紧急工单', label='首次响应', unit='小时'),
             task('standard', 'document_cell', document_id='targets', where={'地区': '华东', '年份': 2026}, column='首次响应小时'),
             task('compare', 'compare', left=ref('fact', 'value'), right=ref('standard', 'value'), operator='le')]
    assert agent.run(tasks)['status'] == 'incomplete'
    normalized, notes = normalize_fusion_tasks(tasks)
    assert len(notes) == 2
    result = agent.run(normalized)
    assert result['status'] == 'ok' and result['results']['compare']['matched'] is True
    assert result['results']['compare']['left']['quote'] == '紧急工单应在2小时内首次响应'
    assert result['results']['compare']['right']['locator'].endswith('/column:首次响应小时')


@pytest.mark.parametrize('source_tool,args', [
    ('policy_select', {'document_id': 'policy', 'as_of': '2025-01-01', 'label': '期限'}),
    ('document_fact', {'document_id': 'facts', 'label': '标准名称'}),
])
def test_actual_policy_and_text_fact_comparison_restore_evidence(agent, source_tool, args):
    tasks = [task('left', source_tool, **args), task('right', source_tool, **args),
             task('compare', 'compare', left=ref('left', 'value'), right=ref('right', 'value'))]
    normalized, notes = normalize_fusion_tasks(tasks)
    assert len(notes) == 2 and all(note['source_tool'] == source_tool for note in notes)
    result = agent.run(normalized)
    assert result['status'] == 'ok' and result['results']['compare']['matched'] is True
    assert result['results']['compare']['left']['source_uri']


@pytest.mark.parametrize('reference', [ref('formula', 'parameters', 0), ref('formula', 'expression', 0),
                                       ref('formula', 'value'), 'literal formula', 100])
def test_other_paths_and_literals_are_not_repaired_and_still_fail(agent, reference):
    tasks = [task('formula', 'document_formula', document_id='formulas', label='客单价'),
             task('calculate', 'calculate', formula=reference, parameters={})]
    original = deepcopy(tasks)
    normalized, notes = normalize_fusion_tasks(tasks)
    assert normalized == original and tasks == original and notes == []
    assert agent.run(normalized)['status'] == 'incomplete'


def test_same_leaf_on_wrong_tool_and_sql_question_are_unchanged(agent):
    tasks = [task('source', 'document_fact', document_id='facts', label='标准名称'),
             task('sql', 'sql', question=['2025年', ref('source', 'value'), '销售额']),
             task('calculate', 'calculate', formula=ref('source', 'expression'), parameters={'x': ref('source', 'value')})]
    normalized, notes = normalize_fusion_tasks(tasks)
    assert normalized == tasks and notes == []


def test_wrong_compare_source_is_not_repaired_and_executor_rejects(agent):
    tasks = [task('sql', 'sql', question='2025年华东地区销售额'),
             task('compare', 'compare', left=ref('sql', 'value'), right=ref('sql', 'value'))]
    normalized, notes = normalize_fusion_tasks(tasks)
    assert normalized == tasks and notes == []
    assert agent.run(normalized)['status'] == 'incomplete'


@pytest.mark.parametrize('tasks', [None, [], {}, [None],
    [task('bad', 'shell', command='echo unsafe')],
    [task('bad', 'unknown')],
    [task('bad', 'compare', left=ref('missing', 'value'), right=ref('missing', 'value'))],
    [task('bad', 'compare', left=ref('bad', 'value'), right=ref('bad', 'value'))],
    [task('bad', [], anything='x')],
    [task('bad', 'search', query={'ref': 'bad', 'path': [True]})],
    [task('bad', 'search', query={'ref': 'bad', 'path': [['value']]})],
    [task('bad', 'search', query=float('nan'))],
])
def test_invalid_structures_unknown_tools_and_refs_explicitly_reject(tasks):
    with pytest.raises(DependencyPlanError):
        normalize_fusion_tasks(tasks)


def test_cycles_and_aliases_do_not_crash_or_execute():
    recursive = []
    recursive.append(recursive)
    with pytest.raises(DependencyPlanError):
        normalize_fusion_tasks(recursive)
    with pytest.raises(DependencyPlanError):
        normalize_fusion_tasks([task('a', 'search', query=ref('b')), task('b', 'search', query=ref('a'))])


def test_normalization_does_not_execute_and_does_not_alias_nested_user_args(monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError('normalization must not execute tools')
    monkeypatch.setattr(DependencyAgent, 'execute', prohibited)
    tasks = [task('cell', 'document_cell', document_id='targets', where={'地区': '华东'}, column='目标增长率'),
             task('compare', 'compare', left=ref('cell', 'value'), right=ref('cell', 'value'))]
    normalized, notes = normalize_fusion_tasks(tasks)
    normalized[0]['args']['where']['地区'] = 'changed only in copy'
    notes[0]['old_path'].append('changed only in note')
    assert tasks[0]['args']['where']['地区'] == '华东'
    assert tasks[1]['args']['left']['path'] == ['value']


def test_shared_reference_alias_does_not_change_unrelated_sql_question():
    shared = ref('cell', 'value')
    tasks = [task('cell', 'document_cell', document_id='targets', where={'地区': '华东'}, column='目标增长率'),
             task('formula', 'document_formula', document_id='formulas', label='客单价'),
             task('sql', 'sql', question=['2025年', shared, '销售额']),
             task('calculate', 'calculate', formula=ref('formula'), parameters={'x': shared})]
    normalized, notes = normalize_fusion_tasks(tasks)
    assert len(notes) == 1
    assert normalized[-1]['args']['parameters']['x'] == ref('cell')
    assert normalized[-2]['args']['question'][1] == ref('cell', 'value')
    assert shared == ref('cell', 'value')


def test_cell_value_projection_for_sql_text_keeps_actual_source_dependency(agent):
    # The existing fixture's target table also contains the actual region.
    tasks = [task('cell', 'document_cell', document_id='targets', where={'地区': '华东'}, column='地区'),
             task('sql', 'sql', question=['2025年', ref('cell'), '地区销售额'])]
    assert agent.run(tasks)['status'] == 'incomplete'
    normalized, notes = normalize_fusion_tasks(tasks)
    assert normalized[1]['args']['question'][1] == ref('cell', 'value')
    assert tasks[1]['args']['question'][1] == ref('cell')
    assert notes[0]['reason'] == 'project_cell_value_for_text_with_dependency_provenance_retained'
    result = agent.run(normalized)
    assert result['status'] == 'ok'
    assert result['results']['sql']['rows'][0]['销售额'] == 29584
    assert {'from': 'cell', 'to': 'sql'} in result['edges']
    assert result['results']['cell']['source_uri'].endswith('/original')


def test_complex_or_non_cell_result_is_never_stringified_into_query_text():
    tasks = [task('formula', 'document_formula', document_id='formulas', label='客单价'),
             task('sql', 'sql', question=['2025年', ref('formula'), '销售额'])]
    normalized, notes = normalize_fusion_tasks(tasks)
    assert normalized == tasks and notes == []
