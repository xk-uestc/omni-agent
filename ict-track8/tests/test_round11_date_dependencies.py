import sqlite3

import pytest

from backend.nl2sql.date_profile import (
    date_candidate_fields, sql_date_dependencies, storage_profiles,
)
from backend.nl2sql.schema import SchemaIntrospector


@pytest.fixture
def snapshot():
    with sqlite3.connect(':memory:') as db:
        db.executescript('''
            CREATE TABLE rental(rental_id INTEGER, customer_id INTEGER,
                                rental_date TEXT, return_date TEXT);
            CREATE TABLE payment(payment_id INTEGER, staff_id INTEGER,
                                 amount REAL, payment_date TEXT);
            CREATE TABLE irrelevant(staff_id INTEGER, event_date TEXT);
            INSERT INTO rental VALUES(1, 7, '2005-08-01 10:00:00', '2005-08-03 12:00:00');
            INSERT INTO payment VALUES(1, 2, 5.5, '2005-07-01 11:00:00');
            INSERT INTO irrelevant VALUES(2, 'not a date');
        ''')
        yield db, SchemaIntrospector().introspect(db, include_row_count=False)


def test_natural_language_rental_time_includes_unmentioned_dependency(snapshot):
    db, tables = snapshot
    result = storage_profiles(db, tables,
        '2005年8月租出、return_date非空的租赁记录数、不同客户数及平均归还天数')
    assert {p['field'] for p in result} == {'rental.rental_date', 'rental.return_date'}
    assert all(p['format'] == 'iso_text' for p in result)


def test_payment_non_date_qualified_field_scopes_natural_year(snapshot):
    db, tables = snapshot
    result = storage_profiles(db, tables, '仍查2005年收款，改按payment.staff_id求amount总额')
    assert [p['field'] for p in result] == ['payment.payment_date']
    assert result[0]['time_comparison']['operator'] == 'JULIANDAY'


def test_qualified_date_does_not_probe_unrelated_owner(snapshot):
    _, tables = snapshot
    assert date_candidate_fields(tables, '按payment.payment_date统计2005年7月收款') == [
        ('payment', 'payment_date')]


def test_temporal_language_with_no_physical_scope_does_not_scan_database(snapshot):
    _, tables = snapshot
    assert date_candidate_fields(tables, '2005年收款总额') == []
    assert date_candidate_fields(tables, '2005年按staff_id统计') == []


def test_non_temporal_question_does_not_expand_table_dates(snapshot):
    _, tables = snapshot
    assert date_candidate_fields(tables, '按payment.staff_id求amount合计') == []
    assert date_candidate_fields(tables, 'return_date非空') == [('rental', 'return_date')]


def test_explicit_dependencies_are_schema_checked_prioritized_and_deduplicated(snapshot):
    db, tables = snapshot
    fields = [('rental', 'rental_date'), ('absent', 'date'), ('payment', 'amount'),
              ('rental', 'rental_date')]
    result = storage_profiles(db, tables, '2005年按payment.staff_id统计',
                              candidate_fields=fields, max_columns=1)
    assert [p['field'] for p in result] == ['rental.rental_date']


def test_whole_column_verification_not_inference_from_temporal_question(snapshot):
    db, tables = snapshot
    db.execute("INSERT INTO payment VALUES(2, 2, 1.0, 'unknown')")
    result = storage_profiles(db, tables, '2005年按payment.staff_id求收款')
    assert result[0]['format'] == 'unknown'
    assert result[0]['non_null_rows'] == 2
    assert 'time_comparison' not in result[0]


def test_expanded_dates_remain_bounded_and_connection_recovers(snapshot):
    db, tables = snapshot
    result = storage_profiles(db, tables, '2005年rental归还平均天数', max_seconds=0)
    assert len(result) == 1
    assert result[0]['verification'] == 'probe_budget_exhausted_or_unavailable'
    assert db.execute('SELECT COUNT(*) FROM rental').fetchone()[0] == 1


@pytest.mark.parametrize('sql, expected', [
    ('SELECT JULIANDAY(r.return_date)-JULIANDAY(r.rental_date) AS days FROM rental AS r',
     [('rental', 'rental_date'), ('rental', 'return_date')]),
    ("WITH t AS (SELECT DATE(p.payment_date) AS month FROM payment p) SELECT month FROM t",
     [('payment', 'payment_date')]),
    ('SELECT payment_date FROM payment', [('payment', 'payment_date')]),
    ('SELECT p.staff_id FROM payment p WHERE EXISTS '
     '(SELECT 1 FROM rental r WHERE r.rental_date < p.payment_date)',
     [('payment', 'payment_date'), ('rental', 'rental_date')]),
    ('SELECT SUM(amount) FROM payment', []),
    ('DELETE FROM payment', []),
    ('SELECT bogus_date FROM payment', []),
    ('SELECT date FROM absent', []),
    ('SELECT payment_date FROM payment; DELETE FROM rental', []),
])
def test_sql_date_dependencies_visit_physical_scopes_only(snapshot, sql, expected):
    _, tables = snapshot
    assert sql_date_dependencies(sql, tables) == expected


def test_ast_dependency_probe_does_not_execute_candidate_sql(snapshot):
    db, tables = snapshot
    fields = sql_date_dependencies(
        "SELECT DATE(p.payment_date) FROM payment p WHERE p.amount > 10000000", tables)
    result = storage_profiles(db, tables, '', candidate_fields=fields)
    # A SELECT that would return no rows is never executed as the date probe.
    assert result[0]['non_null_rows'] == 1
    assert result[0]['format'] == 'iso_text'


def test_ambiguous_bare_date_name_does_not_expand_unmentioned_date_fields(snapshot):
    db, _ = snapshot
    db.execute('CREATE TABLE other(return_date TEXT, created_at TEXT)')
    tables = SchemaIntrospector().introspect(db, include_row_count=False)
    assert set(date_candidate_fields(tables, '2005年return_date非空')) == {
        ('rental', 'return_date'), ('other', 'return_date')}


def test_qualified_reference_keeps_same_named_date_owners_separate(snapshot):
    db, _ = snapshot
    db.execute('CREATE TABLE other(return_date TEXT, created_at TEXT)')
    tables = SchemaIntrospector().introspect(db, include_row_count=False)
    for question in ('2005年rental.return_date非空', '2005年rental . return_date非空'):
        assert set(date_candidate_fields(tables, question)) == {
            ('rental', 'rental_date'), ('rental', 'return_date')}
