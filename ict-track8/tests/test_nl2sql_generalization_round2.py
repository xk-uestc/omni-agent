"""Quoted physical names generalize safely through the real Schema allowlist."""
from copy import deepcopy
import sqlite3

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.metric_compiler import MetricCompiler
from backend.nl2sql.model_contract import ModelPlanError, ModelPlanValidator
from backend.nl2sql.planner import SingleTablePlanner
from backend.nl2sql.schema import SchemaIntrospector
from backend.nl2sql.security import SqlSafetyError, execute_read_only


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def payload(table='Order Header', column='Gross Amount', *, version=1):
    metric = {'table': table, 'column': column, 'function': 'SUM', 'label': 'Gross Amount'}
    base = {'version': version, 'table': table, 'metric': metric, 'dimensions': [], 'filters': [],
            'analysis_mode': 'aggregate', 'limit': 100, 'confidence': 0.95}
    if version == 2:
        base.pop('table')
        base.pop('metric')
        base['metrics'] = [{**metric, 'id': 'amount', 'unit': 'unknown', 'currency': None, 'missing': 'null'}]
        base['derived_metrics'] = []
    return base


def validate_and_execute(connection, proposed):
    tables = SchemaIntrospector().introspect(connection)
    plan = ModelPlanValidator().validate(proposed, tables, question='Explicit physical column aggregate')
    if plan.metrics:
        sql, params = MetricCompiler(SingleTablePlanner()).compile(plan, tables)
    else:
        sql, params = SingleTablePlanner().build_sql(plan)
    return plan, sql, execute_read_only(connection, sql, params)


@pytest.mark.parametrize('table,column', [
    ('Order Header', 'Gross Amount'), ('order-header', 'net-total'),
    ('Sales.Header', 'Invoice.Amount'), ('select', 'from'),
    ('客户 资料', '支付 金额'), ('Ledger "Archived"', 'gross "amount"'),
    ('  Ledger  ', '  Amount  '), ('Import; DROP TABLE archive', 'Amount'),
])
@pytest.mark.parametrize('version', [1, 2])
def test_real_quoted_names_are_bound_exactly_and_return_correct_aggregate(table, column, version):
    with sqlite3.connect(':memory:') as connection:
        connection.execute(f'CREATE TABLE {quote(table)} ({quote(column)} REAL NOT NULL)')
        connection.executemany(f'INSERT INTO {quote(table)} VALUES (?)', [(12.5,), (8.75,), (-1.25,)])
        plan, sql, (columns, rows) = validate_and_execute(connection, payload(table, column, version=version))
        assert plan.metric_table == table and plan.metric_column == column
        assert rows == ({'Gross Amount': 20.0},)
        assert columns == ('Gross Amount',)
        assert quote(table) in sql and quote(column) in sql
        assert connection.execute(f'SELECT COUNT(*) FROM {quote(table)}').fetchone()[0] == 3


@pytest.fixture
def joined_connection():
    connection = sqlite3.connect(':memory:')
    connection.executescript('''
        CREATE TABLE "Customer Group" ("Group ID" INTEGER PRIMARY KEY, "Region-Code" TEXT NOT NULL);
        CREATE TABLE "Order.Header" (
            "Order ID" INTEGER PRIMARY KEY, "Group ID" INTEGER NOT NULL,
            "Gross Amount" REAL NOT NULL, "Order Date" DATE NOT NULL,
            FOREIGN KEY("Group ID") REFERENCES "Customer Group"("Group ID"));
        INSERT INTO "Customer Group" VALUES (1, 'East'), (2, 'West');
        INSERT INTO "Order.Header" VALUES
            (1, 1, 12.5, '2040-01-01'), (2, 1, 8.75, '2040-02-01'), (3, 2, 40, '2040-03-01');
    ''')
    try:
        yield connection
    finally:
        connection.close()


@pytest.mark.parametrize('version', [1, 2])
@pytest.mark.parametrize('hint', [None, ['Order.Header', 'Customer Group'], 'Order.Header>Customer Group'])
def test_quoted_cross_table_dimension_and_filter_use_real_foreign_key(joined_connection, version, hint):
    proposed = payload('Order.Header', 'Gross Amount', version=version)
    proposed['dimensions'] = [{'table': 'Customer Group', 'column': 'Region-Code', 'label': 'Region'}]
    proposed['filters'] = [{'table': 'Customer Group', 'column': 'Region-Code', 'operator': '=', 'value': 'East'}]
    if hint:
        proposed['join_path'] = hint
    plan, sql, (_, rows) = validate_and_execute(joined_connection, proposed)
    assert rows == ({'Region': 'East', 'Gross Amount': 21.25},)
    assert plan.join_path and '"Order.Header"."Group ID"' in sql
    assert 'East' not in sql


