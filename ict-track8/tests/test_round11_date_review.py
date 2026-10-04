"""Candidate dependencies reach review only after the structural safety gate."""
from datetime import date
from types import SimpleNamespace
import sqlite3

import pytest

from backend.nl2sql.complex_query import CHECKS, propose_complex, needs_complex_query
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.schema import SchemaIntrospector
from backend.nl2sql.security import SqlSafetyError


class Client:
    def __init__(self, sql):
        self.sql, self.contexts = sql, []

    def generate(self, instructions, context, schema, **kwargs):
        self.contexts.append((kwargs['name'], context))
        if kwargs['name'] != 'complex_sql_independent_review':
            return {'sql': self.sql, 'clarification': None}
        return {'approved': True, 'checks': {k: True for k in CHECKS}, 'clarification': None}


def provider(client):
    return SimpleNamespace(client=client, reference_date=date(2026, 1, 1), catalog=None,
                           audit={}, supports_complex_queries=True)


def test_explicit_each_field_aggregate_uses_reviewed_relational_route():
    assert needs_complex_query('以payment.payment_date筛选2005年7月，各staff_id的amount合计，不按租赁或归还日期过滤。')
    assert not needs_complex_query('payment.payment_date是多少')


def test_candidate_probe_is_review_only_and_dependencies_are_physical():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE events(id INTEGER, created_at TEXT)')
        tables = SchemaIntrospector().introspect(db, include_row_count=False)
    client = Client('SELECT MAX(JULIANDAY(created_at)) latest FROM events')
    called = []
    def load(fields, existing):
        called.append(fields)
        return [{'field': 'events.created_at', 'format': 'unknown'}]
    propose_complex(provider(client), '统计最新记录', tables, date_profile_loader=load)
    assert ('events', 'created_at') in called[0]
    assert client.contexts[0][1]['date_storage_profiles'] == []
    assert client.contexts[-1][1]['date_storage_profiles'][0]['format'] == 'unknown'


def test_invalid_candidate_never_requests_probe():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE events(id INTEGER, created_at TEXT)')
        tables = SchemaIntrospector().introspect(db, include_row_count=False)
    def fail(*args):
        pytest.fail('Unsafe candidate requested a snapshot probe')
    with pytest.raises(SqlSafetyError):
        propose_complex(provider(Client('DELETE FROM events')), '统计记录', tables,
                        date_profile_loader=fail)


def test_engine_probes_candidate_date_in_actual_snapshot(tmp_path):
    path = tmp_path / 'source.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE events(id INTEGER, created_at TEXT);"
                         "INSERT INTO events VALUES(1,'2030-01-01'),(2,'2030-01-03');")
    client = Client('SELECT MAX(JULIANDAY(created_at)) latest FROM events')
    result = Nl2SqlEngine(path, model_plan_provider=provider(client),
        result_artifact_dir=tmp_path / 'artifacts').answer('列出最新记录')
    assert result.status == 'ok'
    initial = client.contexts[0][1]['date_storage_profiles']
    final = client.contexts[-1][1]['date_storage_profiles']
    assert initial == []
    assert final[0]['field'] == 'events.created_at'
    assert final[0]['format'] == 'iso_text'
    assert final[0]['verification'] == 'whole_column_execution_snapshot'
