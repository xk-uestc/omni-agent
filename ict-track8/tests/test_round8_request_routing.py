"""Natural requests exercise actual bounded SQL execution, not score fixtures."""
import sqlite3
from datetime import date
import pytest

from backend.nl2sql.complex_query import CHECKS, needs_complex_query
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.result_scope import requests_complete_result
from backend.nl2sql.result_scope import configure_complete_scope
from backend.nl2sql.models import QueryPlan
from backend.nl2sql.result_artifact import ResultBudgets
from backend.nl2sql.security import SqlSafetyError


class Provider:
    supports_complex_queries = True
    reference_date = date(2026, 1, 1)
    catalog = None
    audit = {}

    def __init__(self, sql):
        self.sql = sql
        self.client = self
        self.calls = []

    def generate(self, instructions, context, schema, **options):
        self.calls.append(options['name'])
        if options['name'] == 'complex_sql_proposal':
            return {'sql': self.sql, 'clarification': None}
        return {'approved': True, 'checks': {k: True for k in CHECKS}, 'clarification': None}


@pytest.mark.parametrize('question', [
    '按invoice.customer_id合计invoice.amount，经customers.id关联显示customers.name',
    '各部门合计高于这些部门合计平均值的部门及金额',
    '返回份数排名前三个不同名次的全部产品，含并列',
    '列出每个产品的库存数量，没有库存时为0',
    '先按客户分组，计算每组平均值与平均值的平均',
])
def test_relational_request_reaches_full_schema_channel(question):
    assert needs_complex_query(question)


@pytest.mark.parametrize('question,expected', [
    ('列出全部invoice的id和amount，按id升序，不设置数量上限', True),
    ('显示所有客户，不限100行', True),
    ('按客户分组，全部返回', True),
    ('list all invoices ordered by id', True),
    ('查询总金额，不限制年份或付款日期', False),
    ('查询电影平均长度，不设置日期限制', False),
    ('不要返回全部记录，只统计总金额', False),
    ('do not list all records; count them', False),
])
def test_complete_request_preserves_date_and_negative_semantics(question, expected):
    assert requests_complete_result(question) is expected


def test_natural_all_rows_keeps_preview_cap_and_persists_full_sql_result(tmp_path):
    path = tmp_path/'source.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE invoice(id INTEGER PRIMARY KEY, amount REAL)')
        connection.executemany('INSERT INTO invoice VALUES(?,?)', [(i, i*1.25) for i in range(1, 161)])
    model = Provider('SELECT id,amount FROM invoice ORDER BY id')
    engine = Nl2SqlEngine(path, model_plan_provider=model, result_artifact_dir=tmp_path/'results')
    answer = engine.answer('列出全部invoice的id和amount，按id升序，不限100行')
    assert answer.status == 'ok'
    assert len(answer.rows) == 100
    assert answer.rows[0] == {'id': 1, 'amount': 1.25}
    metadata = answer.provenance['complete_result']
    assert metadata['status'] == 'complete' and metadata['row_count'] == 160
    assert metadata['preview_truncated'] is True
    assert answer.plan['semantic_row_limit'] is None
    assert 'LIMIT' not in answer.sql.upper()
    assert model.calls == ['complex_sql_proposal', 'complex_sql_independent_review']


def test_simple_scalar_average_stays_in_typed_channel():
    assert not needs_complex_query('各地区销售额高于平均')


@pytest.mark.parametrize('prefix', ['不要只返回5行，', '不要返回5行预览，',
                                  '不要只返回五行，', "do not return only 5 rows; "])
def test_negated_caps_cannot_truncate_a_complete_execution(tmp_path, prefix):
    path = tmp_path/'negative-cap.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE invoice(id INTEGER PRIMARY KEY)')
        connection.executemany('INSERT INTO invoice VALUES(?)', [(i,) for i in range(160)])
    model = Provider('SELECT id FROM invoice ORDER BY id')
    engine = Nl2SqlEngine(path, model_plan_provider=model, result_artifact_dir=tmp_path/'results')
    result = engine.answer(prefix+'列出全部invoice的id，按id升序')
    assert result.status == 'ok'
    assert result.provenance['complete_result']['row_count'] == 160
    assert result.plan['semantic_row_limit'] is None
    assert result.plan['preview_row_limit'] is None
    assert 'LIMIT' not in result.sql.upper()


