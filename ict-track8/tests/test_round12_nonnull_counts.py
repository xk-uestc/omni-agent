"""Preserve requested field lineage for explicit non-NULL record counts."""
from datetime import date
from types import SimpleNamespace
import sqlite3

from backend.nl2sql.complex_query import (CHECKS, explicit_nonnull_count_fields, needs_complex_query,
                                          propose_complex)
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.schema import SchemaIntrospector


class RepairClient:
    def __init__(self):
        self.calls = []

    def generate(self, instructions, context, schema, **kwargs):
        name = kwargs['name']
        self.calls.append((name, context))
        if name == 'complex_sql_proposal':
            return {'sql': 'SELECT COUNT(*) AS n FROM staff WHERE picture IS NOT NULL',
                    'clarification': None}
        if name == 'complex_sql_structural_repair':
            assert context['structural_failure'] == (
                'complex_query_explicit_nonnull_count_requires_field_projection')
            return {'sql': 'SELECT COUNT(picture) AS n FROM staff', 'clarification': None}
        return {'approved': True, 'checks': {key: True for key in CHECKS}, 'clarification': None}


def test_nonnull_count_structural_repair_projects_requested_field():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture BLOB)')
        tables = SchemaIntrospector().introspect(db, include_row_count=False)
    question = '统计全部staff的picture非空记录数。'
    assert needs_complex_query(question)
    assert explicit_nonnull_count_fields(question, tables) == [('staff', 'picture')]
    client = RepairClient()
    provider = SimpleNamespace(client=client, reference_date=date(2026, 1, 1),
        catalog=None, audit={}, supports_complex_queries=True)
    plan, compiled = propose_complex(provider, question, tables)
    assert compiled is not None
    assert 'COUNT("staff"."picture")' in compiled[0]
    assert plan.semantic_audit['status'] == 'model_reviewed'
    assert [name for name, _ in client.calls] == [
        'complex_sql_proposal', 'complex_sql_structural_repair', 'complex_sql_independent_review']


def test_ambiguous_bare_field_does_not_create_projection_repair():
    with sqlite3.connect(':memory:') as db:
        db.executescript('CREATE TABLE staff(picture TEXT); CREATE TABLE archive(picture TEXT);')
        tables = SchemaIntrospector().introspect(db, include_row_count=False)
    assert explicit_nonnull_count_fields('picture非空记录数', tables) == []


def test_nonnull_filter_does_not_become_nonnull_count_request():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE rental(return_date TEXT)')
        tables = SchemaIntrospector().introspect(db, include_row_count=False)
    assert explicit_nonnull_count_fields(
        '统计return_date非空的租赁记录数。', tables) == []


def test_engine_executes_repaired_field_count_without_null_filter(tmp_path):
    path = tmp_path / 'source.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, picture TEXT);"
                         "INSERT INTO staff VALUES(1,'x'),(2,NULL),(3,'y');")
    client = RepairClient()
    provider = SimpleNamespace(client=client, reference_date=date(2026, 1, 1),
        catalog=None, audit={}, supports_complex_queries=True)
    result = Nl2SqlEngine(path, model_plan_provider=provider).answer(
        '统计全部staff的picture非空记录数。')
    assert result.status == 'ok' and result.rows == ({'n': 2},)
    assert 'COUNT("staff"."picture")' in result.sql
    assert ' IS NOT NULL' not in result.sql
