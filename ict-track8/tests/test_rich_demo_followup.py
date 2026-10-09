"""Rich-schema follow-ups retain exact metric, region and year scopes."""
import sqlite3

import pytest

from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_rich_demo_data
from backend.nl2sql.value_index import ValueEntry, ValueIndex
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


@pytest.fixture
def rich_agent(tmp_path):
    path = initialize_rich_demo_data(tmp_path / 'rich.sqlite')
    return OmniAgent(Nl2SqlEngine(path), KnowledgeStore(tmp_path / 'knowledge'), ConversationStore())


def expected_value(engine, year, region, expression):
    with sqlite3.connect(engine.database_path) as connection:
        return connection.execute(
            f'SELECT {expression} FROM sales_orders WHERE region = ? AND order_date >= ? AND order_date < ?',
            (region, f'{year}-01-01', f'{year + 1}-01-01')).fetchone()[0]


def test_six_round_demo_matches_independent_database_queries(rich_agent):
    turns = [
        ('2025年华东地区的销售额', 2025, '华东', '销售额', 'SUM(sales_amount)'),
        ('那华南呢', 2025, '华南', '销售额', 'SUM(sales_amount)'),
        ('看看订单数', 2025, '华南', '订单数', 'COUNT(*)'),
        ('换成华北', 2025, '华北', '订单数', 'COUNT(*)'),
        ('换成2024年', 2024, '华北', '订单数', 'COUNT(*)'),
        ('看看销量', 2024, '华北', '销量', 'SUM(quantity)'),
    ]
    for index, (question, year, region, label, expression) in enumerate(turns):
        response = rich_agent.query(question, session_id='six-rich')
        assert response['status'] == 'ok' and response['route'] == 'sql'
        assert response['result']['rows'] == [{label: expected_value(rich_agent.engine, year, region, expression)}]
        assert response['context_turns'] == index
        if index:
            assert response['context_resolution']['mode'] == 'server_verified_sql_followup'


@pytest.mark.parametrize('question', ['那华南呢', '换成华南', '看看订单数', '看看销量'])
def test_verified_followup_does_not_ask_intent_model_to_change_source(rich_agent, question):
    assert rich_agent.query('2025年华东销售额', session_id='route')['status'] == 'ok'
    class RejectIntentModel:
        def generate(self, *args, **kwargs):
            pytest.fail('A server-verified SQL replacement already determines its source')
    rich_agent.client = RejectIntentModel()
    result = rich_agent.query(question, session_id='route')
    assert result['status'] == 'ok' and result['route'] == 'sql'
    assert result['context_resolution']['mode'] == 'server_verified_sql_followup'


@pytest.mark.parametrize('region_filter', [None, '华东地区'])
def test_model_cannot_drop_or_change_canonical_region_filter(rich_agent, region_filter):
    filters = [{'table': 'sales_orders', 'column': 'order_date', 'operator': 'RANGE',
                'value': ['2025-01-01', '2026-01-01']}]
    if region_filter:
        filters.append({'table': 'sales_orders', 'column': 'region', 'operator': '=', 'value': region_filter})
    payload = {'version': 1, 'table': 'sales_orders',
        'metric': {'table': 'sales_orders', 'column': 'sales_amount', 'function': 'SUM', 'label': '销售额'},
        'dimensions': [], 'filters': filters, 'analysis_mode': 'aggregate', 'limit': 20,
        'confidence': .99, 'rewritten_question': '2025年华东地区的销售额'}
    rich_agent.engine.model_plan_provider = lambda *args: payload
    response = rich_agent.query('2025年华东地区的销售额', session_id='model-filter')
    assert response['status'] == 'ok'
    assert response['result']['plan']['planner_audit']['fallback'] is True
    assert response['result']['rows'] == [{'销售额': expected_value(rich_agent.engine, 2025, '华东', 'SUM(sales_amount)')}]


def test_metric_table_preference_keeps_same_table_value_ambiguity():
    index = ValueIndex([ValueEntry(table, column, '华东', '华东') for table, column in
                        [('orders', 'region'), ('orders', 'shipping_region'), ('regions', 'name')]])
    matches, ambiguities = index.match('华东', set(), preferred_tables={'orders'})
    assert not matches and len(ambiguities) == 1
    matches, ambiguities = index.match('华东', {('regions', 'name')}, preferred_tables={'orders'})
    assert not ambiguities and [(m.table, m.column) for m in matches] == [('regions', 'name')]


@pytest.mark.parametrize('seed', [20261007, 20261008, 20261009])
def test_seeded_short_followup_sequences_match_database(rich_agent, seed):
    from tools.verify_random_followups_http import scenarios
    for case, turns in enumerate(scenarios(seed)):
        for question, year, region, (label, column, function) in turns:
            response = rich_agent.query(question, session_id=f'random-{seed}-{case}')
            expression = 'COUNT(*)' if function == 'COUNT' else f'{function}({column})'
            assert response['status'] == 'ok', (question, response)
            assert response['route'] == 'sql'
            assert response['result']['rows'] == [{label: expected_value(rich_agent.engine, year, region, expression)}]
