"""Colloquial history edits, checked against independent SQL and safety guards."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import sqlite3

import pytest

from backend.executed_scope_edit import ExecutedScopeEditAgent
from backend.conversational_sql_edit import conversational_edit
from backend.history_reference import ConversationReferenceAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database, initialize_rich_demo_data
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore, ConversationTurn
from backend.sql_history_scope import resolve_sql_followup_scope, saved_sql_context


@pytest.fixture(scope='module')
def engine(tmp_path_factory):
    root = tmp_path_factory.mktemp('conversational-edits')
    return Nl2SqlEngine(initialize_database(root/'db.sqlite'), reference_date=date(2026, 10, 9))


@pytest.fixture(scope='module')
def rich_engine(tmp_path_factory):
    root = tmp_path_factory.mktemp('conversational-edits-rich')
    return Nl2SqlEngine(initialize_rich_demo_data(root/'db.sqlite'), reference_date=date(2026, 10, 9))


def executed(engine, question, *, turn_id=None):
    result = engine.answer(question).to_dict()
    assert result['status'] == 'ok', (question, result.get('clarification'))
    state = {'route':'sql', 'pending_question':None, 'clarification_code':None,
        **{key:result['plan'][key] for key in ('filters', 'metrics', 'dimensions')},
        'executed_sql_context':saved_sql_context(question, result)}
    return ConversationTurn(question, question, 0, state, turn_id), result


def scalar(engine, year, region, expression='SUM(sales_amount)', *, channel=None):
    sql = f'SELECT {expression} FROM sales_orders WHERE order_date>=? AND order_date<? AND region=?'
    parameters = [f'{year}-01-01', f'{year+1}-01-01', region]
    if channel:
        sql += ' AND channel=?'
        parameters.append(channel)
    with sqlite3.connect(engine.database_path) as connection:
        return connection.execute(sql, parameters).fetchone()[0]


@pytest.mark.parametrize('template',[
    '同样条件，{year}年，{region}，订单数',
    '同样的条件下，时间改成{year}年，地区改成{region}，指标改成订单数',
    '沿用原有条件，年份{year}，地区{region}，订单数',
    '那{year}年，{region}，订单数呢',
    '相同范围下，{year}年，{region}，订单数',
    '保持原来条件，{year}年，{region}，订单数',
])
@pytest.mark.parametrize('year', [2024, 2025])
@pytest.mark.parametrize('region', ['华南', '华北', '华东'])
def test_three_omitted_slot_edits_match_independent_sql(engine, template, year, region):
    turn, _ = executed(engine, '2025年华东销售额')
    question = template.format(year=year, region=region)
    scope, audit = resolve_sql_followup_scope(question, [turn], engine)
    assert audit['mode'] == 'server_verified_executed_sql_edit', (question, audit)
    assert audit['actual_question'] == question
    assert len(audit['replacements']) == 3
    actual = engine.answer(scope).to_dict()
    assert actual['rows'] == [{'订单数':scalar(engine, year, region, 'COUNT(order_id)')}]
    assert actual['plan']['filters'] and scope == f'{year}年{region}订单数'


@pytest.mark.parametrize('template',[
    '只看{region}', '仅查{region}', '{region}也查一下', '{region}也看一下',
    '再查一下{region}', '同样条件，{region}',
])
@pytest.mark.parametrize('region', ['华南', '华北', '华东'])
def test_colloquial_region_replacement_keeps_time_metric_and_channel(engine, template, region):
    turn, _ = executed(engine, '2025年华东线上销售额')
    scope, audit = resolve_sql_followup_scope(template.format(region=region), [turn], engine)
    assert audit['mode'] == 'server_verified_executed_sql_edit', audit
    actual = engine.answer(scope).to_dict()
    assert actual['rows'] == [{'销售额':scalar(engine, 2025, region, channel='线上')}]


@pytest.mark.parametrize('template',[
    '再给我{metric}，其他条件不变', '再给出{metric}，其余都不变',
    '同样条件，{metric}',
])
@pytest.mark.parametrize('metric,expression',[
    ('订单数', 'COUNT(order_id)'), ('销量', 'SUM(quantity)'), ('平均单价', 'AVG(unit_price)'),
])
def test_metric_ellipsis_preserves_physical_aggregation(engine, template, metric, expression):
    turn, _ = executed(engine, '2025年华东线上销售额')
    scope, audit = resolve_sql_followup_scope(template.format(metric=metric), [turn], engine)
    assert audit['mode'] == 'server_verified_executed_sql_edit', audit
    result = engine.answer(scope).to_dict()
    assert list(result['rows'][0].values()) == [scalar(engine, 2025, '华东', expression, channel='线上')]
    assert result['plan']['metric_function'] == expression.split('(')[0]


@pytest.mark.parametrize('question, table, date_column, metric_column',[
    ('采购金额', 'purchase_orders', 'purchase_date', 'purchase_amount'),
    ('回款金额', 'payment_receipts', 'payment_date', 'collected_amount'),
    ('运营费用', 'operating_expenses', 'expense_date', 'expense_amount'),
    ('薪酬总额', 'payroll_records', 'payroll_date', 'salary_amount'),
    ('网页浏览量', 'web_traffic_daily', 'visit_date', 'page_views'),
])
def test_identical_history_grammar_transfers_to_other_business_domains(rich_engine, question, table, date_column, metric_column):
    engine = rich_engine
    turn, _ = executed(engine, f'2025年华东{question}')
    scope, audit = resolve_sql_followup_scope('同样条件，2024年，华南', [turn], engine)
    assert audit['mode'] == 'server_verified_executed_sql_edit', audit
    result = engine.answer(scope).to_dict()
    with sqlite3.connect(engine.database_path) as connection:
        expected = connection.execute(f'SELECT SUM({metric_column}) FROM {table} '
            f'WHERE {date_column}>=? AND {date_column}<? AND region=?',
            ('2024-01-01', '2025-01-01', '华南')).fetchone()[0]
    assert list(result['rows'][0].values()) == [expected]
    assert result['plan']['table'] == table


@pytest.mark.parametrize('command',[
    '同样条件，2024年，火星', '只看华南且不含线上', '同样条件，华南或者华北',
    '同样条件，时间改成2024年，时间改成2023年', '只看华南;DROP TABLE sales_orders',
    '再给我订单数，其他条件不变，但排除线上', '同样条件，不计NULL',
    '同样条件，先看2024年再看2025年', '同样条件，所有范围',
    '同样条件，2024年，华南，订单数，线上', '其他条件不变',
])
def test_unknown_prose_and_conflicting_edits_are_atomic_rejections(engine, command):
    turn, _ = executed(engine, '2025年华东线上销售额')
    result = ExecutedScopeEditAgent(engine).run(command, [turn])
    assert result is not None and not result.verified, (command, result)
    assert result.scope == turn.effective_question
    assert result.replacements == ()
    assert engine.answer(turn.effective_question).status == 'ok'


@pytest.mark.parametrize('tamper', ['hash', 'revision', 'filters', 'missing_record', 'failed'])
def test_colloquial_scope_cannot_authorize_unverified_history(engine, tamper):
    turn, _ = executed(engine, '2025年华东销售额')
    state = deepcopy(turn.state)
    if tamper == 'hash': state['executed_sql_context']['sha256'] = '0'*64
    elif tamper == 'revision': state['executed_sql_context']['payload']['source_revision'] = 'wrong'
    elif tamper == 'filters': state['filters'] = []
    elif tamper == 'missing_record': state.pop('executed_sql_context')
    else: state['pending_question'] = turn.effective_question; state['clarification_code'] = 'missing_metric'
    turn = ConversationTurn(turn.question, turn.effective_question, 0, state)
    scope, audit = resolve_sql_followup_scope('同样条件，华南', [turn], engine)
    assert audit['mode'] not in {'server_verified_executed_sql_edit','server_verified_sql_followup'}
    assert audit['requires_clarification'] is True


@pytest.mark.parametrize('base',[
    '2025年华东各品类销售额排名前3',
    '2025年华东各品类销售额超过10000',
    '2025年华东按月销售额趋势',
    '2025年华东不含线上销售额',
])
def test_ellipsis_never_loses_ranking_having_group_grain_or_negation(engine, base):
    turn, before = executed(engine, base)
    scope, audit = resolve_sql_followup_scope('只看华南', [turn], engine)
    assert audit['mode'] == 'server_verified_executed_sql_edit', audit
    result = engine.answer(scope).to_dict()
    for field in ('top_n', 'having', 'limit', 'dimensions', 'dimension_transforms',
                  'comparison_mode', 'order_desc', 'analysis_mode'):
        assert result['plan'][field] == before['plan'][field], field
    with sqlite3.connect(engine.database_path) as connection:
        if '排名' in base:
            rows = connection.execute('WITH grouped AS (SELECT product_category,SUM(sales_amount) AS amount '
                'FROM sales_orders WHERE order_date>=? AND order_date<? AND region=? GROUP BY product_category), '
                'ranked AS (SELECT *,DENSE_RANK() OVER (ORDER BY amount DESC) AS ranking FROM grouped) '
                'SELECT product_category,amount,ranking FROM ranked WHERE ranking<=3 ORDER BY ranking',
                ('2025-01-01','2026-01-01','华南')).fetchall()
        elif '超过' in base:
            rows = connection.execute('SELECT product_category,SUM(sales_amount) FROM sales_orders '
                'WHERE order_date>=? AND order_date<? AND region=? GROUP BY product_category HAVING SUM(sales_amount)>10000',
                ('2025-01-01','2026-01-01','华南')).fetchall()
        elif '按月' in base:
            rows = connection.execute("SELECT strftime('%Y-%m',order_date),SUM(sales_amount) FROM sales_orders "
                'WHERE order_date>=? AND order_date<? AND region=? GROUP BY 1 ORDER BY 1',
                ('2025-01-01','2026-01-01','华南')).fetchall()
        else:
            rows = connection.execute('SELECT SUM(sales_amount) FROM sales_orders WHERE order_date>=? '
                'AND order_date<? AND region=? AND channel!=?',
                ('2025-01-01','2026-01-01','华南','线上')).fetchall()
    assert sorted(tuple(row.values()) for row in result['rows']) == sorted(rows)


@pytest.mark.parametrize('expression',[
    '回到刚才那次数据库查询，那华南呢',
    '接着上一条问数查询，只看华南',
    '回到上一次SQL查询，同样条件，华南',
])
def test_natural_explicit_reference_resolves_executed_sql_only(engine, expression):
    turn, _ = executed(engine, '2025年华东销售额')
    document = ConversationTurn('保修多久', '保修多久', 1, {'route':'document'})
    scope, audit = resolve_sql_followup_scope(expression, [turn, document], engine)
    assert audit['context_reference']['history_index'] == 0
    assert engine.answer(scope).rows == ({'销售额':scalar(engine, 2025, '华南')},)


def test_reference_does_not_skip_failure_without_success_qualifier(engine):
    good, _ = executed(engine, '2025年华东销售额')
    failed = ConversationTurn('2025年华北未知字段', '2025年华北未知字段', 1,
        {'route':'sql','pending_question':'2025年华北未知字段','clarification_code':'missing_metric'})
    scope, audit = resolve_sql_followup_scope('回到刚才那次数据库查询，只看华南', [good, failed], engine)
    assert audit['requires_clarification'] is True
    assert audit['reason'] == 'requested_sql_reference_not_verified'


@pytest.mark.parametrize('prefix', ['同样条件', '同样的范围下', '相同条件', '保持原来条件'])
def test_explicit_keep_does_not_turn_failed_scope_into_complete_independent_sql(engine, prefix):
    failed = ConversationTurn('2025年华东线上销售额超过', '2025年华东线上销售额超过', 0,
        {'route':'sql', 'pending_question':'2025年华东线上销售额超过',
         'clarification_code':'unresolved_coverage', 'filters':[], 'metrics':[], 'dimensions':[]})
    question = prefix + '，2024年，华南，订单数'
    scope, audit = resolve_sql_followup_scope(question, [failed], engine)
    assert scope == question and audit['requires_clarification'] is True
    assert audit['mode'] == 'independent'


def test_group_transform_or_owner_change_cannot_hide_behind_same_column(engine, monkeypatch):
    turn, _ = executed(engine, '2025年华东按月销售额趋势')
    original = engine.extract_required_intent
    def tampered(question):
        plan = original(question)
        if '华南' in question:
            plan.dimension_transforms = {column:'year' for column in plan.dimensions}
        return plan
    monkeypatch.setattr(engine, 'extract_required_intent', tampered)
    scope, audit = resolve_sql_followup_scope('只看华南', [turn], engine)
    assert audit['mode'] == 'executed_sql_edit_rejected'
    assert scope == turn.effective_question


def test_twelve_turn_chain_keeps_scope_across_restart_and_switches(tmp_path):
    engine = Nl2SqlEngine(initialize_rich_demo_data(tmp_path/'db.sqlite'))
    agent = OmniAgent(engine, KnowledgeStore(tmp_path/'knowledge'),
        ConversationStore(storage_path=tmp_path/'sessions.sqlite', max_turns=16))
    questions = [
        '2025年华东线上销售额', '只看华南', '同样条件，2024年',
        '再给我订单数，其他条件不变', '华北也查一下', '删除渠道筛选',
        '同样条件，2025年，华中，销售额', '只看华南',
        '2025年华东网页浏览量', '同样条件，2024年，华北',
        '回到倒数第四次SQL查询，只看华北', '再给我订单数，其余不变',
    ]
    expected_scopes = [
        (2025,'华东','SUM(sales_amount)','线上'), (2025,'华南','SUM(sales_amount)','线上'),
        (2024,'华南','SUM(sales_amount)','线上'), (2024,'华南','COUNT(order_id)','线上'),
        (2024,'华北','COUNT(order_id)','线上'), (2024,'华北','COUNT(order_id)',None),
        (2025,'华中','SUM(sales_amount)',None), (2025,'华南','SUM(sales_amount)',None),
        None, None, (2025,'华北','SUM(sales_amount)',None), (2025,'华北','COUNT(order_id)',None),
    ]
    for index, (question, expected) in enumerate(zip(questions, expected_scopes)):
        if index == 6:
            agent.conversations = ConversationStore(storage_path=agent.conversations.storage_path, max_turns=16)
        result = agent.query(question, session_id='long-chain')
        assert result['status'] == 'ok', (index, question, result)
        if expected:
            year, region, metric, channel = expected
            assert list(result['result']['rows'][0].values()) == [scalar(engine, year, region, metric, channel=channel)]
        else:
            assert result['result']['plan']['table'] == 'web_traffic_daily'
    isolated = agent.query('只看华南', session_id='other')
    assert isolated['status'] != 'ok' and not isolated['result'].get('sql')
    assert isolated['context_turns'] == 0


@pytest.mark.parametrize('command', ['只看华南', '华南也查一下', '同样条件，华南'])
def test_explicit_value_addition_when_old_scope_has_no_filter(engine, command):
    turn, _ = executed(engine, '2025年销售额')
    scope, audit = resolve_sql_followup_scope(command, [turn], engine)
    assert audit['mode'] == 'server_verified_executed_sql_edit', audit
    assert audit['replacements'][0]['operation'] == 'add'
    assert engine.answer(scope).rows == ({'销售额':scalar(engine, 2025, '华南')},)


@pytest.mark.parametrize('question', ['2025年华东销售额', '2024年华南网页浏览量', '退货政策是什么'])
def test_new_independent_requests_add_no_schema_reads(question):
    class NoReads:
        def analyze_slots(self, *args, **kwargs):
            raise AssertionError('ordinary request must exit grammar before touching schema')
    assert conversational_edit(question, '2025年华东销售额', NoReads()) is None


def test_verified_colloquial_resolution_adds_no_model_requests(engine, monkeypatch):
    turn, _ = executed(engine, '2025年华东销售额')
    class NoCalls:
        supports_complex_queries = True
        reference_date = date(2026, 10, 9)
        @property
        def client(self):
            raise AssertionError('verified local history edit must not call model')
    monkeypatch.setattr(engine, 'model_plan_provider', NoCalls())
    scope, audit = resolve_sql_followup_scope('同样条件，2024年，华南，订单数', [turn], engine)
    assert audit['mode'] == 'server_verified_executed_sql_edit'
    assert scope == '2024年华南订单数'


@pytest.mark.parametrize('base',[
    '2025年各地区销售额', '2025年各个地区销售额', '2025年每个地区的销售额',
    '2025年各品类销量', '2025年每个品类的销量',
])
def test_implicit_group_phrase_can_be_replaced_without_retaining_old_dimension(engine, base):
    turn, before = executed(engine, base)
    scope, audit = resolve_sql_followup_scope('换成按渠道分组', [turn], engine)
    assert audit['mode'] == 'server_verified_sql_followup', audit
    result = engine.answer(scope).to_dict()
    assert result['plan']['dimensions'] == ['channel']
    metric = 'SUM(sales_amount)' if '销售额' in base else 'SUM(quantity)'
    with sqlite3.connect(engine.database_path) as connection:
        rows = connection.execute(f'SELECT channel, {metric} FROM sales_orders '
            'WHERE order_date>=? AND order_date<? GROUP BY channel',
            ('2025-01-01','2026-01-01')).fetchall()
    assert sorted(tuple(row.values()) for row in result['rows']) == sorted(rows)
    assert result['plan']['filters'] == before['plan']['filters']


def test_eight_turn_group_metric_time_chain_matches_independent_gold(rich_engine, tmp_path):
    agent = OmniAgent(rich_engine, KnowledgeStore(tmp_path/'knowledge'), ConversationStore())
    questions = [
        '2025年各地区销售额', '换成销量', '那2024年呢', '换成毛利',
        '换成按渠道分组', '那2025年呢', '换成销售额', '换成按地区分组',
    ]
    gold = [
        (2025, 'region', 'sales_amount'), (2025, 'region', 'quantity'),
        (2024, 'region', 'quantity'), (2024, 'region', 'gross_profit'),
        (2024, 'channel', 'gross_profit'), (2025, 'channel', 'gross_profit'),
        (2025, 'channel', 'sales_amount'), (2025, 'region', 'sales_amount'),
    ]
    for question, (year, dimension, metric) in zip(questions, gold):
        result = agent.query(question, session_id='frozen-group-chain')
        assert result['status'] == 'ok', (question, result.get('context_resolution'))
        plan = result['result']['plan']
        assert plan['dimensions'] == [dimension]
        assert plan['dimension_tables'] == {dimension:'sales_orders'}
        assert plan['metric_column'] == metric and plan['metric_function'] == 'SUM'
        with sqlite3.connect(rich_engine.database_path) as connection:
            expected = connection.execute(f'SELECT "{dimension}",SUM("{metric}") FROM sales_orders '
                f'WHERE order_date>=? AND order_date<? GROUP BY "{dimension}" ORDER BY SUM("{metric}") DESC',
                (f'{year}-01-01', f'{year+1}-01-01')).fetchall()
        assert sorted(tuple(row.values()) for row in result['result']['rows']) == sorted(expected)
        if question != questions[0]:
            assert result['context_resolution']['mode'] == 'server_verified_sql_followup'


def test_same_score_group_owners_are_not_selected_by_candidate_order(rich_engine, monkeypatch):
    turn, _ = executed(rich_engine, '2025年按渠道分组销售额')
    analyze = rich_engine.analyze_slots
    def tied(question, **kwargs):
        slots = analyze(question, **kwargs)
        if '按地区分组' in question:
            slots['dimensions'] = [replace(link, score=0.74) for link in slots['dimensions']]
        return slots
    monkeypatch.setattr(rich_engine, 'analyze_slots', tied)
    scope, audit = resolve_sql_followup_scope('换成按地区分组', [turn], rich_engine)
    assert scope == '换成按地区分组'
    assert audit['mode'] == 'independent' and audit['requires_clarification'] is True
