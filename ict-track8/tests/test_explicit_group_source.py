"""Equal FK values cannot replace an explicitly requested grouping source."""
from datetime import date
from types import SimpleNamespace
import sqlite3
import pytest

from backend.nl2sql.complex_query import CHECKS, propose_complex, _verify_explicit_group_sources, _explicit_group_sources
from backend.nl2sql.schema import SchemaIntrospector
from backend.nl2sql.security import SqlSafetyError


def tables():
    with sqlite3.connect(':memory:') as db:
        db.executescript('CREATE TABLE owners(owner_id INTEGER PRIMARY KEY);'
                        'CREATE TABLE categories(category_id INTEGER PRIMARY KEY);'
                        'CREATE TABLE records(record_id INTEGER PRIMARY KEY, '
                        'owner_id INTEGER REFERENCES owners(owner_id), '
                        'category_id INTEGER REFERENCES categories(category_id));')
        return SchemaIntrospector().introspect(db, include_row_count=False)


QUESTION = '按 owners.owner_id 和 categories.category_id 统计不同record_id数量，返回两个ID和去重数。'
WRONG = ('SELECT r.owner_id AS owner_id,r.category_id AS category_id,COUNT(DISTINCT r.record_id) AS n '
         'FROM records AS r GROUP BY r.owner_id,r.category_id')
RIGHT = ('SELECT o.owner_id AS owner_id,c.category_id AS category_id,COUNT(DISTINCT r.record_id) AS n '
         'FROM records AS r JOIN owners AS o ON o.owner_id=r.owner_id '
         'JOIN categories AS c ON c.category_id=r.category_id GROUP BY o.owner_id,c.category_id')


def test_equal_fk_group_is_not_the_explicit_parent_group():
    assert _explicit_group_sources(QUESTION, tables()) == [
        {('owners', 'owner_id'), ('categories', 'category_id')}]
    with pytest.raises(SqlSafetyError, match='complex_query_explicit_group_source_mismatch'):
        _verify_explicit_group_sources(QUESTION, WRONG, tables())
    _verify_explicit_group_sources(QUESTION, RIGHT, tables())


def test_group_key_is_resolved_through_direct_cte_alias():
    sql = ('WITH keys AS (SELECT o.owner_id AS oid,c.category_id AS cid,r.record_id AS rid '
           'FROM records r JOIN owners o ON o.owner_id=r.owner_id '
           'JOIN categories c ON c.category_id=r.category_id) '
           'SELECT oid,cid,COUNT(DISTINCT rid) AS n FROM keys GROUP BY oid,cid')
    _verify_explicit_group_sources(QUESTION, sql, tables())


def test_inner_group_for_outer_scalar_aggregate_keeps_original_sources():
    sql = ('WITH totals AS (' + RIGHT + ') SELECT AVG(n) AS average FROM totals')
    _verify_explicit_group_sources(QUESTION, sql, tables())


@pytest.mark.parametrize('question', [
    '不要按owners.owner_id统计，只返回records记录总数。',
    '按owners.owner_id筛选后统计records记录数。',
    '使用owners.owner_id与records.owner_id关联，按record_id分组统计。',
    '按unknown.owner_id统计记录数。',
    '按owner_id统计记录数。',
])
def test_filter_join_unknown_and_ambiguous_bare_fields_do_not_invent_group_contract(question):
    _verify_explicit_group_sources(question, WRONG, tables())


def test_existing_single_representation_repair_can_restore_requested_group_source():
    calls = []
    class Client:
        def generate(self, instructions, context, schema, **kwargs):
            name = kwargs['name']
            calls.append(name)
            assert context['explicit_physical_group_fields'] == [[
                {'table': 'categories', 'column': 'category_id'},
                {'table': 'owners', 'column': 'owner_id'}]]
            if name == 'complex_sql_proposal':
                return {'sql': WRONG, 'clarification': None}
            if name == 'complex_sql_structural_repair':
                assert context['structural_failure'] == 'complex_query_explicit_group_source_mismatch'
                return {'sql': RIGHT, 'clarification': None}
            return {'approved': True, 'checks': {key: True for key in CHECKS}, 'clarification': None}
    provider = SimpleNamespace(client=Client(), reference_date=date(2032,1,1), catalog=None, audit={})
    plan, compiled = propose_complex(provider, QUESTION, tables())
    assert compiled and plan.semantic_audit['status'] == 'model_reviewed'
    assert calls == ['complex_sql_proposal','complex_sql_structural_repair','complex_sql_independent_review']
