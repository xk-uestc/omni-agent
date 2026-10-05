"""Rejected contextualizations must not be reapplied below the scope verifier."""
from copy import deepcopy

import pytest

from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


def test_stable_reference_disambiguates_duplicate_sql_and_comparison_uses_exact_ids(agent):
    first=agent.query('2025年华东销售额',session_id='stable-duplicate')
    second=agent.query('2025年华东销售额',session_id='stable-duplicate')
    assert first['query_reference_id']!=second['query_reference_id']
    answer=agent.query(f"回到SQL查询编号{first['query_reference_id']}，那华南呢",session_id='stable-duplicate')
    assert answer['status']=='ok'
    assert answer['result']['rows']==agent.engine.answer('2025年华南销售额').to_dict()['rows']
    assert answer['context_resolution']['context_reference']['turn_id']==first['query_reference_id']
    compared=agent.query(f"比较SQL查询编号{first['query_reference_id']}和{answer['query_reference_id']}",session_id='stable-duplicate')
    assert compared['status']=='ok' and compared['route']=='comparison'
    # Comparisons now have their own stable turn identity for pending recovery.
    # That identity must never be accepted as an executed SQL source.
    assert compared['query_reference_id'] not in {first['query_reference_id'],second['query_reference_id'],answer['query_reference_id']}
    rejected=agent.query(f"回到SQL查询编号{compared['query_reference_id']}，那华北呢",session_id='stable-duplicate')
    assert rejected['status']=='clarification'
    assert rejected['context_resolution']['reason']=='requested_sql_reference_not_available'
    assert not rejected['result'].get('sql')
    returned=agent.query(f"回到SQL查询编号{first['query_reference_id']}，那华北呢",session_id='stable-duplicate')
    assert returned['status']=='ok' and returned['route']=='sql'
    assert returned['result']['rows']==agent.engine.answer('2025年华北销售额').to_dict()['rows']
    assert returned['context_resolution']['context_reference']['turn_id']==first['query_reference_id']


def test_stable_reference_from_another_session_clarifies_without_executing(agent):
    first=agent.query('2025年华东销售额',session_id='stable-source')
    rejected=agent.query(f"回到SQL查询编号{first['query_reference_id']}，那华南呢",session_id='stable-other')
    assert rejected['status']=='clarification'
    assert rejected['context_resolution']['reason']=='requested_sql_reference_not_available'
    assert not rejected['result'].get('sql')


@pytest.mark.parametrize('expression',['回到上一次SQL查询，那华北呢',
    '回到SQL查询“2025年华东销售额”，那华北呢',
    '回到SQL查询编号q_invalid，那华北呢'])
def test_explicit_sql_reference_takes_precedence_over_comparison_operand_edit(agent,expression):
    agent.query('2025年华东销售额',session_id='reference-priority')
    agent.query('2025年华南销售额',session_id='reference-priority')
    assert agent.query('比较刚才两次查询',session_id='reference-priority')['status']=='ok'
    result=agent.query(expression,session_id='reference-priority')
    assert result['route']!='comparison'
    if 'q_invalid' in expression:
        assert result['status']=='clarification'
        assert result['context_resolution']['reason']=='requested_sql_reference_expression_unsupported'
        assert not result['result'].get('sql')
    else:
        assert result['status']=='ok' and result['route']=='sql'
        assert result['result']['rows']==agent.engine.answer('2025年华北销售额').to_dict()['rows']


class SqlRouter:
    def generate(self, instructions, context, *args, **kwargs):
        return {"route": "sql", "effective_question": context["question"],
                "tasks_json": "[]", "clarification": ""}


def test_typed_time_and_grain_continue_the_pending_question(agent):
    pending=agent.query('销售额趋势',session_id='typed-time')
    assert pending['status']=='clarification'
    pending=agent.query('2025年',session_id='typed-time')
    assert pending['status']=='clarification'
    assert pending['result']['clarification_code']=='missing_time_grain'
    assert '销售额趋势' in pending['effective_question'] and '2025年' in pending['effective_question']
    answer=agent.query('按月',session_id='typed-time')
    assert answer['status']=='ok'
    assert answer['result']['rows']==agent.engine.answer('销售额趋势 2025年 按月').to_dict()['rows']
    assert answer['context_resolution']['mode']=='server_verified_sql_clarification_time_fill'


