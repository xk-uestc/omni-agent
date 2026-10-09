"""Semantic choices inherit their original source-bound complete SQL request."""
from dataclasses import replace
import json
import sqlite3

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.schema import AliasRule, SchemaLinker
from backend.session import ConversationStore


@pytest.fixture
def engine(tmp_path):
    database = tmp_path / 'mixed.sqlite'
    with sqlite3.connect(database) as connection:
        connection.executescript('''
            CREATE TABLE sales_orders(order_id INTEGER PRIMARY KEY,order_date TEXT,region TEXT,
                                      sales_amount REAL,cost_amount REAL);
            INSERT INTO sales_orders VALUES(1,'2025-03-01','华东',100,60);
            INSERT INTO sales_orders VALUES(2,'2025-11-01','华东',200,120);
            INSERT INTO sales_orders VALUES(3,'2024-03-01','华东',800,500);
            INSERT INTO sales_orders VALUES(4,'2025-03-01','华南',400,240);
            CREATE TABLE finance_loans(loan_id INTEGER PRIMARY KEY,loan_date TEXT,region TEXT,
                                       loan_amount REAL,expense_amount REAL);
            INSERT INTO finance_loans VALUES(1,'2025-03-01','华东',1000,30);
            INSERT INTO finance_loans VALUES(2,'2024-03-01','华东',9000,270);
            INSERT INTO finance_loans VALUES(3,'2025-03-01','华南',3000,90);
        ''')
    aliases = tmp_path / 'aliases.json'
    rules = [
        {'table': 'sales_orders', 'column': 'order_date', 'role': 'dimension', 'aliases': ['日期', '销售日期']},
        {'table': 'sales_orders', 'column': 'region', 'role': 'dimension', 'aliases': ['地区']},
        {'table': 'sales_orders', 'column': 'sales_amount', 'role': 'metric', 'aliases': ['销售额'], 'metric_function': 'SUM'},
        {'table': 'sales_orders', 'column': 'cost_amount', 'role': 'metric', 'aliases': ['成本'], 'metric_function': 'SUM'},
        {'table': 'finance_loans', 'column': 'loan_date', 'role': 'dimension', 'aliases': ['日期', '借款日期']},
        {'table': 'finance_loans', 'column': 'region', 'role': 'dimension', 'aliases': ['地区']},
        {'table': 'finance_loans', 'column': 'loan_amount', 'role': 'metric', 'aliases': ['借款本金'], 'metric_function': 'SUM'},
        {'table': 'finance_loans', 'column': 'expense_amount', 'role': 'metric', 'aliases': ['费用'], 'metric_function': 'SUM'},
    ]
    aliases.write_text(json.dumps(rules, ensure_ascii=False), encoding='utf-8')
    return Nl2SqlEngine(database, aliases_path=aliases, metric_catalog_path=tmp_path / 'no-catalog.json')


@pytest.fixture(params=['memory', 'persistent'])
def owner(request, tmp_path):
    ticks = [100.0]
    store = ConversationStore(storage_path=tmp_path / 'sessions.sqlite' if request.param == 'persistent' else None,
                              clock=lambda: ticks[0], ttl_seconds=60)
    store.test_ticks = ticks
    return store


def save(owner, engine, question='2025年华东本金是多少', session='choice'):
    with owner.turn(session), engine.consistent_reads():
        semantic = engine.normalize_question(question)
        assert semantic.ambiguities
        identifier = owner.semantic_pending.save(session, question, semantic, engine)
    return identifier