@pytest.mark.parametrize('question,expected', [
    ('列出全部invoice的id，不限制为5行', None),
    ('不要只返回5行，只返回10行', 10),
    ('不要返回5行预览，最多返回8行', 8),
    ('do not return 5 rows; return 10 rows', 10),
    ('do not return only 5 rows; list all records', None),
    ('list all records; no limit 5', None),
    ('不要限制为5行，返回10行', 10),
])
def test_row_request_polarity_keeps_independent_positive_caps(question, expected):
    plan = QueryPlan()
    configure_complete_scope(plan, question)
    assert plan.semantic_row_limit == expected


@pytest.mark.parametrize('question', ['不要返回5行，只返回5行',
                                    '不要只返回五行，最多返回五行',
                                    'do not return 5 rows; return 5 rows'])
def test_contradictory_row_requests_require_clarification(question):
    with pytest.raises(SqlSafetyError, match='positive_negative_cap_conflict'):
        configure_complete_scope(QueryPlan(), question)


@pytest.mark.parametrize('prefix', [
    '不想只返回5行，', '不希望只返回5行，', '不要仅仅返回5行，',
    '不要限制返回5行，', "don't just return 5 rows; ",
    '不能不返回5行，', '不可以不返回5行，',
    'do not not return 5 rows; ', 'I would rather not return 5 rows; ',
    '不想只预览5行，', "don't just preview 5 rows; ",
    '不想只对invoice.id返回5行，',
    '不希望对invoice.amount为2.5的记录只返回5行，',
    '不想对金额2.5的记录只预览5行，',
    'I would rather not for invoice.id return 5 rows; ',
])
def test_unresolved_negative_cap_never_authorizes_a_positive_substring(tmp_path, prefix):
    question = prefix + '列出全部invoice的id'
    plan = QueryPlan()
    with pytest.raises(SqlSafetyError, match='negative_scope_ambiguous'):
        configure_complete_scope(plan, question)
    assert not plan.complete_results and plan.semantic_row_limit is None
    path = tmp_path / 'negative-unknown.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE invoice(id INTEGER PRIMARY KEY)')
        connection.executemany('INSERT INTO invoice VALUES(?)', [(i,) for i in range(160)])
    engine = Nl2SqlEngine(path, model_plan_provider=Provider('SELECT id FROM invoice ORDER BY id'),
                         result_artifact_dir=tmp_path / 'results')
    with pytest.raises(SqlSafetyError, match='negative_scope_ambiguous'):
        engine.answer(question)
    assert not list((tmp_path / 'results').glob('*.json'))


def test_separate_business_exclusion_does_not_negate_a_positive_row_request():
    plan = QueryPlan()
    configure_complete_scope(plan, '不含取消的订单，列出全部invoice的id，只返回5行')
    assert plan.semantic_row_limit == 5


@pytest.mark.parametrize('question', [
    '对invoice.id只返回5行',
    '对invoice.amount为2.5的记录只返回5行',
    '不含取消的invoice.id记录，金额2.5的记录只返回5行',
])
def test_physical_columns_and_decimal_values_preserve_positive_caps(question):
    plan = QueryPlan()
    configure_complete_scope(plan, question)
    assert plan.semantic_row_limit == 5


def test_qualified_physical_group_does_not_turn_an_id_into_a_second_metric():
    assert needs_complex_query('按invoice.customer_id分组，求invoice.amount合计')