@pytest.mark.parametrize('time_reply,grain_reply',[
    ('看2025年吧','按月统计'),('就看2025年即可','选择按月趋势'),
    ('2025年。','按月汇总！'),('用2025年','按月趋势'),
])
def test_natural_clarification_keeps_accumulated_scope_and_matches_independent_sql(agent,time_reply,grain_reply):
    session='natural-fill'
    assert agent.query('销售额趋势',session_id=session)['status']=='clarification'
    pending=agent.query(time_reply,session_id=session)
    assert pending['status']=='clarification'
    assert pending['result']['clarification_code']=='missing_time_grain'
    assert '销售额趋势' in pending['effective_question']
    answer=agent.query(grain_reply,session_id=session)
    assert answer['status']=='ok'
    assert answer['result']['rows']==agent.engine.answer('2025年按月销售额趋势').to_dict()['rows']
    assert answer['context_resolution']['mode'] in {
        'server_verified_sql_clarification_time_fill','server_verified_sql_clarification_option_fill'}


def test_natural_grain_preserves_already_confirmed_region_and_time(agent):
    pending=agent.query('2025年华东销售额趋势',session_id='grain-region')
    assert pending['result']['clarification_code']=='missing_time_grain'
    answer=agent.query('按月统计吧',session_id='grain-region')
    assert answer['status']=='ok'
    assert answer['result']['rows']==agent.engine.answer('2025年华东按月销售额趋势').to_dict()['rows']


def test_typed_offered_dimension_resumes_ranking(agent):
    pending=agent.query('2025年销售额排名',session_id='typed-dimension')
    assert pending['status']=='clarification'
    option=next(o for o in pending['result']['clarification_options'] if o['value']=='region')
    answer=agent.query('选择'+option['label']+'吧',session_id='typed-dimension')
    assert answer['status']=='ok'
    assert answer['result']['rows']==agent.engine.answer('2025年按地区销售额排名').to_dict()['rows']
    assert answer['context_resolution']['mode']=='server_verified_sql_clarification_option_fill'


@pytest.mark.parametrize('reply',['按月统计并排除华东','选择按月趋势或者按年趋势','看2025年吧;DROP TABLE sales_orders'])
def test_compound_reply_is_not_treated_as_single_offered_choice(agent,reply):
    agent.query('华东销售额趋势',session_id='compound-fill')
    agent.query('2025年',session_id='compound-fill')
    answer=agent.query(reply,session_id='compound-fill')
    assert answer['context_resolution'].get('mode') not in {
        'server_verified_sql_clarification_time_fill','server_verified_sql_clarification_option_fill'}


def test_duplicate_offered_labels_do_not_choose_first_option(agent,monkeypatch):
    from backend.sql_history_scope import _resolve_pending_option
    from types import SimpleNamespace
    monkeypatch.setattr(agent.engine,'extract_required_intent',lambda _:SimpleNamespace(
        clarification_code='ambiguous_dimension',clarification_options=[
            {'label':'国家','value':'Customer.Country'},{'label':'国家','value':'Invoice.BillingCountry'}]))
    previous=SimpleNamespace(effective_question='按国家统计金额',state={
        'route':'sql','pending_question':'按国家统计金额','clarification_code':'ambiguous_dimension'})
    assert _resolve_pending_option('选择国家',previous,agent.engine) is None


def test_typed_entity_option_selects_exact_value_without_losing_question(agent):
    pending=agent.query('星河的销售额',session_id='typed-entity')
    assert pending['result']['clarification_code']=='ambiguous_value'
    option=next(o for o in pending['result']['clarification_options'] if o['value'].endswith('=>星河 Pro'))
    answer=agent.query('选择'+option['label'],session_id='typed-entity')
    assert answer['status']=='ok'
    assert answer['result']['rows']==agent.engine.answer('星河 Pro的销售额').to_dict()['rows']


def test_independent_query_does_not_consume_pending_choice(agent):
    agent.query('2025年华东销售额趋势',session_id='independent-option')
    answer=agent.query('2024年华南订单数',session_id='independent-option',reset_context=True)
    assert answer['status']=='ok'
    assert answer['result']['rows']==agent.engine.answer('2024年华南订单数').to_dict()['rows']
    assert answer['context_resolution']['mode']=='independent'


