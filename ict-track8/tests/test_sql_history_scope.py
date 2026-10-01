from copy import deepcopy

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.session import ConversationTurn
from backend.sql_history_scope import resolve_sql_followup_scope


@pytest.fixture
def context(tmp_path):
    engine = Nl2SqlEngine(initialize_database(tmp_path / 'sales.sqlite'))
    question = '2025年华东销售额'
    result = engine.answer(question).to_dict()
    assert result['status'] == 'ok'
    state = {'route': 'sql', 'pending_question': None, 'clarification_code': None,
             **{key: result['plan'][key] for key in ('metrics', 'filters', 'dimensions')}}
    return engine, ConversationTurn(question, question, 0, state)


@pytest.mark.parametrize('question, fragments', [
    ('那2024年华南呢', ('2024年', '华南', '销售额')),
    ('那华南呢', ('2025年', '华南', '销售额')),
    ('那2024年呢', ('2024年', '华东', '销售额')),
    ('换成订单数', ('2025年', '华东', '订单数')),
])
def test_short_followup_preplanning_scope(context, question, fragments):
    engine, previous = context
    scope, audit = resolve_sql_followup_scope(question, [previous], engine)
    assert audit['mode'] == 'server_verified_sql_followup'
    assert all(fragment in scope for fragment in fragments)
    assert audit['actual_question'] == question
    assert audit['base_scope_question'] == previous.effective_question
    assert audit['replacements']


@pytest.mark.parametrize('question', [
    '那2024年华南销售额呢', '2024年华南订单数呢',
    '那华南只看金额超过1000的呢', '那华南排除退货呢',
    '那火星呢', '那2024年和2025年呢', '那销售额和订单数呢',
    '那按月统计呢', '如果华南呢', '那呢',
])
def test_unknown_complex_or_fresh_scope_is_not_verified(context, question):
    engine, previous = context
    scope, audit = resolve_sql_followup_scope(question, [previous], engine)
    assert scope == question
    assert audit['mode'] == 'independent'


@pytest.mark.parametrize('mutation', ['clarification', 'route', 'filter', 'metric', 'scope', 'missing_status'])
def test_previous_turn_must_be_successful_matching_sql(context, mutation):
    engine, previous = context
    state = deepcopy(previous.state)
    base = previous.effective_question
    if mutation == 'clarification':
        state['pending_question'] = base
    elif mutation == 'route':
        state['route'] = 'document'
    elif mutation == 'filter':
        state['filters'] = []
    elif mutation == 'metric':
        state['metrics'] = [{'table': 'sales_orders', 'column': 'order_id', 'function': 'COUNT'}]
    elif mutation == 'scope':
        base = '2024年华南销售额'
    else:
        state.pop('pending_question')
    previous = ConversationTurn(previous.question, base, 0, state)
    scope, audit = resolve_sql_followup_scope('那华南呢', [previous], engine)
    assert scope == '那华南呢'
    assert audit['mode'] == 'independent'


def test_no_history_no_provider_call(context):
    engine, previous = context
    class NeverCall:
        def generate(self, *args, **kwargs):
            raise AssertionError('model not permitted')
    engine.model_plan_provider = NeverCall()
    assert resolve_sql_followup_scope('那华南呢', [], engine)[0] == '那华南呢'
    assert resolve_sql_followup_scope('那华南呢', [previous], engine)[1]['mode'] == 'server_verified_sql_followup'


@pytest.mark.parametrize('incorrect_scope', ['2024年华南销售额', '2025年华南订单数', '华南销售额'])
def test_contextualizer_cannot_silently_change_unmentioned_slots(context, monkeypatch, incorrect_scope):
    engine, previous = context
    monkeypatch.setattr(engine, 'contextualize', lambda *args: (incorrect_scope,
        {'mode': 'merged', 'appended': '', 'replaced': [{'slot': 'sales_orders.region', 'from': '华东', 'to': '华南'}]}))
    scope, audit = resolve_sql_followup_scope('那华南呢', [previous], engine)
    assert scope == '那华南呢'
    assert audit['mode'] == 'independent'


def _real_model_state(previous):
    state = deepcopy(previous.state)
    state['metrics'] = [{'id': 'revenue', 'table': 'sales_orders', 'column': 'sales_amount',
                         'function': 'SUM', 'label': '销售额', 'unit': 'currency',
                         'currency': 'CNY', 'missing': 'null', 'filters': []}]
    return state


@pytest.mark.parametrize('single_in', [False, True])
def test_actual_model_v2_history_matches_legacy_server_scope(context, single_in):
    engine, previous = context
    state = _real_model_state(previous)
    if single_in:
        entry = next(item for item in state['filters'] if item['column'] == 'region')
        entry['operator'], entry['value'] = 'IN', [entry['value']]
    turn = ConversationTurn(previous.question, previous.effective_question, 0, state)
    scope, audit = resolve_sql_followup_scope('那2024年华南呢', [turn], engine)
    assert audit['mode'] == 'server_verified_sql_followup'
    assert all(fragment in scope for fragment in ('2024年', '华南', '销售额'))


@pytest.mark.parametrize('slot,value', [('missing', 'zero'), ('currency', 'USD'), ('unit', 'count'),
                                      ('function', 'COUNT_DISTINCT'), ('column', 'order_id')])
def test_real_history_metadata_conflict_is_not_hidden_by_legacy_equivalence(context, slot, value):
    engine, previous = context
    state = _real_model_state(previous)
    state['metrics'][0][slot] = value
    turn = ConversationTurn(previous.question, previous.effective_question, 0, state)
    assert resolve_sql_followup_scope('那华南呢', [turn], engine)[1]['mode'] == 'independent'


@pytest.mark.parametrize('operator,value', [('NOT IN', ['华东']), ('IN', ['华东', '华南']),
                                         ('!=', '华东'), ('=', '华南')])
def test_filter_equivalence_does_not_remove_negation_or_extra_values(context, operator, value):
    engine, previous = context
    state = _real_model_state(previous)
    entry = next(item for item in state['filters'] if item['column'] == 'region')
    entry['operator'], entry['value'] = operator, value
    turn = ConversationTurn(previous.question, previous.effective_question, 0, state)
    assert resolve_sql_followup_scope('那2024年呢', [turn], engine)[1]['mode'] == 'independent'
