"""Average-of-groups must preserve the inner metric, grain and WHERE scope."""
from copy import deepcopy
import sqlite3

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql import lexicon


@pytest.fixture
def database(tmp_path):
    path = tmp_path / 'group-average.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE Warehouses(warehouse_id INTEGER PRIMARY KEY, category_name TEXT NOT NULL);
            CREATE TABLE Lots(lot_id INTEGER PRIMARY KEY, warehouse_id INTEGER NOT NULL,
                stock REAL, status TEXT NOT NULL,
                FOREIGN KEY(warehouse_id) REFERENCES Warehouses(warehouse_id));
            INSERT INTO Warehouses VALUES(1,'A'),(2,'B'),(3,'C');
            INSERT INTO Lots VALUES(1,1,10,'active'),(2,1,10,'active'),(3,2,80,'active'),
                (4,3,NULL,'active'),(5,1,500,'closed');
        ''')
    return path


def proposal(function='SUM', having=True):
    return {'version':2, 'metrics':[{'id':'m0','table':'Lots','column':'stock',
        'function':function,'label':'quantity','unit':'unknown','currency':None,
        'missing':'null','filters':[]}], 'derived_metrics':[],
        'dimensions':[{'table':'Warehouses','column':'category_name','transform':'raw','label':'group'}],
        'filters':[{'table':'Lots','column':'status','operator':'=','value':'active','source_text':'active'}],
        'analysis_mode':'aggregate','having':{'operator':'>','mode':'scalar_avg','value':None} if having else None,
        'comparison':None,'top_n':None,'order_desc':True,'order_metric':'m0','output_metrics':['m0'],
        'limit':100,'confidence':.99,'rewritten_question':'current scope'}


def engine(database, payload=None, **kwargs):
    provider = (lambda q,t:deepcopy(payload)) if payload else None
    return Nl2SqlEngine(database, model_plan_provider=provider,
                       metric_catalog_path=database.parent/'absent.json', **kwargs)


@pytest.mark.parametrize('grouping', ['按 Warehouses.category_name 分组，计算', '按 Warehouses.category_name 计算'])
def test_group_sum_compares_average_group_sum_not_raw_rows(database, grouping):
    question = (f'{grouping} Lots.stock 库存合计，只看 active，'
                '只返回库存合计大于所有类别库存合计平均值的类别及其合计。')
    result = engine(database, proposal(), model_fallback=False).answer(question)
    assert result.status == 'ok' and result.plan['planner_source'] == 'model_validated'
    assert result.plan['metric_function'] == 'SUM'
    assert result.plan['having']['mode'] == 'scalar_avg'
    assert len(result.plan['metrics']) == 1
    assert len(result.rows) == 1
    assert set(result.rows[0].values()) == {'B',80.0}
    assert 'SELECT AVG' in result.sql
    assert 'active' in result.parameters
    assert 'WHERE' in result.sql


def test_direct_average_comparator_keeps_inner_average(database):
    question = '按 Warehouses.category_name 分组，只看 active，统计 Lots.stock 平均值高于平均的类别'
    result = engine(database, proposal('AVG'), model_fallback=False).answer(question)
    assert result.status == 'ok' and result.plan['metric_function'] == 'AVG'
    assert len(result.rows) == 1 and set(result.rows[0].values()) == {'B',80.0}


@pytest.mark.parametrize('subject', ['类别库存总和', 'Warehouses.category_nameLots.stock合计', '类别合计库存'])
def test_bound_group_subject_accepts_schema_and_synonymous_sum(database, subject):
    question = f'按 Warehouses.category_name 分组，统计 Lots.stock 合计，只看 active，只返回大于所有{subject}平均值的类别'
    result = engine(database, proposal(), model_fallback=False).answer(question)
    assert result.status == 'ok' and len(result.rows) == 1
    assert set(result.rows[0].values()) == {'B',80.0}


@pytest.mark.parametrize('subject', ['国家库存合计','类别价格合计','类别去年库存合计',
                                    '类别库存最大值','类别库存合计并包含closed'])
def test_different_average_scope_is_not_silently_consumed(database, subject):
    question = f'按 Warehouses.category_name 分组，统计 Lots.stock 合计，只看 active，只返回大于所有{subject}平均值的类别'
    result = engine(database, proposal()).answer(question)
    assert result.status == 'clarification' and result.sql is None
    assert result.clarification_code in {'unverified_average_scope', 'missing_date_column'}


@pytest.mark.parametrize('mutation', ['missing_having','changed_inner_function','extra_avg_metric'])
def test_model_cannot_drop_comparator_or_add_row_average(database, mutation):
    payload = proposal()
    if mutation == 'missing_having':
        payload['having'] = None
    elif mutation == 'changed_inner_function':
        payload['metrics'][0]['function'] = 'AVG'
    else:
        extra = deepcopy(payload['metrics'][0]); extra.update(id='m1',label='row average',function='AVG')
        payload['metrics'].append(extra)
    question = '按 Warehouses.category_name 分组，统计 Lots.stock 库存合计，只看 active，只返回大于所有类别库存合计平均值的类别'
    result = engine(database, payload).answer(question)
    assert result.plan['planner_source'] == 'rules_fallback'
    assert result.status == 'ok' and result.plan['metric_function'] == 'SUM'
    assert len(result.rows) == 1 and set(result.rows[0].values()) == {'B',80.0}


@pytest.mark.parametrize('operator,expected',[('不高于','<='),('不低于','>='),('大于','>'),('低于','<')])
def test_long_group_comparator_preserves_polarity(operator, expected):
    threshold = lexicon.parse_threshold(f'{operator}所有类别库存合计平均值')
    assert threshold is not None and threshold.operator == expected
    assert threshold.reference == '类别库存合计' and threshold.mode == 'scalar_avg'


def test_unbound_subject_cannot_be_bypassed_by_dropping_average(database):
    result = engine(database, proposal(having=False)).answer(
        '按 Warehouses.category_name 分组，统计 Lots.stock 合计，只看 active，只返回大于所有国家库存合计平均值的类别')
    assert result.status == 'clarification' and result.sql is None
    assert result.clarification_code == 'unverified_average_scope'


def test_all_groups_population_is_not_restricted_to_one_output_group(database):
    result = engine(database, proposal()).answer(
        '按 Warehouses.category_name 分组，统计 Lots.stock 合计，只看 A，只返回大于所有类别库存合计平均值的类别')
    assert result.status == 'clarification' and result.sql is None
    assert result.clarification_code == 'unverified_average_scope'


@pytest.mark.parametrize('comparator', ['大于全部平均值', '高于整体平均', '大于总体平均水平'])
@pytest.mark.parametrize('with_model', [False, True])
def test_short_average_population_cannot_reuse_output_group_filter(database, comparator, with_model):
    result = engine(database, proposal() if with_model else None).answer(
        f'按 Warehouses.category_name 分组，统计 Lots.stock 库存合计，只看 A，只返回{comparator}的类别')
    assert result.status == 'clarification' and result.sql is None
    assert result.clarification_code == 'unverified_average_scope'


@pytest.mark.parametrize('comparator', ['大于全部平均值', '高于整体平均'])
def test_short_average_keeps_unfiltered_group_population(database, comparator):
    result = engine(database).answer(
        f'按 Warehouses.category_name 分组，统计 Lots.stock 库存合计，只看 active，只返回{comparator}的类别')
    assert result.status == 'ok'
    assert len(result.rows) == 1 and set(result.rows[0].values()) == {'B', 80.0}


def test_reference_year_is_not_lost_when_a_real_date_column_exists(database):
    with sqlite3.connect(database) as connection:
        connection.execute('ALTER TABLE Lots ADD booked_at DATE')
        connection.execute("UPDATE Lots SET booked_at='2025-01-01'")
    result = engine(database, proposal()).answer(
        '按 Warehouses.category_name 分组，统计 Lots.stock 合计，只看 active，只返回大于所有类别去年库存合计平均值的类别')
    assert result.status == 'clarification' and result.sql is None
    assert result.clarification_code == 'unverified_average_scope'


@pytest.mark.parametrize('reference', ['日期', 'Lots.booked_at', '年份'])
def test_month_group_comparator_cannot_borrow_a_raw_date_or_year_population(database, reference):
    with sqlite3.connect(database) as connection:
        connection.execute('ALTER TABLE Lots ADD booked_at DATE')
        connection.execute("UPDATE Lots SET booked_at='2025-01-01'")
    result = engine(database).answer(
        f'按 Lots.booked_at 的月份分组，统计 Lots.stock 库存合计，只返回大于所有{reference}库存合计平均值的月份')
    assert result.status == 'clarification' and result.sql is None
    assert result.clarification_code == 'unverified_average_scope'


def test_month_comparator_with_explicit_same_grain_uses_group_results(database):
    with sqlite3.connect(database) as connection:
        connection.execute('ALTER TABLE Lots ADD booked_at DATE')
        connection.execute("UPDATE Lots SET booked_at=CASE warehouse_id WHEN 2 THEN '2025-02-01' ELSE '2025-01-01' END")
    result = engine(database).answer(
        '按 Lots.booked_at 的月份分组，统计 Lots.stock 库存合计，只看 active，只返回大于所有月份库存合计平均值的月份')
    assert result.status == 'ok'
    assert result.plan['dimension_transforms'] == {'booked_at':'month'}
    assert len(result.rows) == 1 and set(result.rows[0].values()) == {'2025-02',80.0}