@pytest.mark.parametrize('reply',['2025-13','2025年 排除华东','2025年;DROP TABLE sales_orders'])
def test_invalid_or_compound_date_is_not_a_verified_time_selection(agent,reply):
    agent.query('销售额趋势',session_id='invalid-time')
    answer=agent.query(reply,session_id='invalid-time')
    assert answer['context_resolution'].get('mode')!='server_verified_sql_clarification_time_fill'


def test_natural_five_turn_followups_preserve_executed_results(agent):
    turns = [
        ('2025年华东销售额', '2025年华东销售额'),
        ('再查华南', '2025年华南销售额'),
        ('同样2024年', '2024年华南销售额'),
        ('换成订单数', '2024年华南订单数'),
        ('按渠道分组', '2024年华南订单数 按渠道分组'),
    ]
    for index, (question, expected_question) in enumerate(turns):
        answer = agent.query(question, session_id='natural-five')
        expected = agent.engine.answer(expected_question).to_dict()
        assert answer['status'] == expected['status'] == 'ok'
        assert answer['result']['rows'] == expected['rows']
        if index:
            assert answer['context_resolution']['mode'] == 'server_verified_sql_followup'


@pytest.mark.parametrize('mutation', ['question', 'sql', 'parameters'])
def test_modified_execution_record_cannot_seed_followup(agent, mutation):
    first = agent.query('2025年华东销售额', session_id='modified-record')
    state = deepcopy(first['state'])
    state['executed_sql_context']['payload'][mutation] = [] if mutation == 'parameters' else 'changed'
    agent.conversations.remember('modified-record', question=first['question'],
                                 effective_question=first['effective_question'], state=state)
    answer = agent.query('再查华南', session_id='modified-record')
    assert answer['status'] == 'clarification'
    assert answer['context_resolution']['reason'] == 'sql_history_execution_context_invalid'
    assert not answer['result'].get('rows')
    fresh = agent.query('2024年华南订单数', session_id='modified-record')
    assert fresh['status'] == 'ok'


@pytest.mark.parametrize('field,expected', [
    ('Invoice.BillingCountry', {'GB': 30, 'CA': 35}),
    ('Customer.Country', {'US': 50, 'CA': 15}),
])
def test_dimension_clarification_accepts_offered_field_in_conversation(tmp_path, field, expected):
    import sqlite3
    db = tmp_path / 'roles.sqlite'
    with sqlite3.connect(db) as connection:
        connection.executescript(
            'CREATE TABLE Customer(CustomerId INTEGER PRIMARY KEY,Country TEXT);'
            'CREATE TABLE Invoice(InvoiceId INTEGER PRIMARY KEY,CustomerId INTEGER REFERENCES Customer(CustomerId),Total REAL,BillingCountry TEXT);'
            "INSERT INTO Customer VALUES(1,'US'),(2,'CA');"
            "INSERT INTO Invoice VALUES(10,1,30,'GB'),(11,1,20,'CA'),(12,2,15,'CA');")
    agent = OmniAgent(Nl2SqlEngine(db), KnowledgeStore(tmp_path / 'knowledge'),
                      ConversationStore(), SqlRouter())
    first = agent.query('按国家统计订单金额', session_id='dimension-choice')
    assert first['result']['clarification_code'] == 'ambiguous_dimension'
    from backend.sql_history_scope import resolve_sql_followup_scope
    rejected, audit = resolve_sql_followup_scope('选择Invoice.Total',
        agent.conversations.context('dimension-choice'), agent.engine)
    assert rejected == '选择Invoice.Total'
    assert audit['mode'] == 'independent'
    answer = agent.query('选择' + field, session_id='dimension-choice')
    assert answer['status'] == 'ok'
    assert answer['context_resolution']['selected_role'] == 'dimension'
    result = answer['result']
    assert {row[field.split('.')[1]]: row[result['plan']['metric_label']]
            for row in result['rows']} == expected


@pytest.mark.parametrize('question', ['再查华南，不含线上', '同样2024年，如果超过1000', '按火星分组'])
def test_natural_followups_do_not_drop_unknown_conditions(agent, question):
    assert agent.query('2025年华东销售额', session_id='unknown-natural')['status'] == 'ok'
    answer = agent.query(question, session_id='unknown-natural')
    assert answer['status'] == 'clarification'
    assert not answer['result'].get('rows')


