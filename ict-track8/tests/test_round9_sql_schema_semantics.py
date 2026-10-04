"""Snapshot schema/date evidence and retained independent SQL review gates."""
from copy import deepcopy
from datetime import date
import sqlite3

import pytest

from backend.nl2sql.complex_query import CHECKS, propose_complex
from backend.nl2sql.date_profile import storage_profiles
from backend.nl2sql.date_semantics import is_date_column
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.schema import SchemaIntrospector


def schema(connection):
    return SchemaIntrospector().introspect(connection, include_row_count=False)


@pytest.mark.parametrize('definition', [
    'id INTEGER PRIMARY KEY',
    'id INTEGER PRIMARY KEY AUTOINCREMENT',
    'id INTEGER PRIMARY KEY ASC',
    'id INTEGER, PRIMARY KEY(id)',
    'id INTEGER, PRIMARY KEY(id DESC)',
])
def test_exact_integer_rowid_alias_is_effectively_non_null(definition):
    with sqlite3.connect(':memory:') as db:
        db.execute(f'CREATE TABLE records({definition})')
        # PRAGMA's declared notnull alone does not describe a rowid alias.
        assert db.execute('PRAGMA table_info(records)').fetchone()[3] == 0
        db.execute('INSERT INTO records(id) VALUES(NULL)')
        db.execute('INSERT INTO records(id) VALUES(NULL)')
        assert [row[0] for row in db.execute('SELECT id FROM records ORDER BY id')] == [1, 2]
        assert schema(db)[0].columns[0].nullable is False


@pytest.mark.parametrize('definition', [
    'id INT PRIMARY KEY',
    'id TEXT PRIMARY KEY',
    'id INTEGER PRIMARY KEY DESC',
    'id INTEGER, other INTEGER, PRIMARY KEY(id,other)',
])
def test_non_alias_and_composite_primary_keys_retain_actual_nullability(definition):
    with sqlite3.connect(':memory:') as db:
        db.execute(f'CREATE TABLE records({definition})')
        db.execute('INSERT INTO records(id) VALUES(NULL)')
        db.execute('INSERT INTO records(id) VALUES(NULL)')
        assert db.execute('SELECT COUNT(*) FROM records WHERE id IS NULL').fetchone()[0] == 2
        assert schema(db)[0].columns[0].nullable is True


@pytest.mark.parametrize('ddl', [
    'CREATE TABLE records(id INT NOT NULL PRIMARY KEY)',
    'CREATE TABLE records(id TEXT PRIMARY KEY) WITHOUT ROWID',
    'CREATE TABLE records(id TEXT PRIMARY KEY) STRICT',
])
def test_actual_non_null_constraints_remain_enforced(ddl):
    with sqlite3.connect(':memory:') as db:
        db.execute(ddl)
        assert schema(db)[0].columns[0].nullable is False
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('INSERT INTO records(id) VALUES(NULL)')


@pytest.mark.parametrize('column', ['created_at', 'updated_at', 'recorded_at', 'published_on'])
def test_generic_time_names_are_candidates_with_actual_storage_evidence(column):
    assert is_date_column(column, 'TEXT')
    with sqlite3.connect(':memory:') as db:
        db.execute(f'CREATE TABLE records({column} TEXT)')
        db.execute(f'INSERT INTO records VALUES(?)', ('2026-01-01',))
        profile = storage_profiles(db, schema(db), column)[0]
        assert profile['field'] == f'records.{column}'
        assert profile['format'] == 'iso_text'
        assert profile['time_comparison']['operator'] == 'JULIANDAY'


def test_iso_parseability_does_not_authorize_mixed_separator_text_sorting():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE records(id INTEGER PRIMARY KEY, created_at TEXT)')
        db.executemany('INSERT INTO records VALUES(?,?)', [
            (1, '2026-01-01T01:00:00'), (2, '2026-01-01 23:00:00'),
        ])
        profile = storage_profiles(db, schema(db), 'created_at')[0]
        assert profile['format'] == 'iso_text'
        assert profile['time_comparison'] == {
            'operator': 'JULIANDAY',
            'verification': 'whole_column_sqlite_parse_non_null_values',
            'text_order_verified': False,
            'precision_contract': 'native_sqlite_time_semantics',
        }
        assert db.execute('SELECT id FROM records ORDER BY created_at DESC').fetchone()[0] == 1
        assert db.execute('SELECT id FROM records ORDER BY JULIANDAY(created_at) DESC').fetchone()[0] == 2
        assert '2026-01-01' not in str(profile)