def test_comparison_window_can_bind_a_quoted_date_field(joined_connection):
    proposed = payload('Order.Header')
    proposed['comparison'] = {'mode': '同比', 'date_table': 'Order.Header', 'date_column': 'Order Date',
        'current_start': '2040-01-01', 'current_end': '2041-01-01',
        'previous_start': '2039-01-01', 'previous_end': '2040-01-01'}
    plan, sql, (_, rows) = validate_and_execute(joined_connection, proposed)
    assert plan.comparison_period['date_column'] == 'Order Date'
    assert '"Order.Header"."Order Date"' in sql
    assert rows[0]['本期Gross Amount'] == 61.25
    assert rows[0]['同期Gross Amount'] is None


@pytest.mark.parametrize('field,bad', [
    ('table', 'Missing Table'), ('metric.table', 'Order Header; DROP TABLE archive'),
    ('metric.column', 'Gross Amount) FROM archive'), ('metric.column', 'Gross Amount '),
    ('metric.column', 'gross amount'),
])
def test_physical_names_must_exist_exactly_not_be_sql_or_normalized_guesses(field, bad):
    with sqlite3.connect(':memory:') as connection:
        connection.execute('CREATE TABLE "Order Header" ("Gross Amount" REAL NOT NULL)')
        proposed = payload()
        if field == 'table':
            proposed['table'] = bad
        else:
            proposed['metric'][field.split('.')[1]] = bad
        with pytest.raises(ModelPlanError, match='不存在'):
            ModelPlanValidator().validate(proposed, SchemaIntrospector().introspect(connection), question='Aggregate')
        assert connection.execute('SELECT COUNT(*) FROM "Order Header"').fetchone()[0] == 0


@pytest.mark.parametrize('bad', ['', ' ', 'Name\x00Suffix', 'Name\nSuffix', 'Name\tSuffix', 'Name\x7fSuffix',
                                 'Name\x85Suffix', 'Name\x9fSuffix', 'a' * 129, 9])
def test_control_empty_or_overbudget_schema_names_are_rejected_before_sql(bad):
    with sqlite3.connect(':memory:') as connection:
        connection.execute('CREATE TABLE "Order Header" ("Gross Amount" REAL NOT NULL)')
        proposed = payload()
        proposed['metric']['column'] = bad
        with pytest.raises(ModelPlanError, match='标识符'):
            ModelPlanValidator().validate(proposed, SchemaIntrospector().introspect(connection), question='Aggregate')


@pytest.mark.parametrize('field', ['id', 'derived_id', 'order_metric', 'output_metric'])
def test_expression_ids_keep_strict_identifier_contract(field):
    with sqlite3.connect(':memory:') as connection:
        connection.execute('CREATE TABLE "Order Header" ("Gross Amount" REAL NOT NULL)')
        proposed = payload(version=2)
        if field == 'id':
            proposed['metrics'][0]['id'] = 'amount value'
        elif field == 'derived_id':
            proposed['derived_metrics'] = [{'id': 'amount value', 'label': 'Derived', 'expression': {'ref': 'amount'}}]
        elif field == 'order_metric':
            proposed['order_metric'] = 'amount value'
        else:
            proposed['output_metrics'] = ['amount value']
        with pytest.raises(ModelPlanError, match='非法字符'):
            ModelPlanValidator().validate(proposed, SchemaIntrospector().introspect(connection), question='Aggregate')


def test_unknown_join_path_cannot_escape_the_schema_allowlist(joined_connection):
    proposed = payload('Order.Header')
    proposed['join_path'] = ['Order.Header', 'Customer Group', 'Secrets']
    proposed['dimensions'] = [{'table': 'Customer Group', 'column': 'Region-Code', 'label': 'Region'}]
    with pytest.raises(ModelPlanError, match='join_path.table 不存在'):
        ModelPlanValidator().validate(proposed, SchemaIntrospector().introspect(joined_connection), question='Aggregate')


