"""Deterministic lineage and NULL-row count regressions for complex SQL."""
from datetime import date
from types import SimpleNamespace
import sqlite3

import pytest

from backend.nl2sql.complex_query import (CHECKS, _verify_count_projection,
    explicit_null_row_count_fields, propose_complex)
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.schema import SchemaIntrospector
from backend.nl2sql.security import SqlSafetyError


class NullCountRepairClient:
    def __init__(self):
        self.calls = []

    def generate(self, instructions, context, schema, **kwargs):
        name = kwargs['name']
        self.calls.append(name)
        if name == 'complex_sql_proposal':
            return {'sql': 'SELECT COUNT(picture) AS n FROM staff WHERE picture IS NULL',
                    'clarification': None}
        if name == 'complex_sql_structural_repair':
            assert context['structural_failure'] == 'complex_query_null_row_count_requires_row_projection'
            return {'sql': 'SELECT COUNT(*) AS n FROM staff WHERE picture IS NULL',
                    'clarification': None}
        return {'approved': True, 'checks': {key: True for key in CHECKS}, 'clarification': None}


class DateRepairClient:
    def __init__(self):
        self.calls = []
        self.repair_context = None

    def generate(self, instructions, context, schema, **kwargs):
        name = kwargs['name']
        self.calls.append(name)
        if name == 'complex_sql_proposal':
            return {'sql': "SELECT COUNT(*) AS n FROM rental WHERE rental_date >= '2005-06-01' "
                    "AND rental_date < '2005-07-01'", 'clarification': None}
        if name == 'complex_sql_structural_repair':
            self.repair_context = context
            return {'sql': "SELECT COUNT(*) AS n FROM rental AS r WHERE JULIANDAY(r.rental_date) "
                    ">= JULIANDAY('2005-06-01') AND JULIANDAY(r.rental_date) < JULIANDAY('2005-07-01')",
                    'clarification': None}
        return {'approved': True, 'checks': {key: True for key in CHECKS}, 'clarification': None}


def _tables(ddl):
    with sqlite3.connect(':memory:') as db:
        db.executescript(ddl)
        return SchemaIntrospector().introspect(db, include_row_count=False)


def test_verified_iso_date_range_gets_structurally_repaired_before_review():
    tables = _tables('CREATE TABLE rental(rental_id INTEGER PRIMARY KEY, rental_date TEXT);')
    client = DateRepairClient()
    provider = SimpleNamespace(client=client, reference_date=date(2026, 1, 1),
        catalog=None, audit={}, supports_complex_queries=True)
    profiles = [{'field': 'rental.rental_date', 'format': 'iso_text', 'time_comparison': {
        'operator': 'JULIANDAY',
        'verification': 'whole_column_sqlite_parse_non_null_values',
        'text_order_verified': False,
        'precision_contract': 'native_sqlite_time_semantics'}}]
    plan, compiled = propose_complex(provider,
        '统计rental在2005年6月的记录总数。', tables,
        date_profiles=profiles, date_profile_loader=lambda columns, existing: existing)
    assert compiled is not None
    assert 'JULIANDAY("r"."rental_date")' in compiled[0]
    assert client.calls == [
        'complex_sql_proposal', 'complex_sql_structural_repair', 'complex_sql_independent_review']
    assert client.repair_context['structural_failure'] == 'complex_query_verified_date_requires_julianday'
    assert plan.model_plan_diagnostics['structural_attempts'][0]['status'] == 'rejected'


def test_count_alias_resolves_back_to_requested_physical_field():
    tables = _tables('CREATE TABLE rental(rental_id INTEGER PRIMARY KEY, return_date TEXT);')
    _verify_count_projection(
        '统计return_date非空的记录数。',
        'SELECT COUNT(r.return_date) AS n FROM rental AS r WHERE r.return_date IS NOT NULL',
        tables)


def test_null_row_count_identifies_unique_explicit_nullable_field():
    tables = _tables('CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture BLOB);')
    assert explicit_null_row_count_fields('统计全部staff中picture为NULL的记录数。', tables) == [
        ('staff', 'picture')]


def test_null_row_count_rejects_count_of_the_null_field():
    tables = _tables('CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture BLOB);')
    with pytest.raises(SqlSafetyError, match='complex_query_null_row_count_requires_row_projection'):
        _verify_count_projection('统计全部staff中picture为NULL的记录数。',
            'SELECT COUNT(staff.picture) AS n FROM staff WHERE staff.picture IS NULL', tables)


def test_null_row_count_requires_the_matching_null_filter():
    tables = _tables('CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture BLOB);')
    with pytest.raises(SqlSafetyError, match='complex_query_null_row_count_requires_null_predicate'):
        _verify_count_projection('统计全部staff中picture为NULL的记录数。',
            'SELECT COUNT(*) AS n FROM staff', tables)


@pytest.mark.parametrize('sql', [
    'SELECT COUNT(*) AS n FROM staff WHERE picture IS NOT NULL',
    'SELECT COUNT(*) AS n FROM staff WHERE picture IS NULL OR staff_id = 1',
])
def test_null_count_does_not_treat_negated_or_disjunctive_predicate_as_scope(sql):
    tables = _tables('CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture BLOB);')
    with pytest.raises(SqlSafetyError, match='complex_query_null_row_count_requires_null_predicate'):
        _verify_count_projection('统计全部staff中picture为NULL的记录数。', sql, tables)