def test_plain_group_followup_without_history_does_not_invent_metric(agent):
    answer = agent.query('按渠道分组', session_id='new-group')
    assert answer['status'] == 'clarification'
    assert not answer['result'].get('rows')


def test_long_dialogue_survives_history_window_and_store_restart(tmp_path):
    database = initialize_database(tmp_path / 'long.sqlite')
    knowledge = KnowledgeStore(tmp_path / 'long-knowledge')
    path = tmp_path / 'long-sessions.sqlite'
    def build():
        return OmniAgent(Nl2SqlEngine(database), knowledge,
            ConversationStore(storage_path=path, max_turns=4), SqlRouter())
    agent = build()
    turns = [('2025年华东销售额', '2025年华东销售额'),
             ('再查华南', '2025年华南销售额'),
             ('同样2024年', '2024年华南销售额'),
             ('换成订单数', '2024年华南订单数'),
             ('那华北呢', '2024年华北订单数'),
             ('那2025年呢', '2025年华北订单数'),
             ('那华东呢', '2025年华东订单数'),
             ('换成销售额', '2025年华东销售额'),
             ('再查华南', '2025年华南销售额'),
             ('同样2024年', '2024年华南销售额'),
             ('换成订单数', '2024年华南订单数'),
             ('按渠道分组', '2024年华南订单数 按渠道分组')]
    for index, (question, expected_question) in enumerate(turns):
        if index == 6:
            agent = build()
        answer = agent.query(question, session_id='long-dialogue')
        assert answer['status'] == 'ok'
        assert answer['result']['rows'] == agent.engine.answer(expected_question).to_dict()['rows']
        assert answer['context_turns'] == min(index, 4)
    reset = agent.query('按渠道分组', session_id='long-dialogue', reset_context=True)
    assert reset['status'] == 'clarification'
    assert not reset['result'].get('rows')
    independent = agent.query('2024年华南销售额', session_id='another-session')
    assert independent['status'] == 'ok' and independent['context_turns'] == 0


def test_field_choice_can_fill_one_gap_while_preserving_next_clarification(agent):
    first = agent.query('2025年华东地区', session_id='staged-field')
    assert first['status'] == 'clarification'
    answer = agent.query('选择customers.customer_id', session_id='staged-field')
    assert answer['status'] == 'clarification'
    assert answer['result']['clarification_code'] == 'missing_date_column'
    assert answer['result']['plan']['metric_table'] == 'customers'
    assert answer['context_resolution']['remaining_clarification_code'] == 'missing_date_column'
    assert '2025年华东地区' in answer['effective_question']
    assert not answer['result'].get('rows')
    fresh = agent.query('2024年华南订单数', session_id='staged-field')
    assert fresh['status'] == 'ok'


def test_offered_physical_metric_choice_uses_same_resolver_as_click_selection(agent):
    first = agent.query('2025年华东地区', session_id='physical-fill')
    assert first['status'] == 'clarification'
    answer = agent.query('sales_orders.customer_id', session_id='physical-fill')
    expected = agent.engine.answer('2025年华东地区 客户数 [field:metric:sales_orders.customer_id]').to_dict()
    assert answer['status'] == 'ok'
    assert answer['result']['rows'] == expected['rows']
    assert answer['context_resolution']['selected_role'] == 'metric'


def test_explicit_topic_return_reuses_verified_sql_after_document_interlude(agent):
    first = agent.query('2025年华东销售额', session_id='topic-return')
    agent.conversations.remember('topic-return', question='业务指标定义是什么',
        effective_question='业务指标定义是什么', state={'route': 'document', 'sources': ['definition']})
    answer = agent.query('回到上一次SQL查询，那华南呢', session_id='topic-return')
    assert answer['status'] == 'ok'
    assert answer['result']['rows'] == agent.engine.answer('2025年华南销售额').to_dict()['rows']
    assert answer['context_resolution']['context_reference']['history_index'] == 0
    assert answer['context_resolution']['base_scope_question'] == first['effective_question']


def test_topic_return_does_not_silently_skip_failed_sql_turn(agent):
    agent.query('2025年华东销售额', session_id='topic-failure')
    agent.query('2025年华南销售额超过', session_id='topic-failure')
    answer = agent.query('回到上一次SQL查询，那华北呢', session_id='topic-failure')
    assert answer['status'] == 'clarification'
    assert answer['context_resolution']['reason'] == 'requested_sql_reference_not_verified'
    assert not answer['result'].get('rows')
    recovered = agent.query('回到上一次成功的SQL查询，那华北呢', session_id='topic-failure')
    assert recovered['status'] == 'ok'
    assert recovered['result']['rows'] == agent.engine.answer('2025年华北销售额').to_dict()['rows']