def test_sql_safety_gate_still_rejects_raw_write_and_multiple_statements(joined_connection):
    for sql in ('DELETE FROM "Order.Header"', 'SELECT * FROM "Order.Header"; DROP TABLE "Customer Group"'):
        with pytest.raises(SqlSafetyError):
            execute_read_only(joined_connection, sql)
    assert joined_connection.execute('SELECT COUNT(*) FROM "Order.Header"').fetchone()[0] == 3


def test_full_engine_accepts_schema_grounded_model_with_spaces_without_rules_fallback(tmp_path):
    path = tmp_path / 'quoted-business.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE "Order Header" ("Order ID" INTEGER PRIMARY KEY, "Gross Amount" REAL NOT NULL);
            INSERT INTO "Order Header" VALUES (1, 12.5), (2, 8.75);
        ''')
    proposed = payload()
    engine = Nl2SqlEngine(path, model_plan_provider=lambda question, tables: deepcopy(proposed), model_fallback=False)
    result = engine.answer('Order Header的Gross Amount合计')
    assert result.status == 'ok' and result.rows == ({'总Gross Amount': 21.25},)
    assert result.plan['planner_source'] == 'model_validated'
    assert not result.plan['planner_audit']['fallback']


def test_same_named_numeric_fields_remain_semantically_ambiguous_after_quote_support(tmp_path):
    path = tmp_path / 'ambiguous-business.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE "Order Header" ("Gross Amount" REAL NOT NULL);
            CREATE TABLE "Refund Header" ("Gross Amount" REAL NOT NULL);
            INSERT INTO "Order Header" VALUES (12.5);
            INSERT INTO "Refund Header" VALUES (8.75);
        ''')
    engine = Nl2SqlEngine(path, model_plan_provider=lambda question, tables: payload())
    result = engine.answer('Gross Amount合计')
    assert result.status == 'clarification' and result.clarification_code == 'ambiguous_metric'
    assert result.sql is None


def test_explicit_owner_of_same_named_field_remains_executable(tmp_path):
    path = tmp_path / 'owned-business.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE "Order Header" ("Gross Amount" REAL NOT NULL);
            CREATE TABLE "Refund Header" ("Gross Amount" REAL NOT NULL);
            INSERT INTO "Order Header" VALUES (12.5);
            INSERT INTO "Refund Header" VALUES (8.75);
        ''')
    engine = Nl2SqlEngine(path, model_plan_provider=lambda question, tables: payload(), model_fallback=False)
    result = engine.answer('Order Header的Gross Amount合计')
    assert result.status == 'ok' and result.rows == ({'总Gross Amount': 12.5},)
    assert result.plan['planner_source'] == 'model_validated'


@pytest.mark.parametrize('explicit_owner', [False, True])
def test_ambiguous_dimension_requires_owner_even_for_high_confidence_model(tmp_path, explicit_owner):
    path = tmp_path / 'dimensions-business.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE "Customer Group" ("Group ID" INTEGER PRIMARY KEY, "Region-Code" TEXT NOT NULL);
            CREATE TABLE "Order.Header" ("Order ID" INTEGER PRIMARY KEY, "Group ID" INTEGER NOT NULL,
                "Region-Code" TEXT NOT NULL, "Gross Amount" REAL NOT NULL,
                FOREIGN KEY("Group ID") REFERENCES "Customer Group"("Group ID"));
            INSERT INTO "Customer Group" VALUES (1, 'CustomerRegion');
            INSERT INTO "Order.Header" VALUES (1, 1, 'OrderRegion', 12.5);
        ''')
    proposed = payload('Order.Header')
    proposed['dimensions'] = [{'table': 'Order.Header', 'column': 'Region-Code', 'label': 'Region-Code'}]
    engine = Nl2SqlEngine(path, model_plan_provider=lambda question, tables: deepcopy(proposed))
    field = 'Order.Header的Region-Code' if explicit_owner else 'Region-Code'
    result = engine.answer('按' + field + '统计Order.Header的Gross Amount合计')
    if explicit_owner:
        assert result.status == 'ok' and result.plan['planner_source'] == 'model_validated'
        assert result.rows == ({'Region-Code': 'OrderRegion', '总Gross Amount': 12.5},)
    else:
        assert result.status == 'clarification' and result.clarification_code == 'ambiguous_dimension'
        assert result.sql is None