@pytest.mark.parametrize('aggregate', [
    'COUNT(*) FILTER (WHERE picture IS NULL AND staff_id > 10)',
    'SUM(CASE WHEN picture IS NULL AND staff_id > 10 THEN 1 ELSE 0 END)',
])
def test_conditional_null_count_rejects_extra_predicates_that_can_undercount(aggregate):
    tables = _tables('CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture BLOB);')
    with pytest.raises(SqlSafetyError, match='complex_query_null_row_count_requires_null_predicate'):
        _verify_count_projection('统计全部staff中picture为NULL的记录数。',
            f'SELECT {aggregate} AS n FROM staff', tables)


@pytest.mark.parametrize('sql', [
    'SELECT COUNT(*) AS n FROM staff WHERE picture IS NULL',
    'SELECT COUNT(staff.staff_id) AS n FROM staff WHERE picture IS NULL',
])
def test_null_row_count_accepts_rows_or_schema_verified_nonnull_key(sql):
    tables = _tables('CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture BLOB);')
    _verify_count_projection('统计全部staff中picture为NULL的记录数。', sql, tables)


def test_null_and_nonnull_counts_of_same_field_accept_conditional_aggregates():
    tables = _tables('CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture BLOB);')
    _verify_count_projection(
        '统计staff记录总数、picture非空记录数、picture为空的记录数及其比例。',
        'SELECT COUNT(*) AS total_count, COUNT(picture) AS nonnull_count, '
        'SUM(CASE WHEN picture IS NULL THEN 1 ELSE 0 END) AS null_count, '
        '1.0 * SUM(CASE WHEN picture IS NULL THEN 1 ELSE 0 END) / COUNT(*) AS null_ratio '
        'FROM staff', tables)


def test_null_count_accepts_filtered_count_and_count_case():
    tables = _tables('CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture BLOB);')
    _verify_count_projection('统计全部staff中picture为NULL的记录数，同时统计picture非空记录数。',
        'SELECT COUNT(*) FILTER (WHERE picture IS NULL) AS null_count, '
        'COUNT(picture) AS nonnull_count FROM staff', tables)
    _verify_count_projection('统计全部staff中picture为NULL的记录数。',
        'SELECT COUNT(CASE WHEN picture IS NULL THEN 1 END) AS null_count FROM staff', tables)


def test_null_and_nonnull_count_cannot_share_query_wide_null_filter():
    tables = _tables('CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture BLOB);')
    with pytest.raises(SqlSafetyError,
        match='complex_query_null_and_nonnull_count_scope_requires_separate_query'):
        _verify_count_projection('统计staff记录总数、picture非空记录数、picture为空的记录数。',
            'SELECT COUNT(*) AS null_count, COUNT(staff.picture) AS nonnull_count '
            'FROM staff WHERE picture IS NULL', tables)


def test_null_count_repair_executes_with_count_star(tmp_path):
    path = tmp_path / 'nullable-source.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture TEXT);"
                         "INSERT INTO staff VALUES(1,'x'),(2,NULL),(3,NULL);")
    client = NullCountRepairClient()
    provider = SimpleNamespace(client=client, reference_date=date(2026, 1, 1),
        catalog=None, audit={}, supports_complex_queries=True)
    result = Nl2SqlEngine(path, model_plan_provider=provider).answer(
        '统计全部staff中picture为NULL的记录数。')
    assert result.status == 'ok' and result.rows == ({'n': 2},)
    assert 'COUNT(*)' in result.sql and 'IS NULL' in result.sql
    assert client.calls == [
        'complex_sql_proposal', 'complex_sql_structural_repair', 'complex_sql_independent_review']


def test_null_nonnull_and_total_counts_return_correct_values(tmp_path):
    path = tmp_path / 'mixed-null-counts.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture TEXT);"
                         "INSERT INTO staff VALUES(1,'a'),(2,NULL),(3,'b'),(4,NULL),(5,'c');")
    sql = ('SELECT COUNT(*) AS total_count, COUNT(s.picture) AS nonnull_count, '
           'SUM(CASE WHEN s.picture IS NULL THEN 1 ELSE 0 END) AS null_count, '
           '1.0 * SUM(CASE WHEN s.picture IS NULL THEN 1 ELSE 0 END) / COUNT(*) AS null_ratio '
           'FROM staff AS s')
    class ConditionalNullCountClient:
        def generate(self, instructions, context, schema, **kwargs):
            if kwargs['name'] == 'complex_sql_proposal':
                return {'sql': sql, 'clarification': None}
            return {'approved': True, 'checks': {key: True for key in CHECKS}, 'clarification': None}

    provider = SimpleNamespace(client=ConditionalNullCountClient(), reference_date=date(2026, 1, 1),
        catalog=None, audit={}, supports_complex_queries=True)
    result = Nl2SqlEngine(path, model_plan_provider=provider).answer(
        '统计staff记录总数、picture非空记录数、picture为空的记录数及其比例。')
    assert result.status == 'ok'
    assert result.rows == ({'total_count': 5, 'nonnull_count': 3, 'null_count': 2, 'null_ratio': 0.4},)


def test_null_count_guard_does_not_trigger_on_unqualified_value_count():
    tables = _tables('CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture BLOB);')
    assert explicit_null_row_count_fields('查找picture为空的staff记录。', tables) == []