def test_topic_reference_outside_window_requests_clarification(agent):
    agent.query('2025年华东销售额', session_id='lost-reference')
    for index in range(agent.conversations.max_turns):
        agent.conversations.remember('lost-reference', question=f'definition-{index}',
            effective_question=f'definition-{index}', state={'route': 'document'})
    answer = agent.query('回到上一次SQL查询，那华南呢', session_id='lost-reference')
    assert answer['status'] == 'clarification'
    assert answer['context_resolution']['reason'] == 'requested_sql_reference_not_available'
    assert not answer['result'].get('rows')


@pytest.mark.parametrize('reference',['回到倒数第二次SQL查询，那华南呢',
                                    '回到SQL查询“2025年华东销售额”，那华南呢'])
def test_selected_earlier_sql_uses_its_own_year_and_metric_not_latest(agent,reference):
    import sqlite3
    first=agent.query('2025年华东销售额',session_id='earlier')
    agent.query('2024年华北订单数',session_id='earlier')
    agent.conversations.remember('earlier',question='definition',effective_question='definition',state={'route':'document'})
    answer=agent.query(reference,session_id='earlier')
    assert answer['status']=='ok'
    with sqlite3.connect(agent.engine.database_path) as connection:
        expected=connection.execute("SELECT SUM(sales_amount) FROM sales_orders WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' AND region='华南'").fetchone()[0]
    assert answer['result']['rows']==[{'销售额':expected}]
    assert answer['context_resolution']['base_scope_question']==first['effective_question']
    assert answer['context_resolution']['context_reference']['history_index']==0


def test_quote_ambiguity_does_not_execute_new_sql_or_choose_latest(agent,monkeypatch):
    agent.query('2025年华东销售额',session_id='duplicate')
    agent.query('2025年华东销售额',session_id='duplicate')
    monkeypatch.setattr(agent.engine,'answer',lambda *a,**k:pytest.fail('ambiguous reference must not execute SQL'))
    answer=agent.query('回到SQL查询“2025年华东销售额”，那华南呢',session_id='duplicate')
    assert answer['status']=='clarification'
    assert answer['context_resolution']['reason']=='requested_sql_reference_ambiguous'
    assert '多次查询' in answer['result']['clarification']


@pytest.mark.parametrize('reference',['回到倒数第二次SQL查询，那华南呢','回到第一次SQL查询，那华南呢'])
def test_empty_or_absolute_reference_rejects_a_model_reroute(agent,reference,monkeypatch):
    class WrongRouter:
        def generate(self,*args,**kwargs):
            pytest.fail('unavailable explicit SQL references must clarify before model planning')
    agent.client=WrongRouter()
    monkeypatch.setattr(agent.engine,'answer',lambda *a,**k:pytest.fail('unavailable reference must not execute SQL'))
    answer=agent.query(reference,session_id='empty')
    assert answer['route']=='clarify' and answer['status']=='clarification'
    assert answer['context_resolution']['requires_clarification'] is True


def test_backward_reference_is_revalidated_after_source_revision_changes(agent,monkeypatch):
    agent.query('2025年华东销售额',session_id='revision-reference')
    agent.query('2024年华北订单数',session_id='revision-reference')
    monkeypatch.setattr(agent.engine,'current_source_revision',lambda:{'changed':True})
    answer=agent.query('回到倒数第二次SQL查询，那华南呢',session_id='revision-reference')
    assert answer['status']=='clarification'
    assert answer['context_resolution']['reason']=='requested_sql_reference_not_verified'
    assert not answer['result'].get('rows')


def test_verified_reference_does_not_ask_model_to_choose_a_different_route(agent):
    agent.query('2025年华东销售额',session_id='fixed-route')
    agent.query('2024年华北订单数',session_id='fixed-route')
    class NoIntentModel:
        def generate(self,*args,**kwargs):
            pytest.fail('verified explicit SQL reference owns its route')
    agent.client=NoIntentModel()
    answer=agent.query('回到倒数第二次SQL查询，那华南呢',session_id='fixed-route')
    assert answer['route']=='sql' and answer['status']=='ok'
    assert 'server_verified_explicit_sql_reference' in answer['trace'][0]['normalizations']


