"""Model proposals see server source slots but must still satisfy them."""
from copy import deepcopy
import sqlite3

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.nl2sql.responses_provider import INSTRUCTIONS


SOURCE = '查2025年销售额排名第一的地区'
TASK = '2025年各地区销售额排名'


class CapturingProvider:
    def __init__(self, missing_rank=False):
        self.contexts = []
        self.missing_rank = missing_rank

    def propose(self, question, tables, verified_intent):
        self.contexts.append(deepcopy(verified_intent))
        return {'version': 2, 'metrics': [{'id': 'revenue', 'table': 'sales_orders', 'column': 'sales_amount',
            'function': 'SUM', 'label': '销售额', 'unit': 'currency', 'currency': 'CNY', 'missing': 'null'}],
            'dimensions': [{'table': 'sales_orders', 'column': 'region', 'label': '地区'}],
            'filters': [{'table': 'sales_orders', 'column': 'order_date', 'operator': 'RANGE',
                         'value': ['2025-01-01', '2026-01-01']}],
            'analysis_mode': 'rank', 'top_n': None if self.missing_rank else verified_intent['top_n'],
            'order_desc': verified_intent['order_desc'], 'limit': 1, 'confidence': .99,
            'rewritten_question': question}


def engine(tmp_path, provider):
    return Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'), model_plan_provider=provider,
                       model_fallback=False)


def test_original_rank_slots_reach_propose_before_sql_and_actual_model_satisfies_scope(tmp_path):
    provider = CapturingProvider()
    instance = engine(tmp_path, provider)
    required = instance.extract_required_intent(SOURCE)
    result = instance.answer(TASK, required_intent=required)
    context = provider.contexts[0]
    assert context['top_n'] == 1 and context['analysis_mode'] == 'rank' and context['order_desc'] is True
    assert context['source'] == 'independent_original_source_scope_in_execution_snapshot'
    assert 'sql' not in context
    assert result.status == 'ok'
    assert result.plan['planner_source'] == 'model_validated'
    assert result.plan['top_n'] == 1
    assert result.provenance['source_constraint_validation']['status'] == 'verified'


def test_visible_rank_contract_does_not_patch_noncompliant_model_plan(tmp_path):
    provider = CapturingProvider(missing_rank=True)
    instance = engine(tmp_path, provider)
    result = instance.answer(TASK, required_intent=instance.extract_required_intent(SOURCE))
    assert provider.contexts[0]['top_n'] == 1
    assert result.status == 'incomplete' and result.sql is None
    assert result.plan['top_n'] is None
    assert result.provenance['source_constraint_validation']['error_codes'] == ['source_aggregation_mismatch']


def test_no_required_keeps_question_local_model_context(tmp_path):
    provider = CapturingProvider()
    instance = engine(tmp_path, provider)
    result = instance.answer(TASK)
    assert provider.contexts[0]['top_n'] is None
    assert provider.contexts[0]['source'] == 'independent_explicit_slot_extraction'
    assert 'scope_question_sha256' not in provider.contexts[0]
    assert result.status == 'ok'


def test_forged_old_plan_fields_do_not_authorize_new_source_scope(tmp_path):
    provider = CapturingProvider()
    instance = engine(tmp_path, provider)
    required = instance.extract_required_intent(SOURCE)
    required.top_n = 99
    required.order_desc = False
    result = instance.answer(TASK, required_intent=required)
    assert provider.contexts[0]['top_n'] == 1 and provider.contexts[0]['order_desc'] is True
    assert result.status == 'ok'


def test_schema_change_revalidates_required_before_provider_request(tmp_path):
    provider = CapturingProvider()
    instance = engine(tmp_path, provider)
    required = instance.extract_required_intent(SOURCE)
    with sqlite3.connect(instance.database_path) as connection:
        connection.execute('ALTER TABLE sales_orders RENAME COLUMN sales_amount TO unrecognized_amount')
    result = instance.answer(TASK, required_intent=required)
    assert result.status == 'incomplete'
    assert provider.contexts == []
    assert result.sql is None


def test_provider_instruction_preserves_generic_rank_and_comparison_contracts():
    assert all(field in INSTRUCTIONS for field in ('analysis_mode', 'top_n', 'order_desc', 'having',
                                                  'comparison_mode', 'comparison_period', 'DENSE_RANK'))