@pytest.mark.parametrize('values', [
    ['2026-01-01'] * 21 + ['not a date'],
    [None, None],
    [20260101000000],
    ['2026-01-01T01:00:00+02:00'],
])
def test_time_candidate_names_do_not_turn_unverified_values_into_dates(values):
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE records(created_at)')
        db.executemany('INSERT INTO records VALUES(?)', [(value,) for value in values])
        profile = storage_profiles(db, schema(db), 'created_at')[0]
        assert profile['format'] == 'unknown'
        assert 'time_comparison' not in profile


def test_time_probe_budget_exhaustion_does_not_authorize_a_comparator():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE records(created_at TEXT)')
        db.execute("INSERT INTO records VALUES('2026-01-01')")
        profile = storage_profiles(db, schema(db), 'created_at', max_seconds=0)[0]
        assert profile['format'] == 'unknown'
        assert 'time_comparison' not in profile
        assert db.execute('SELECT COUNT(*) FROM records').fetchone()[0] == 1


class Provider:
    supports_complex_queries = True
    reference_date = date(2026, 1, 1)
    catalog = None
    audit = {}

    def __init__(self, sql, *, approve=True):
        self.sql = sql
        self.approve = approve
        self.client = self
        self.calls = []

    def generate(self, instructions, context, response_schema, **options):
        self.calls.append((options['name'], deepcopy(context)))
        if options['name'] == 'complex_sql_proposal':
            return {'sql': self.sql, 'clarification': None}
        return {'approved': self.approve,
                'checks': {key: self.approve for key in CHECKS},
                'clarification': None if self.approve else 'Date ordering was not verified.'}


def test_latest_actual_engine_receives_date_comparator_and_non_null_key(tmp_path):
    database = tmp_path / 'latest.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY, owner INTEGER, created_at TEXT)')
        db.executemany('INSERT INTO events VALUES(?,?,?)', [
            (1, 1, '2026-01-01T01:00:00'),
            (2, 1, '2026-01-01 23:00:00'),
            (3, 1, '2026-01-01T23:00:00'),
        ])
    provider = Provider('WITH ranked AS (SELECT id,owner,ROW_NUMBER() OVER('
                        'PARTITION BY owner ORDER BY JULIANDAY(created_at) DESC,id DESC) AS rn '
                        'FROM events) SELECT id,owner FROM ranked WHERE rn=1 ORDER BY owner')
    result = Nl2SqlEngine(database, model_plan_provider=provider,
                         result_artifact_dir=tmp_path/'results').answer(
        '列出每个events.owner的created_at最新一条，同一时刻id最大，输出id和owner')
    assert result.status == 'ok' and result.rows == ({'id': 3, 'owner': 1},)
    assert [operation for operation, _ in provider.calls] == [
        'complex_sql_proposal', 'complex_sql_independent_review']
    for _, context in provider.calls:
        assert context['date_storage_profiles'][0]['time_comparison']['operator'] == 'JULIANDAY'
        assert context['schema'][0]['columns'][0]['nullable'] is False


def test_staged_aggregate_population_retains_null_group_and_ignores_unrequested_entities():
    with sqlite3.connect(':memory:') as db:
        db.executescript('CREATE TABLE facts(entity INTEGER, value REAL);'
                         'CREATE TABLE entities(id INTEGER PRIMARY KEY);'
                         'INSERT INTO facts VALUES(1,10),(1,10),(2,5),(NULL,100);'
                         'INSERT INTO entities VALUES(1),(2),(3);')
        sql = ('WITH grouped AS (SELECT entity,SUM(value) AS total FROM facts GROUP BY entity) '
               'SELECT entity,total FROM grouped WHERE total>(SELECT AVG(total) FROM grouped) '
               'ORDER BY entity')
        provider = Provider(sql)
        plan, compiled = propose_complex(provider,
            '先按facts.entity分组求value合计，再返回高于所有这些组总额平均值的entity和总额', schema(db))
        assert plan.clarification is None
        assert db.execute(*compiled).fetchall() == [(None, 100.0)]
        for _, context in provider.calls:
            assert context['execution_contract']['staged_group_population'] == (
                'preceding_explicit_group_relation_unless_user_changes_population')
        assert 'entities' not in plan.join_tables


def test_added_snapshot_evidence_does_not_bypass_an_independent_rejection():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY, created_at TEXT)')
        db.execute("INSERT INTO events VALUES(1,'2026-01-01')")
        provider = Provider('SELECT id FROM events ORDER BY JULIANDAY(created_at) DESC', approve=False)
        plan, compiled = propose_complex(provider, '列出created_at最新的events.id', schema(db),
                                         date_profiles=storage_profiles(db, schema(db), 'created_at'))
        assert compiled is None
        assert plan.clarification_code == 'complex_query_semantic_review_rejected'
        assert plan.semantic_audit['status'] == 'rejected'