@pytest.fixture
def agent(tmp_path):
    return OmniAgent(Nl2SqlEngine(initialize_database(tmp_path / "sales.sqlite")),
                     KnowledgeStore(tmp_path / "knowledge"), ConversationStore(), SqlRouter())


def test_rejected_time_mutation_cannot_be_reapplied_after_model_router(agent, monkeypatch):
    assert agent.query("2025年华东销售额", session_id="reject-time")["status"] == "ok"
    monkeypatch.setattr(agent.engine, "contextualize", lambda *args: (
        "2024年华南销售额", {"mode": "merged", "appended": "", "replaced": [
            {"slot": "sales_orders.region", "from": "华东", "to": "华南"}]}))
    answer = agent.query("那华南呢", session_id="reject-time")
    assert answer["context_resolution"]["mode"] == "independent"
    assert answer["context_resolution"]["requires_clarification"] is True
    assert answer["effective_question"] == "那华南呢"
    assert answer["status"] == "clarification"
    assert answer["result"].get("rows", []) == []


def test_unverified_saved_sql_filters_do_not_supply_query_defaults(agent):
    first = agent.query("2025年华东销售额", session_id="reject-state")
    assert first["status"] == "ok"
    state = deepcopy(first["state"])
    state["filters"] = []
    agent.conversations.remember("reject-state", question=first["question"],
                                 effective_question=first["effective_question"], state=state)
    answer = agent.query("那华南呢", session_id="reject-state")
    assert answer["context_resolution"]["mode"] == "independent"
    assert answer["context_resolution"]["requires_clarification"] is True
    assert answer["effective_question"] == "那华南呢"
    assert answer["status"] == "clarification"


def test_unknown_exclusion_cannot_be_dropped_by_context_rewriter(agent, monkeypatch):
    assert agent.query("2025年华东销售额", session_id="reject-exclusion")["status"] == "ok"
    monkeypatch.setattr(agent.engine, "contextualize", lambda *args: (
        "2025年华南销售额", {"mode": "merged", "appended": "", "replaced": [
            {"slot": "sales_orders.region", "from": "华东", "to": "华南"}]}))
    question = "那华南呢，不含线上"
    answer = agent.query(question, session_id="reject-exclusion")
    assert answer["context_resolution"]["mode"] == "independent"
    assert answer["context_resolution"]["requires_clarification"] is True
    assert answer["effective_question"] == question
    assert answer["status"] == "clarification"


def test_verified_five_turn_slots_still_execute_exact_regions_years_metrics(agent):
    turns = [("2025年华东销售额", "2025", "华东", "销售额"),
             ("那华南呢", "2025", "华南", "销售额"),
             ("那2024年呢", "2024", "华南", "销售额"),
             ("那订单数呢", "2024", "华南", "订单数"),
             ("那华北呢", "2024", "华北", "订单数")]
    for index, (question, year, region, metric) in enumerate(turns):
        answer = agent.query(question, session_id="verified-five")
        expected = agent.engine.answer(f"{year}年{region}{metric}").to_dict()
        assert answer["status"] == "ok"
        assert answer["result"]["rows"] == expected["rows"]
        assert answer["context_turns"] == index
        if index:
            assert answer["context_resolution"]["mode"] == "server_verified_sql_followup"


@pytest.mark.parametrize("metric", ["销售额", "订单数", "销售额平均值"])
def test_pending_missing_metric_accepts_short_answer_and_preserves_scope(agent, metric):
    first = agent.query("2025年华东地区", session_id="refill")
    assert first["status"] == "clarification"
    answer = agent.query(metric, session_id="refill")
    expected = agent.engine.answer("2025年华东地区 " + metric).to_dict()
    assert answer["status"] == "ok"
    assert answer["result"]["rows"] == expected["rows"]
    assert answer["context_resolution"]["mode"] == "server_verified_sql_clarification_fill"