@pytest.mark.parametrize('reply,field,gold', [
    ('成本', 'sales_orders.cost_amount', 180),
    ('我指的是成本', 'sales_orders.cost_amount', 180),
    ('我说的是成本', 'sales_orders.cost_amount', 180),
    ('就是成本', 'sales_orders.cost_amount', 180),
    ('选成本', 'sales_orders.cost_amount', 180),
    ('sales_orders.cost_amount', 'sales_orders.cost_amount', 180),
    ('cost_amount', 'sales_orders.cost_amount', 180),
    ('sales_orders.cost_amount（成本）', 'sales_orders.cost_amount', 180),
    ('借款本金', 'finance_loans.loan_amount', 1000),
    ('finance_loans.loan_amount', 'finance_loans.loan_amount', 1000),
])
def test_choice_retains_year_and_region_real_execution(owner, engine, reply, field, gold):
    identifier = save(owner, engine)
    with owner.turn('choice'), engine.consistent_reads():
        decision = owner.semantic_pending.resolve('choice', reply, engine)
        assert decision.status == 'resolved', decision
        assert decision.pending_id == identifier
        assert decision.question.startswith('2025年华东' + ('成本' if field.startswith('sales_orders.') else '借款本金'))
        result = engine.answer(decision.question)
    assert result.status == 'ok', result.to_dict()
    assert tuple(tuple(row.values()) for row in result.rows) == ((gold,),)
    assert '2025-01-01' in result.parameters and '2026-01-01' in result.parameters
    assert '华东' in result.parameters
    assert owner.context('choice') == ()
    assert owner.sources.list_sources('choice') == ()


@pytest.mark.parametrize('reply', ['1', '第一个', '第一项', '2', '第二个'])
def test_numbered_choices_are_current_menu_bound(owner, engine, reply):
    save(owner, engine)
    with owner.turn('choice'), engine.consistent_reads():
        decision = owner.semantic_pending.resolve('choice', reply, engine)
    assert decision.status == 'resolved', decision
    assert tuple(tuple(row.values()) for row in engine.answer(decision.question).rows) in (((180,),), ((1000,),))


@pytest.mark.parametrize('reply', ['本金', '第八个', '成本和借款本金', '成本，其他条件不变',
                                  '成本，2024年', '成本，华南', '费用', 'DROP TABLE sales_orders',
                                  'sales_orders.cost_amount; DELETE FROM sales_orders', ''])
def test_unknown_mixed_and_injected_choice_never_becomes_new_query(owner, engine, reply):
    save(owner, engine)
    with owner.turn('choice'), engine.consistent_reads():
        decision = owner.semantic_pending.resolve('choice', reply, engine)
    assert decision.status == 'clarification', decision
    assert decision.reason == 'semantic_pending_choice_not_unique'
    assert decision.question == '2025年华东本金是多少'
    assert owner.context('choice') == ()


def test_expiry_blocks_short_reply_instead_of_full_database_query(owner, engine):
    save(owner, engine)
    owner.test_ticks[0] += 61
    decision = owner.semantic_pending.resolve('choice', '成本', engine)
    assert decision.status == 'clarification'
    assert decision.reason == 'semantic_pending_expired'
    assert owner.semantic_pending.resolve('choice', '成本', engine).reason == 'semantic_pending_expired'


@pytest.mark.parametrize('change', ['data', 'schema', 'alias'])
def test_source_or_schema_change_blocks_pending_scope(owner, engine, change):
    save(owner, engine)
    if change == 'alias':
        engine.planner.linker = SchemaLinker((AliasRule('sales_orders', 'cost_amount', ('新成本',), 'metric', 'SUM'),))
    else:
        with sqlite3.connect(engine.database_path) as connection:
            connection.execute('UPDATE sales_orders SET cost_amount=999 WHERE order_id=1' if change == 'data'
                               else 'ALTER TABLE sales_orders ADD COLUMN new_field REAL')
    decision = owner.semantic_pending.resolve('choice', '成本', engine)
    assert decision.status == 'clarification'
    assert decision.reason == 'semantic_pending_source_changed'


