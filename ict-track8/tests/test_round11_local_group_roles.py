"""Schema roles follow local grouping, explicit ownership and date exclusions."""
import sqlite3

import pytest

from backend.nl2sql.question_roles import (excluded_filter_scope, field_owners,
                                         group_fields, normalized)
from backend.nl2sql.schema import SchemaIntrospector, SchemaLinker


@pytest.fixture
def tables():
    with sqlite3.connect(':memory:') as connection:
        connection.executescript('''
            CREATE TABLE payment(payment_id INTEGER PRIMARY KEY, staff_id INTEGER,
                                 amount REAL, payment_date DATETIME);
            CREATE TABLE rental(rental_id INTEGER PRIMARY KEY, staff_id INTEGER,
                                amount REAL, rental_date DATETIME, return_date DATETIME);
            CREATE TABLE staff(staff_id INTEGER PRIMARY KEY, name TEXT);
        ''')
        return SchemaIntrospector().introspect(connection)


@pytest.mark.parametrize('cue', ['各', '每个', '每一'])
def test_bare_physical_group_uses_only_uniquely_named_source(tables, cue):
    question = normalized(f'以 payment.payment_date 为准，统计2005年7月{cue} staff_id 的 amount 合计，不按租赁或归还日期过滤。')
    masked, spans = excluded_filter_scope(question, tables)
    assert spans and len(masked) == len(question)
    assert group_fields(masked, tables) == {('payment', 'staff_id')}
    assert field_owners(masked, tables)['staff_id'] == {'payment'}
    links = SchemaLinker().link(question, tables)
    staff_links = [(link.table, link.role) for link in links if link.column == 'staff_id']
    assert staff_links == [('payment', 'dimension')]
    assert {(link.table, link.column) for link in links if link.role == 'metric'} == {('payment', 'amount')}
    assert not any(link.column in {'rental_date', 'return_date'} for link in links)
    assert '不按租赁或归还日期过滤' in question


def test_unqualified_group_does_not_guess_between_two_explicit_sources(tables):
    text = normalized('按payment.payment_date和rental.rental_date筛选，统计各staff_id的amount合计')
    assert group_fields(text, tables) == {('payment', 'staff_id'), ('rental', 'staff_id'), ('staff', 'staff_id')}
    assert field_owners(text, tables)['staff_id'] == {'payment', 'rental', 'staff'}


def test_explicit_local_group_source_wins_over_other_named_sources(tables):
    text = normalized('按 rental.staff_id 分组，统计 payment.amount 合计')
    assert group_fields(text, tables) == {('rental', 'staff_id')}
    links = SchemaLinker().link(text, tables)
    assert [(l.table, l.role) for l in links if l.column == 'staff_id'] == [('rental', 'dimension')]


@pytest.mark.parametrize('text', [
    '各staff_id最新记录的amount合计', '每个staff_id最早的amount合计',
    '各staff_id的name', '每个staff_id的最近记录数量',
])
def test_each_entity_is_not_always_grouping(tables, text):
    assert group_fields(normalized(text), tables) == set()


@pytest.mark.parametrize('text', [
    '不按amount大于10过滤', '不按payment.payment_date为空过滤',
    '不按payment.missing_date过滤', '不按staff.payment_date过滤',
    '不按return_date为2020年过滤', '不按金额和归还日期过滤',
])
def test_value_negation_unknown_fields_and_mixed_measures_stay_visible(tables, text):
    text = normalized(text)
    masked, spans = excluded_filter_scope(text, tables)
    assert masked == text and not spans


@pytest.mark.parametrize('text', [
    '不按归还日期过滤', '不要按rental.return_date进行筛选',
    '不按照rental_date和return_date过滤',
])
def test_only_local_date_directive_is_masked(tables, text):
    question = normalized('payment.amount合计；' + text + '，return_date非空')
    masked, spans = excluded_filter_scope(question, tables)
    assert len(spans) == 1 and 'return_date非空' in masked
    links = SchemaLinker().link(question, tables)
    assert any(l.column == 'return_date' and l.role == 'filter' for l in links)


def test_positive_date_usage_survives_a_separate_exclusion(tables):
    question = '按rental.return_date分组，payment.amount合计，不按rental.return_date过滤'
    links = SchemaLinker().link(question, tables)
    assert any(l.table == 'rental' and l.column == 'return_date' and l.role == 'dimension' for l in links)