def test_metric_reply_after_unresolved_constraint_does_not_reuse_failed_scope(agent):
    first = agent.query("2025年华东销售额超过", session_id="unresolved")
    assert first["status"] == "clarification"
    answer = agent.query("那订单数呢", session_id="unresolved")
    assert answer["context_resolution"]["mode"] == "independent"
    assert answer["context_resolution"]["requires_clarification"] is True
    assert answer["effective_question"] == "那订单数呢"
    assert answer["status"] == "clarification"
    assert not answer["result"].get("rows")


def test_pending_refill_keeps_unknown_original_conditions_and_clarifies(agent):
    first = agent.query("2025年华东地区排除火星", session_id="unknown")
    assert first["status"] == "clarification"
    answer = agent.query("销售额", session_id="unknown")
    assert answer["context_resolution"]["mode"] == "independent"
    assert answer["context_resolution"]["requires_clarification"] is True
    assert answer["effective_question"] == "销售额"
    assert answer["status"] == "clarification"
    assert not answer["result"].get("rows")
    assert answer["state"]["pending_sql_scope"] == first["effective_question"]
    again = agent.query("订单数", session_id="unknown")
    assert again["status"] == "clarification"
    assert not again["result"].get("rows")
    fresh = agent.query("2024年华南订单数", session_id="unknown")
    assert fresh["status"] == "ok"
    assert fresh["effective_question"] == "2024年华南订单数"


def test_execution_failure_is_persisted_and_independent_next_turn_recovers(agent, monkeypatch):
    from backend.nl2sql.security import SqlSafetyError
    original = agent.engine.answer
    def reject(question, **kwargs):
        raise SqlSafetyError('secret_candidate_must_not_leak')
    monkeypatch.setattr(agent.engine, 'answer', reject)
    first = agent.query('2025年华东销售额', session_id='execution-failure')
    assert first['status'] == 'incomplete'
    assert first['result']['result_state'] == 'unexecuted'
    assert 'secret_candidate' not in str(first)
    assert 'executed_sql_context' not in first['state']
    monkeypatch.setattr(agent.engine, 'answer', original)
    followup = agent.query('那华南呢', session_id='execution-failure')
    assert followup['status'] == 'clarification'
    assert followup['context_turns'] == 1
    recovered = agent.query('2024年华南销售额', session_id='execution-failure')
    assert recovered['status'] == 'ok' and recovered['context_turns'] == 2


def test_invalid_saved_scope_plus_metric_followup_cannot_query_all_rows(agent):
    first = agent.query("2025年华东销售额", session_id="invalid-metric")
    state = deepcopy(first["state"])
    state["filters"] = []
    agent.conversations.remember("invalid-metric", question=first["question"],
                                 effective_question=first["effective_question"], state=state)
    answer = agent.query("那订单数呢", session_id="invalid-metric")
    assert answer["status"] == "clarification"
    assert not answer["result"].get("rows")


@pytest.mark.parametrize("previous", ["2025年华东地区", "2025年华东销售额"])
@pytest.mark.parametrize("question", ["文档中销售额是如何定义的？", "那文档中的销售额定义呢？"])
def test_document_metric_definition_is_not_blocked_as_sql_reply(agent, previous, question):
    agent.knowledge.ingest("销售额定义：已经确认的订单收入，不包括尚未成交的报价。".encode(),
                           document_id="definition", title="业务指标定义", modality="txt", filename="definition.txt")
    agent.query(previous, session_id="definition")
    class DefinitionRouter(SqlRouter):
        def generate(self, instructions, context, *args, **kwargs):
            assert context["actual_question"] == question
            return {"route": "document", "effective_question": "错误的订单数定义",
                    "tasks_json": "[]", "clarification": ""}
    agent.client = DefinitionRouter()
    answer = agent.query(question, session_id="definition")
    assert answer["route"] == "document"
    assert answer["effective_question"] == question
    assert answer["status"] == "ok"
    assert "销售额定义" in answer["result"]["answer"]
    assert not answer["state"].get("pending_sql_scope")


def test_full_query_after_missing_metric_clears_planning_history(agent):
    assert agent.query("2025年华东地区", session_id="fresh-pending")["status"] == "clarification"
    class FreshRouter(SqlRouter):
        def generate(self, instructions, context, *args, **kwargs):
            assert context["history"] == []
            return super().generate(instructions, context, *args, **kwargs)
    agent.client = FreshRouter()
    answer = agent.query("2024年华南订单数", session_id="fresh-pending")
    assert answer["status"] == "ok"
    assert answer["effective_question"] == "2024年华南订单数"