@pytest.mark.parametrize('field', ['original_question', 'question', 'snapshot', 'normalization'])
def test_pending_payload_tampering_blocks_choice(owner, engine, field):
    save(owner, engine)
    with owner.semantic_pending.connection() as connection:
        payload = json.loads(connection.execute('SELECT payload FROM pending_semantic_choices').fetchone()[0])
        payload[field] = '成本'
        connection.execute('UPDATE pending_semantic_choices SET payload=?', (json.dumps(payload, ensure_ascii=False),))
    decision = owner.semantic_pending.resolve('choice', '成本', engine)
    assert decision.status == 'clarification'
    assert decision.reason == 'semantic_pending_integrity_failed'


def test_cross_session_and_reset_do_not_inherit_pending_conditions(owner, engine):
    save(owner, engine)
    assert owner.semantic_pending.resolve('other', '成本', engine) is None
    owner.clear('choice')
    assert owner.semantic_pending.resolve('choice', '成本', engine) is None


def test_history_authority_is_preserved_and_intervening_turn_invalidates_choice(owner, engine):
    owner.remember('choice', question='2024年华南销售额', effective_question='2024年华南销售额',
                   state={'route': 'sql', 'executed_sql_context': {'sql': 'verified-old-sql'}})
    before = owner.context('choice')
    save(owner, engine)
    assert owner.context('choice') == before
    owner.remember('choice', question='2025年华南销售额', effective_question='2025年华南销售额',
                   state={'route': 'sql', 'executed_sql_context': {'sql': 'verified-new-sql'}})
    decision = owner.semantic_pending.resolve('choice', '成本', engine)
    assert decision.reason == 'semantic_pending_history_changed'
    assert owner.context('choice')[-1].question == '2025年华南销售额'


@pytest.mark.parametrize('question', ['2024年华南销售额', '新问题：2024年销售额', '成本是什么意思', '取消',
                                     '请查询2024年华南销售额', '帮我查2024年华南销售额', '查看2024年华南销售额',
                                     '换个主题，2024年华南销售额', '换一个话题，2024年华南销售额'])
def test_full_new_question_can_clear_pending_record(owner, engine, question):
    save(owner, engine)
    decision = owner.semantic_pending.resolve('choice', question, engine)
    assert decision.status == 'unrelated'
    assert owner.semantic_pending.resolve('choice', '成本', engine) is None


def test_persistent_pending_survives_restart_with_same_snapshot(tmp_path, engine):
    path = tmp_path / 'sessions.sqlite'
    first = ConversationStore(storage_path=path)
    save(first, engine)
    second = ConversationStore(storage_path=path)
    with second.turn('choice'), engine.consistent_reads():
        decision = second.semantic_pending.resolve('choice', '成本', engine)
        result = engine.answer(decision.question)
    assert decision.status == 'resolved', decision
    assert tuple(tuple(row.values()) for row in result.rows) == ((180,),)


def test_save_requires_real_current_normalization(owner, engine):
    normalization = engine.normalize_question('2025年华东本金是多少')
    bad = replace(normalization, original_question='本金', normalized_question='成本')
    with pytest.raises(ValueError):
        owner.semantic_pending.save('choice', '2025年华东本金是多少', bad, engine)


def test_multiple_ambiguous_spans_are_resolved_one_at_a_time(owner, engine):
    question = '2025年华东本金和花销是多少'
    semantic = engine.normalize_question(question)
    assert len(semantic.ambiguities) == 2
    owner.semantic_pending.save('choice', question, semantic, engine)
    with owner.turn('choice'), engine.consistent_reads():
        first = owner.semantic_pending.resolve('choice', '成本', engine)
    assert first.status == 'clarification', first
    assert first.reason == 'semantic_pending_more_choices'
    assert first.question == '2025年华东成本和花销是多少'
    assert first.normalization.ambiguities[0].source_text == '花销'
    assert owner.context('choice') == ()
    with owner.turn('choice'), engine.consistent_reads():
        second = owner.semantic_pending.resolve('choice', '费用', engine)
    assert second.question.startswith('2025年华东')
    assert '成本' in second.question
    assert second.status in {'resolved', 'clarification'}
    assert owner.context('choice') == ()
