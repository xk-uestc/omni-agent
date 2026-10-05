"""Row-count intent must survive NULL data and representation repair."""
from datetime import date
from types import SimpleNamespace
import sqlite3
import pytest
from backend.nl2sql.complex_query import CHECKS, _verify_count_projection, propose_complex
from backend.nl2sql.schema import SchemaIntrospector
from backend.nl2sql.security import SqlSafetyError


def database():
    db = sqlite3.connect(':memory:')
    db.executescript("CREATE TABLE events(id INTEGER PRIMARY KEY,event_date TEXT);"
                    "INSERT INTO events VALUES(1,'2025-01-02'),(2,NULL),(3,'2025-01-03');")
    return db, SchemaIntrospector().introspect(db, include_row_count=False)


@pytest.mark.parametrize('question', ['统计events记录总数', '统计events记录数',
    '统计events行数', 'Return the number of records in events'])
def test_nullable_field_is_not_a_record_count(question):
    db, tables = database()
    try:
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 3
        assert db.execute('SELECT COUNT(event_date) FROM events').fetchone()[0] == 2
        with pytest.raises(SqlSafetyError, match='record_count_requires_row_projection'):
            _verify_count_projection(question, 'SELECT COUNT(event_date) AS n FROM events', tables)
        _verify_count_projection(question, 'SELECT COUNT(*) AS n FROM events', tables)
        _verify_count_projection(question, 'SELECT COUNT(id) AS n FROM events', tables)
    finally:
        db.close()


def test_date_filter_does_not_change_requested_projection():
    db, tables = database()
    try:
        with pytest.raises(SqlSafetyError, match='record_count_requires_row_projection'):
            _verify_count_projection('统计2025年events记录数，按event_date限定年份',
                "SELECT COUNT(event_date) AS n FROM events WHERE event_date >= '2025-01-01'", tables)
        _verify_count_projection('统计event_date非空记录数',
            'SELECT COUNT(event_date) AS n FROM events', tables)
    finally:
        db.close()


@pytest.mark.parametrize('question,sql', [
    ('统计不同event_date的记录数', 'SELECT COUNT(DISTINCT event_date) AS n FROM events'),
    ('按event_date分组统计记录数', 'SELECT event_date,COUNT(event_date) AS n FROM events GROUP BY event_date'),
    ('执行COUNT(event_date)统计记录数', 'SELECT COUNT(event_date) AS n FROM events'),
])
def test_distinct_group_and_explicit_expression_are_not_reinterpreted(question, sql):
    db, tables = database()
    try:
        _verify_count_projection(question, sql, tables)
    finally:
        db.close()


def test_single_existing_repair_preserves_filters_and_rows():
    db, tables = database()
    calls = []
    class Client:
        def generate(self, instructions, context, schema, **kwargs):
            name = kwargs['name']; calls.append(name)
            if name == 'complex_sql_proposal':
                return {'sql': 'SELECT COUNT(event_date) AS n FROM events WHERE id >= 2', 'clarification': None}
            if name == 'complex_sql_structural_repair':
                assert context['structural_failure'] == 'complex_query_record_count_requires_row_projection'
                return {'sql': 'SELECT COUNT(*) AS n FROM events WHERE id >= 2', 'clarification': None}
            return {'approved': True, 'checks': {k: True for k in CHECKS}, 'clarification': None}
    try:
        provider = SimpleNamespace(client=Client(), reference_date=date(2026, 1, 1), catalog=None, audit={})
        plan, compiled = propose_complex(provider, '统计events中id>=2的记录数', tables)
        assert db.execute(*compiled).fetchone()[0] == 2
        assert calls == ['complex_sql_proposal', 'complex_sql_structural_repair', 'complex_sql_independent_review']
        assert plan.metrics[0].column == 'id'
    finally:
        db.close()