def test_single_character_rating_binds_only_to_observed_schema_value(tmp_path):
    path = tmp_path/'ratings.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE reviews(id INTEGER PRIMARY KEY,rating TEXT)')
        connection.executemany('INSERT INTO reviews VALUES(?,?)', [(1, 'R'), (2, 'PG')])
    engine = Nl2SqlEngine(path)
    slots = engine.analyze_slots('R评级')
    assert any(value.table == 'reviews' and value.column == 'rating' and value.value == 'R'
               for value in slots['values'])
    assert not engine.analyze_slots('X评级')['values']


@pytest.mark.parametrize('group',['每个客户','每组','每位客户'])
def test_per_group_latest_row_is_not_a_global_result_limit(group):
    plan=QueryPlan()
    scope=configure_complete_scope(plan,f'列出{group}最新一条记录，时间相同则id最大的胜出')
    assert scope['semantic_row_limit'] is None


def test_relational_groups_deliver_complete_artifact_without_all_keyword(tmp_path):
    path = tmp_path/'groups.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE invoice(id INTEGER PRIMARY KEY, amount REAL)')
        connection.executemany('INSERT INTO invoice VALUES(?,?)', [(i, 2.5) for i in range(1, 161)])
    model = Provider('SELECT id,SUM(amount) AS total FROM invoice GROUP BY id ORDER BY id')
    engine = Nl2SqlEngine(path, model_plan_provider=model, result_artifact_dir=tmp_path/'results')
    answer = engine.answer('列出按invoice.id分组的invoice.amount合计，按id升序')
    assert answer.status == 'ok' and len(answer.rows) == 100
    metadata = answer.provenance['complete_result']
    assert metadata['status'] == 'complete' and metadata['row_count'] == 160
    page = engine.complete_result_page(metadata['artifact_id'], offset=100)
    assert page['columns'] == ['id', 'total'] and page['rows'][-1] == [160, 2.5]
    assert 'LIMIT' not in answer.sql.upper()


def test_relational_budget_exhaustion_never_reports_complete_rows(tmp_path):
    path=tmp_path/'budget.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE invoice(id INTEGER PRIMARY KEY, amount REAL)')
        connection.executemany('INSERT INTO invoice VALUES(?,?)',[(i,1.0) for i in range(161)])
    model=Provider('SELECT id,amount FROM invoice ORDER BY id')
    engine=Nl2SqlEngine(path,model_plan_provider=model,result_artifact_dir=tmp_path/'results',
        complete_result_budgets=ResultBudgets(max_rows=120))
    result=engine.answer('列出invoice的id和amount，按id升序')
    assert result.status=='incomplete' and result.result_state=='partial_rows'
    metadata=result.provenance['complete_result']
    assert metadata['status']=='partial' and metadata['artifact_id'] is None
    assert metadata['cursor_eof_verified'] is False and metadata['row_count'] is None


def test_explicit_user_cap_is_enforced_when_model_omits_limit(tmp_path):
    path=tmp_path/'cap.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE invoice(id INTEGER PRIMARY KEY)')
        connection.executemany('INSERT INTO invoice VALUES(?)',[(i,) for i in range(160)])
    engine=Nl2SqlEngine(path,model_plan_provider=Provider('SELECT id FROM invoice ORDER BY id'),
        result_artifact_dir=tmp_path/'results')
    result=engine.answer('列出invoice的id，按id升序，只返回5行')
    assert result.status=='ok' and len(result.rows)==5
    assert result.provenance['complete_result']['row_count']==5
    assert result.sql.endswith('LIMIT ?') and result.parameters[-1]==5


@pytest.mark.parametrize('suffix',[' LIMIT 100',' LIMIT 100 OFFSET 5',' LIMIT 4'])
def test_model_review_cannot_approve_a_conflicting_final_cap(tmp_path,suffix):
    path=tmp_path/'invented-cap.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE invoice(id INTEGER PRIMARY KEY)')
        connection.execute('INSERT INTO invoice VALUES(1)')
    engine=Nl2SqlEngine(path,model_plan_provider=Provider('SELECT id FROM invoice ORDER BY id'+suffix),
        result_artifact_dir=tmp_path/'results')
    with pytest.raises(SqlSafetyError):
        engine.answer('列出全部invoice的id，按id升序')
