"""Schema-only bilingual alias scopes; synthetic databases and no API calls."""
import sqlite3

import pytest

from backend.nl2sql.engine import Nl2SqlEngine


def engine_for(tmp_path, ddl, provider=None):
    database = tmp_path / 'synthetic.sqlite'
    with sqlite3.connect(database) as connection:
        connection.executescript(ddl)
    return Nl2SqlEngine(database, model_plan_provider=provider,
                       metric_catalog_path=tmp_path / 'absent-catalog.json')


INVOICES = '''
CREATE TABLE Invoice(InvoiceId INTEGER PRIMARY KEY, Total REAL NOT NULL);
CREATE TABLE Refund(RefundId INTEGER PRIMARY KEY, Total REAL NOT NULL);
INSERT INTO Invoice VALUES(1,12),(2,8);
INSERT INTO Refund VALUES(1,5);
'''


@pytest.mark.parametrize('question', ['Invoice 的总额合计', '发票的总额合计', '账单总额合计'])
def test_unique_native_or_existing_table_alias_owns_the_local_metric(tmp_path, question):
    result = engine_for(tmp_path, INVOICES).answer(question)
    assert result.status == 'ok'
    assert result.plan['metric_table'] == 'Invoice'
    assert list(result.rows[0].values()) == [20]


@pytest.mark.parametrize('question', ['总额合计', 'Invoice 的总额合计和总额合计'])
def test_bare_occurrence_cannot_borrow_another_occurrences_explicit_owner(tmp_path, question):
    result = engine_for(tmp_path, INVOICES).answer(question)
    assert result.status == 'clarification'
    assert result.clarification_code == 'ambiguous_metric' and result.sql is None


def test_high_confidence_model_cannot_resolve_a_true_metric_ambiguity(tmp_path):
    provider = lambda question, tables: {
        'version': 1, 'table': 'Invoice',
        'metric': {'table': 'Invoice', 'column': 'Total', 'function': 'SUM'},
        'dimensions': [], 'filters': [], 'confidence': .99}
    result = engine_for(tmp_path, INVOICES, provider).answer('总额合计')
    assert result.status == 'clarification' and result.sql is None
    assert result.plan['planner_source'] == 'rules_fallback'
    assert result.clarification_code == 'ambiguous_metric'


def test_shared_chinese_table_alias_keeps_both_possible_owners(tmp_path):
    ddl = INVOICES + '''CREATE TABLE Orders(OrderId INTEGER PRIMARY KEY, Total REAL NOT NULL);
                       INSERT INTO Orders VALUES(1,100);'''
    result = engine_for(tmp_path, ddl).answer('订单的总额合计')
    assert result.status == 'clarification' and result.sql is None
    assert result.clarification_code == 'ambiguous_metric'


@pytest.mark.parametrize('prefix', ['神秘', '非'])
def test_unknown_or_negated_owner_modifier_is_never_consumed_as_schema(tmp_path, prefix):
    result = engine_for(tmp_path, INVOICES).answer(prefix + '发票的总额合计')
    assert result.status == 'clarification' and result.sql is None


COUNTRIES = '''
CREATE TABLE Customer(CustomerId INTEGER PRIMARY KEY, Country TEXT);
CREATE TABLE Invoice(InvoiceId INTEGER PRIMARY KEY, CustomerId INTEGER REFERENCES Customer(CustomerId),
                     Total REAL NOT NULL, BillingCountry TEXT);
INSERT INTO Customer VALUES(1,'East'),(2,'West');
INSERT INTO Invoice VALUES(1,1,12,'North'),(2,2,8,'North');
'''


@pytest.mark.parametrize('question,column,table,groups', [
    ('按客户国家统计发票总额', 'Country', 'Customer', {'East', 'West'}),
    ('按账单国家统计发票总额', 'BillingCountry', 'Invoice', {'North'}),
    ('按 Customer 的国家统计 Invoice 的总额', 'Country', 'Customer', {'East', 'West'}),
])
def test_local_dimension_owner_does_not_borrow_the_metric_source(tmp_path, question, column, table, groups):
    result = engine_for(tmp_path, COUNTRIES).answer(question)
    assert result.status == 'ok'
    assert result.plan['dimension_tables'] == {column: table}
    assert {row[column] for row in result.rows} == groups


def test_bare_country_remains_ambiguous(tmp_path):
    result = engine_for(tmp_path, COUNTRIES).answer('按国家统计发票总额')
    assert result.status == 'clarification' and result.sql is None
    assert result.clarification_code == 'ambiguous_dimension'


def test_table_owner_does_not_choose_between_two_country_roles_in_same_table(tmp_path):
    ddl = COUNTRIES + "ALTER TABLE Invoice ADD Country TEXT; UPDATE Invoice SET Country='Other';"
    result = engine_for(tmp_path, ddl).answer('按发票国家统计发票总额')
    assert result.status == 'clarification' and result.sql is None
    assert result.clarification_code == 'ambiguous_dimension'


def test_numeric_business_alias_has_only_its_local_group_role(tmp_path):
    ddl = '''CREATE TABLE Product(ProductId INTEGER PRIMARY KEY,rating REAL,quantity INTEGER);
             INSERT INTO Product VALUES(1,3,2),(2,3,4),(3,5,10);'''
    result = engine_for(tmp_path, ddl).answer('按评分统计数量合计')
    assert result.status == 'ok'
    assert result.plan['dimension_tables'] == {'rating': 'Product'}
    assert {(row['rating'], next(value for key, value in row.items() if key != 'rating'))
            for row in result.rows} == {(3, 6), (5, 10)}
    assert all(link['role'] == 'dimension' for link in result.plan['links'] if link['column'] == 'rating')


@pytest.mark.parametrize('declaration', ['INTEGER', 'INTEGER NOT NULL'])
@pytest.mark.parametrize('question', ['统计数量', '统计数量合计'])
def test_count_cue_cannot_cross_the_syntax_and_field_alias_boundary(tmp_path, declaration, question):
    ddl = f'''CREATE TABLE Warehouse(warehouse_id INTEGER PRIMARY KEY,quantity {declaration});
              INSERT INTO Warehouse VALUES(1,3),(2,7);'''
    result = engine_for(tmp_path, ddl).answer(question)
    assert result.status == 'ok' and list(result.rows[0].values()) == [10]
    assert result.plan['metric_function'] == 'SUM'


@pytest.mark.parametrize('question', ['数量计数', 'quantity 计数'])
def test_explicit_nullable_field_count_keeps_the_existing_guard(tmp_path, question):
    ddl = '''CREATE TABLE Warehouse(warehouse_id INTEGER PRIMARY KEY,quantity INTEGER);
             INSERT INTO Warehouse VALUES(1,3),(2,NULL);'''
    result = engine_for(tmp_path, ddl).answer(question)
    assert result.status == 'clarification' and result.sql is None
    assert result.clarification_code == 'ambiguous_count_semantics'


def test_explicit_nonnullable_field_count_remains_a_count(tmp_path):
    ddl = '''CREATE TABLE Warehouse(warehouse_id INTEGER PRIMARY KEY,quantity INTEGER NOT NULL);
             INSERT INTO Warehouse VALUES(1,3),(2,7);'''
    result = engine_for(tmp_path, ddl).answer('数量计数')
    assert result.status == 'ok' and list(result.rows[0].values()) == [2]
    assert result.plan['metric_function'] == 'COUNT'
