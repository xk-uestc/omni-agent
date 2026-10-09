"""Local concept grounding across independent schemas, with refusal controls."""
from __future__ import annotations

import json
import time

import pytest

from backend.nl2sql.models import ColumnInfo, TableInfo
from backend.nl2sql.question_roles import _group_spans
from backend.nl2sql.schema import AliasRule, SchemaLinker
from backend.nl2sql.semantic_graph import SemanticGraph, needs_normalization
from backend.nl2sql.semantics import MetricCatalog


def _schema(table="sales_orders", fields=None):
    fields = fields or {
        "turnover_amount": "销售额", "total_cost": "成本", "net_profit": "净利润",
        "sold_quantity": "销量", "page_views": "网页浏览量", "visits": "访问次数",
    }
    tables = (TableInfo(table, tuple(ColumnInfo(name, "REAL", True, False) for name in fields)),)
    rules = tuple(AliasRule(table, name, (label,), "metric", "SUM") for name, label in fields.items())
    return tables, rules


@pytest.mark.parametrize("owner", ["sales_orders", "order_items", "commerce_fact", "交易数据", "f_019"])
@pytest.mark.parametrize("phrase,canonical", [
    ("商品卖了多少钱", "商品销售额"),
    ("商品卖出多少金额", "商品销售额"),
    ("商品卖出去多少钱", "商品销售额"),
    ("商品进账了多少钱", "商品销售额"),
    ("商品赚了多少钱", "商品净利润"),
    ("商品挣了多少", "商品净利润"),
    ("商品赚到多少钱", "商品净利润"),
    ("商品盈利了多少钱", "商品净利润"),
    ("商品花了多少钱", "商品成本"),
    ("商品花掉多少钱", "商品成本"),
    ("商品支出多少钱", "商品成本"),
    ("商品本金多少", "商品成本多少"),
    ("商品本钱多少", "商品成本多少"),
    ("商品卖了多少件", "商品销量"),
    ("网页被看了多少次", "网页浏览量"),
    ("网站访问了多少次", "访问次数"),
    ("PV", "网页浏览量"),
])
def test_colloquial_concepts_ground_across_schemas(owner, phrase, canonical):
    tables, rules = _schema(owner)
    result = SemanticGraph().normalize("2025年各地区" + phrase, tables, rules)
    assert result.normalized_question == "2025年各地区" + canonical
    assert result.clarification is None
    assert result.changed
    assert len(result.rewrites) == 1
    rewrite = result.rewrites[0]
    assert rewrite.table == owner
    assert rewrite.column in {c.name for c in tables[0].columns}
    assert rewrite.confidence >= 0.9
    assert rewrite.source_fields == (f"{owner}.{rewrite.column}",)
    assert rewrite.source_text == result.original_question[rewrite.start:rewrite.end]


@pytest.mark.parametrize("quote", ["'{}'", '"{}"', "`{}`", "“{}”", "‘{}’", "「{}」", "『{}』"])
@pytest.mark.parametrize("phrase", ["本金", "赚了多少钱", "花了多少钱", "本钱"])
def test_quoted_literals_never_rewritten(quote, phrase):
    tables, rules = _schema()
    question = "产品名称为" + quote.format(phrase) + "的2025年销售额"
    result = SemanticGraph().normalize(question, tables, rules)
    assert result.normalized_question == question
    assert not result.rewrites and not result.ambiguities


@pytest.mark.parametrize("literal", ["本金", "本金科技", "花了多少钱旗舰店", "赚了多少钱", "本钱"])
def test_unquoted_database_values_are_preserved(literal):
    tables, rules = _schema()
    question = literal + "的2025年销售额"
    result = SemanticGraph().normalize(question, tables, rules, protected_values=[literal])
    assert result.normalized_question == question
    assert not result.rewrites and not result.ambiguities


@pytest.mark.parametrize("cue", ["名称是", "名字为", "名叫", "叫做", "包含", "含有", "等于", "值为"])
def test_unquoted_filter_literals_are_not_reinterpreted(cue):
    tables, rules = _schema()
    question = "产品" + cue + "本金的2025年销售额"
    result = SemanticGraph().normalize(question, tables, rules)
    assert result.normalized_question == question
    assert not result.rewrites and not result.ambiguities


@pytest.mark.parametrize("cue", ["贷款", "借款", "还款", "存款", "理财", "利息", "银行", "债券"])
def test_financial_principal_cannot_be_guessed_as_cost(cue):
    tables, rules = _schema()
    question = f"2025年{cue}本金多少"
    result = SemanticGraph().normalize(question, tables, rules)
    assert result.normalized_question == question
    assert result.clarification
    assert result.ambiguities[0].concept == "principal_or_cost"
    assert not result.rewrites


@pytest.mark.parametrize("native", ["principal", "loan_principal", "本金", "loan_amount"])
def test_real_financial_fields_win_over_colloquial_cost(native):
    tables, rules = _schema("finance_loans", {native: "借款本金", "total_cost": "成本"})
    result = SemanticGraph().normalize("2025年贷款本金多少", tables, rules)
    assert result.normalized_question in {"2025年贷款本金多少", "2025年借款本金多少"}
    assert all(r.column == native and r.replacement != "成本" for r in result.rewrites)
    assert result.clarification is None


@pytest.mark.parametrize("question", ["本金多少", "本钱多少", "2025年各地区本金是多少"])
def test_principal_requires_a_business_context(question):
    tables, rules = _schema("mystery_fact", {"total_cost": "成本"})
    result = SemanticGraph().normalize(question, tables, rules)
    assert result.normalized_question == question
    assert result.clarification


def test_sales_schema_is_a_business_context():
    tables, rules = _schema("sales_orders", {"total_cost": "成本"})
    result = SemanticGraph().normalize("2025年各地区本金多少", tables, rules)
    assert result.normalized_question == "2025年各地区成本多少"


def test_unscoped_principal_does_not_assume_the_only_cost_field():
    tables, rules = _schema("sales_orders", {"total_cost": "成本"})
    result = SemanticGraph().normalize("本金是多少", tables, rules)
    assert result.clarification and not result.changed


def test_sealed_history_hint_resolves_scope_but_not_unsupported_columns():
    a, ar = _schema("fact_a", {"total_cost": "成本"})
    b, br = _schema("fact_b", {"total_cost": "成本"})
    result = SemanticGraph().normalize("那本金呢", a + b, ar + br,
                                      {"table": "fact_a", "question": "商品销售额是多少"})
    assert result.rewrites[0].table == "fact_a"
    assert result.normalized_question == "那fact_a.total_cost呢"
    assert result.clarification is None


def test_explicit_topic_change_does_not_use_previous_metric_owner():
    a, ar = _schema("fact_a", {"profit": "利润"})
    b, br = _schema("fact_b", {"profit": "利润"})
    result = SemanticGraph().normalize("换个话题，赚了多少钱", a + b, ar + br, {"table": "fact_a"})
    assert result.clarification
    assert not result.rewrites


def test_profit_ambiguity_preserves_gross_and_net_distinction():
    tables, rules = _schema(fields={"gross_profit": "毛利", "net_profit": "净利润"})
    result = SemanticGraph().normalize("2025年赚了多少钱", tables, rules)
    assert result.clarification
    assert len(result.ambiguities[0].candidates) == 2
    assert not result.rewrites


def test_explicit_profit_modifier_resolves_ambiguity_without_duplication():
    tables, rules = _schema(fields={"gross_profit": "毛利", "net_profit": "净利润"})
    result = SemanticGraph().normalize("2025年净利润赚了多少钱", tables, rules)
    assert result.normalized_question == "2025年净利润多少"
    assert result.rewrites[0].column == "net_profit"


def test_same_table_multiple_costs_requires_clarification():
    tables, rules = _schema(fields={"material_cost": "原料成本", "labor_cost": "人工成本"})
    result = SemanticGraph().normalize("商品本金多少", tables, rules)
    assert result.clarification and not result.changed


def test_group_scope_stops_before_first_metric_in_a_multi_metric_request():
    question = "2025年各渠道销售额和毛利分别合计"
    rules = (
        AliasRule("sales_orders", "channel", ("渠道",), "dimension"),
        AliasRule("sales_orders", "sales_amount", ("销售额",), "metric", "SUM"),
        AliasRule("sales_orders", "gross_profit", ("毛利",), "metric", "SUM"),
    )
    text = "".join(question.split())
    grouped_text = [text[start:end] for start, end in _group_spans(text, rules)]
    assert "渠道" in grouped_text
    assert all("销售额" not in scope and "毛利" not in scope for scope in grouped_text)


def test_each_channel_does_not_turn_sales_principal_into_unit_cost():
    sales_tables, sales_rules = _schema("sales_orders", {"total_cost": "销售成本"})
    product_tables, product_rules = _schema("products", {"unit_cost": "单位成本"})
    result = SemanticGraph().normalize("2025年每个渠道的卖货本金",
        sales_tables + product_tables, sales_rules + product_rules)
    assert result.clarification is None
    assert result.rewrites[0].table == "sales_orders"
    assert result.rewrites[0].column == "total_cost"


def test_exact_owner_resolves_colloquial_metric():
    a, ar = _schema("fact_a", {"profit": "利润"})
    b, br = _schema("fact_b", {"profit": "利润"})
    result = SemanticGraph().normalize("fact_b赚了多少钱", a + b, ar + br, {"table": "fact_a"})
    assert result.rewrites[0].table == "fact_b"
    assert result.normalized_question == "fact_bfact_b.profit"


@pytest.mark.parametrize("phrase,field", [
    ("进货花了多少钱", "purchase_amount"),
    ("采购花了多少钱", "purchase_amount"),
    ("物流花了多少钱", "shipping_cost"),
    ("运输花了多少钱", "shipping_cost"),
    ("薪水发了多少钱", "salary_amount"),
    ("员工花了多少钱", "salary_amount"),
    ("运营花了多少钱", "expense_amount"),
    ("报销花了多少钱", "expense_amount"),
])
def test_spending_concepts_use_local_business_context(phrase, field):
    tables, rules = _schema("operations", {"purchase_amount": "采购金额", "shipping_cost": "运费",
        "salary_amount": "薪酬总额", "expense_amount": "运营费用", "total_cost": "成本"})
    result = SemanticGraph().normalize("2025年" + phrase, tables, rules)
    assert result.clarification is None
    assert result.rewrites[0].column == field


@pytest.mark.parametrize("phrase,field,label", [
    ("配送成本", "shipping_cost", "运费"),
    ("买进花了多少", "purchase_amount", "采购金额"),
    ("退回来的钱", "refund_amount", "退款金额"),
])
def test_domain_compounds_bind_to_matching_real_metric(phrase, field, label):
    tables, rules = _schema("operations", {
        "purchase_amount": "采购金额", "shipping_cost": "运费", "refund_amount": "退款金额",
    })
    result = SemanticGraph().normalize("2025年" + phrase, tables, rules)

    assert result.clarification is None
    assert result.normalized_question == "2025年" + label
    assert len(result.rewrites) == 1
    assert result.rewrites[0].column == field


def test_unqualified_spending_does_not_arbitrarily_choose_an_expense():
    tables, rules = _schema("operations", {"purchase_amount": "采购金额", "shipping_cost": "运费", "total_cost": "成本"})
    result = SemanticGraph().normalize("2025年花了多少钱", tables, rules)
    assert result.clarification and not result.changed


def test_unit_cost_never_used_as_order_total_cost():
    tables, rules = _schema("products", {"unit_cost": "单件成本"})
    result = SemanticGraph().normalize("商品本金多少", tables, rules)
    assert result.clarification and not result.changed
    unit = SemanticGraph().normalize("每件商品的本金多少", tables, rules)
    assert unit.normalized_question == "每件商品的单件成本多少"


@pytest.mark.parametrize("question", ["2025年销售额", "按月统计销售额", "成本超过100的商品", "去年毛利率",
    "客户名称", "按地区和渠道分组", "产品名称不包含成本", "本金_raw", "XPVYZ", "PV_2025"])
def test_ordinary_inputs_do_not_build_graph_snapshots(question):
    graph = SemanticGraph()
    tables, rules = _schema()
    result = graph.normalize(question, tables, rules)
    assert result.normalized_question == question
    assert graph.snapshot_count == 0
    assert not needs_normalization(question)


def test_missing_concept_does_not_make_up_a_field():
    tables, rules = _schema(fields={"page_views": "网页浏览量"})
    result = SemanticGraph().normalize("商品本金多少", tables, rules)
    assert result.clarification and not result.changed
    assert not result.ambiguities[0].candidates


def test_aliases_pointing_to_missing_fields_are_ignored():
    tables, rules = _schema(fields={"page_views": "网页浏览量"})
    result = SemanticGraph().normalize("赚了多少钱", tables, rules + (
        AliasRule("sales_orders", "imaginary_profit", ("利润",), "metric"),))
    assert result.clarification and not result.changed


def test_metadata_serializes_without_changing_original_span_offsets():
    tables, rules = _schema()
    question = "2025年商品本金和赚了多少钱，排除日期不明且地区为北美的记录"
    result = SemanticGraph().normalize(question, tables, rules)
    assert result.normalized_question == "2025年商品成本和净利润，排除日期不明且地区为北美的记录"
    assert len(result.rewrites) == 2
    for rewrite in result.rewrites:
        assert question[rewrite.start:rewrite.end] == rewrite.source_text
    assert json.loads(json.dumps(result.to_dict(), ensure_ascii=False))["original_question"] == question


def test_negation_of_principal_equals_cost_is_respected():
    tables, rules = _schema()
    result = SemanticGraph().normalize("这里本金不是成本，2025年本金多少", tables, rules)
    assert result.clarification
    assert result.ambiguities[0].reason.startswith("用户明确排除")


def test_traditional_characters_keep_offsets_and_known_values():
    tables, rules = _schema()
    result = SemanticGraph().normalize("商品賺了多少錢", tables, rules)
    # The project's deliberately limited traditional conversion must never
    # produce a wrong span if it cannot recognize a particular character.
    if result.changed:
        assert result.rewrites[0].source_text in "商品賺了多少錢"
    value = SemanticGraph().normalize("本金科技的銷售額", tables, rules, protected_values=["本金科技"])
    assert not value.changed and not value.ambiguities


def _catalog():
    return MetricCatalog({"version": "unit-test-v1", "metrics": [
        {"id": "revenue", "table": "sales_orders", "column": "revenue", "function": "SUM",
         "label": "销售额", "aliases": ["销售额"], "query_alias": "销售额", "unit": "currency"},
        {"id": "gross_profit", "table": "sales_orders", "column": "gross_profit", "function": "SUM",
         "label": "毛利", "aliases": ["毛利"], "query_alias": "毛利", "unit": "currency"}],
        "derived_metrics": [{"id": "sales_cost", "label": "销售成本", "aliases": ["销售成本", "销售总成本"],
         "expression": {"op": "subtract", "left": {"ref": "revenue"}, "right": {"ref": "gross_profit"}}}]})


def test_configured_derived_cost_uses_only_actual_dependencies():
    tables, rules = _schema(fields={"revenue": "销售额", "gross_profit": "毛利"})
    graph = SemanticGraph(_catalog())
    result = graph.normalize("2025年商品本金多少", tables, rules)
    assert result.normalized_question == "2025年商品销售成本多少"
    assert result.rewrites[0].metric_id == "sales_cost"
    assert result.rewrites[0].source_fields == ("sales_orders.revenue", "sales_orders.gross_profit")
    revenue = graph.normalize("2025年商品卖出多少钱", tables, rules)
    assert revenue.normalized_question == "2025年商品销售额"
    assert revenue.clarification is None


def test_configured_derived_cost_with_missing_dependency_is_unavailable():
    tables, rules = _schema(fields={"revenue": "销售额"})
    result = SemanticGraph(_catalog()).normalize("商品本金多少", tables, rules)
    assert result.clarification and not result.changed


def test_text_primary_key_counts_use_explicit_count_contracts():
    tables = (TableInfo("ledger", (ColumnInfo("ref", "TEXT", False, True),)),)
    rules = (AliasRule("ledger", "ref", ("订单数",), "metric", "COUNT"),)
    result = SemanticGraph().normalize("2025年有多少笔订单", tables, rules)
    assert result.normalized_question == "2025年订单数"
    assert result.rewrites[0].column == "ref"


def test_graph_snapshot_cache_is_bounded_and_schema_sensitive():
    graph = SemanticGraph(max_snapshots=2)
    for i in range(4):
        tables, rules = _schema(f"facts_{i}", {"c": "销售额"})
        result = graph.normalize("卖出多少钱", tables, rules)
        assert result.rewrites[0].table == f"facts_{i}"
    assert graph.snapshot_count == 2


def test_long_inputs_preserved_instead_of_partially_normalized():
    tables, rules = _schema()
    question = "商品本金" + "甲" * 12_000
    result = SemanticGraph().normalize(question, tables, rules)
    assert result.skipped_reason == "semantic_graph_input_budget"
    assert result.normalized_question == question


def test_unknown_constraints_and_literals_survive_metric_translation():
    tables, rules = _schema()
    question = "商品赚了多少钱；只统计区块链结算且排除未签订保密协议的记录"
    result = SemanticGraph().normalize(question, tables, rules)
    assert result.normalized_question.endswith("；只统计区块链结算且排除未签订保密协议的记录")


def test_warm_local_graph_requires_neither_database_nor_api_and_is_fast():
    tables, rules = _schema()
    graph = SemanticGraph()
    graph.normalize("2025年商品本金多少", tables, rules)
    started = time.perf_counter()
    for _ in range(100):
        result = graph.normalize("2025年商品本金多少", tables, rules)
        assert result.changed
    # A generous guard catches accidental I/O or quadratic context expansion,
    # without turning millisecond benchmark noise into a correctness failure.
    assert time.perf_counter() - started < 2.0


@pytest.mark.parametrize("owner", ["unseen_business", "事实表_793", "x_917"])
@pytest.mark.parametrize("phrase,canonical", [
    ("营收", "销售额"), ("营收入", "销售额"), ("营业额", "销售额"),
    ("成交金额", "销售额"), ("赚头", "净利润"), ("获利", "净利润"),
    ("盈利金额", "净利润"), ("卖货赚多少", "净利润"),
    ("赚的钱", "净利润"), ("开销", "成本"), ("花销", "成本"),
    ("花多少钱", "成本"), ("进货钱", "成本"), ("拿货花的钱", "成本"),
    ("卖货成本", "成本"), ("投入成本", "成本"),
    ("销售数量", "销量"), ("售出数量", "销量"),
])
def test_vocabulary_extensions_work_on_unseen_schema_names(owner, phrase, canonical):
    tables, rules = _schema(owner)
    result = SemanticGraph().normalize("2025年" + phrase, tables, rules)
    assert result.normalized_question == "2025年" + canonical
    assert result.clarification is None


@pytest.mark.parametrize("phrase,field", [
    ("物流花多少钱", "shipping_cost"), ("配送花钱多少", "shipping_cost"),
    ("运货花了多少", "shipping_cost"), ("工资发多少钱", "salary_amount"),
    ("发多少工资", "salary_amount"), ("奖金发了多少", "bonus_amount"),
    ("发多少奖金", "bonus_amount"), ("实际收多少钱", "collected_amount"),
    ("到账了多少钱", "collected_amount"), ("运营花多少钱", "expense_amount"),
    ("报销花钱多少", "expense_amount"), ("买了多少件", "purchase_qty"),
])
def test_complete_domain_money_phrases_do_not_leave_unparsed_prefixes(phrase, field):
    tables, rules = _schema("x_014", {"shipping_cost": "运费", "salary_amount": "薪酬总额",
        "bonus_amount": "奖金金额", "collected_amount": "回款金额", "expense_amount": "费用金额",
        "purchase_qty": "采购数量"})
    result = SemanticGraph().normalize("2025年" + phrase, tables, rules)
    assert result.clarification is None
    assert result.normalized_question == "2025年" + next(r.aliases[0] for r in rules if r.column == field)
    assert result.rewrites[0].column == field


def test_sales_units_are_not_confused_with_procurement_or_inventory_units():
    a, ar = _schema("sales_orders", {"quantity": "销量"})
    b, br = _schema("purchase_orders", {"purchase_quantity": "采购数量"})
    c, cr = _schema("inventory_snapshots", {"on_hand_quantity": "库存量"})
    result = SemanticGraph().normalize("2025年卖出去多少件", a + b + c, ar + br + cr)
    assert result.clarification is None
    assert result.rewrites[0].table == "sales_orders"


@pytest.mark.parametrize("question,owner,expected", [
    ("销售本金多少", "sales_orders", "sales_orders.total_cost多少"),
    ("进货本金多少", "purchase_orders", "purchase_orders.total_cost多少"),
    ("贷款本金多少", "loans", "借款本金多少"),
])
def test_principal_mixed_domains_require_explicit_scope(question, owner, expected):
    a, ar = _schema("sales_orders", {"total_cost": "成本"})
    b, br = _schema("purchase_orders", {"total_cost": "成本"})
    c, cr = _schema("loans", {"loan_amount": "借款本金"})
    graph = SemanticGraph()
    result = graph.normalize(question, a + b + c, ar + br + cr)
    assert result.clarification is None
    assert result.rewrites[0].table == owner
    assert result.normalized_question == expected
    ambiguous = graph.normalize("本金多少", a + b + c, ar + br + cr)
    assert ambiguous.clarification and not ambiguous.changed


def test_unannotated_cost_foreign_identifier_is_not_a_currency_measure():
    tables, _ = _schema("sales_orders", {"cost_id": "placeholder"})
    result = SemanticGraph().normalize("商品本金多少", tables)
    assert result.clarification and not result.changed


def test_real_unannotated_principal_can_use_native_identifier():
    tables, _ = _schema("loans", {"loan_principal": "placeholder"})
    result = SemanticGraph().normalize("贷款本金多少", tables)
    assert result.normalized_question == "loan_principal多少"
    assert result.rewrites[0].column == "loan_principal"


@pytest.mark.parametrize("phrase", ["成本是多少", "利润是多少"])
def test_missing_plain_numeric_business_concepts_require_a_field(phrase):
    tables, rules = _schema("traffic", {"page_views": "网页浏览量"})
    result = SemanticGraph().normalize(phrase, tables, rules)
    assert result.clarification and not result.changed


@pytest.mark.parametrize("phrase", ["如何降低成本", "利润是什么", "解释成本含义"])
def test_plain_document_definitions_do_not_trigger_numeric_graph(phrase):
    assert not needs_normalization(phrase)


def test_same_physical_field_with_two_configured_metric_scopes_is_ambiguous():
    payload = _catalog().payload
    duplicate = dict(payload["metrics"][0])
    duplicate.update(id="paid_revenue", label="已付款销售额", aliases=["已付款销售额"],
                     query_alias="已付款销售额", filters=[{"column": "payment_status", "operator": "=", "value": "已付款"}])
    payload["metrics"].append(duplicate)
    tables, rules = _schema(fields={"revenue": "销售额", "gross_profit": "毛利"})
    result = SemanticGraph(MetricCatalog(payload)).normalize("卖出多少钱", tables, rules)
    assert result.clarification and not result.changed


@pytest.mark.parametrize("native", ["salary_amount", "w_041", "工资"])
@pytest.mark.parametrize("phrase", ["工资发了多少钱", "发了多少工资", "发出去的工资"])
def test_short_salary_alias_inside_complete_phrase_is_evidence_not_a_literal(native, phrase):
    tables, rules = _schema("unseen_company", {native: "工资"})
    result = SemanticGraph().normalize("2025年" + phrase, tables, rules)
    assert result.normalized_question == "2025年工资"
    assert result.clarification is None
    assert result.rewrites[0].column == native
    protected = SemanticGraph().normalize("2025年" + phrase, tables, rules, protected_values=[phrase])
    assert protected.normalized_question == "2025年" + phrase
    assert not protected.rewrites and not protected.ambiguities


@pytest.mark.parametrize("phrase", ["运货花的钱", "回款到账金额", "进货花了多少钱"])
def test_noun_payment_phrases_resolve_without_an_unparsed_domain_prefix(phrase):
    fields = {"shipping_cost": "运费", "collected_amount": "回款金额", "purchase_amount": "采购金额"}
    tables, rules = _schema("operations", fields)
    result = SemanticGraph().normalize("2025年" + phrase, tables, rules)
    expected = {"运货花的钱": "运费", "回款到账金额": "回款金额", "进货花了多少钱": "采购金额"}[phrase]
    assert result.normalized_question == "2025年" + expected
    assert result.clarification is None


def test_procurement_payment_can_ground_declared_single_cost_without_ledger():
    tables, rules = _schema("x_01", {"cost_v31": "销售成本"})
    result = SemanticGraph().normalize("2025年进货花了多少钱", tables, rules)
    assert result.normalized_question == "2025年销售成本"
    assert result.rewrites[0].column == "cost_v31"


@pytest.mark.parametrize("phrase", ["本金", "本钱"])
def test_anonymous_schema_declared_business_concepts_ground_principal_as_cost(phrase):
    tables, rules = _schema("opaque_r31", {"n_041": "销售额", "n_023": "销售成本", "n_987": "毛利"})
    result = SemanticGraph().normalize("2025年" + phrase, tables, rules)
    assert result.normalized_question == "2025年销售成本"
    assert result.rewrites[0].column == "n_023"


@pytest.mark.parametrize("phrase", ["工资发了多少钱", "发了多少工资", "发出去的工资"])
@pytest.mark.parametrize("whole_native,component_native", [("salary_amount", "base_salary"), ("n_781", "n_128")])
def test_explicit_salary_total_is_distinct_from_a_basic_salary_component(phrase, whole_native, component_native):
    a, ar = _schema("fact_p41", {whole_native: "薪酬总额"})
    b, br = _schema("fact_e09", {component_native: "基本工资"})
    result = SemanticGraph().normalize("2025年" + phrase, a + b, ar + br)
    assert result.clarification is None
    assert result.normalized_question == "2025年薪酬总额"
    assert result.rewrites[0].column == whole_native
    # An actual component request stays a component rather than being promoted
    # to a salary total, even on completely anonymized native names.
    basic = SemanticGraph().normalize("2025年底薪", a + b, ar + br)
    assert basic.clarification is None
    assert basic.rewrites[0].column == component_native


def test_two_salary_totals_are_not_resolved_by_arbitrary_score_ties():
    a, ar = _schema("facts_a", {"m01": "工资总额"})
    b, br = _schema("facts_b", {"m02": "薪酬总额"})
    c, cr = _schema("facts_c", {"m03": "基本工资"})
    result = SemanticGraph().normalize("发了多少工资", a + b + c, ar + br + cr)
    assert result.clarification and not result.changed


@pytest.mark.parametrize("principal_alias", ["本金", "借款本金"])
def test_explicit_business_unit_context_wins_over_unqualified_financial_alias(principal_alias):
    a, ar = _schema("loans", {"loan_principal": principal_alias})
    b, br = _schema("products", {"unit_cost": "单位成本"})
    graph = SemanticGraph()
    result = graph.normalize("每件商品本金", a + b, ar + br)
    assert result.clarification is None
    assert result.normalized_question == "每件商品单位成本"
    assert result.rewrites[0].column == "unit_cost"
    for explicit in ("每件商品loans.loan_principal", "每件商品loans.本金", "每件商品'本金'"):
        protected = graph.normalize(explicit, a + b, ar + br)
        assert protected.normalized_question == explicit
        assert not protected.rewrites
    value = graph.normalize("每件商品本金", a + b, ar + br, protected_values=["本金"])
    assert not value.changed and not value.rewrites


@pytest.mark.parametrize("native,label", [("profit_rate", "利润率"), ("gross_profit_margin", "毛利率"), ("unit_price", "单价"), ("n_099", "未知数值")])
def test_percentage_price_and_opaque_numeric_fields_do_not_become_profit(native, label):
    tables, rules = _schema("unknown_domain", {native: label})
    result = SemanticGraph().normalize("赚了多少钱", tables, rules)
    assert result.clarification and not result.changed


@pytest.mark.parametrize("requested", ["净利润是多少", "纯利润是多少", "净利", "税后利润是多少", "利润净额是多少"])
def test_missing_net_profit_never_normalizes_a_short_profit_span_to_gross(requested):
    tables, rules = _schema("facts", {"gross_profit": "毛利"})
    result = SemanticGraph().normalize("2025年华东" + requested, tables, rules)
    assert result.clarification and not result.changed
    assert result.ambiguities[0].concept == "net_profit"
    assert not result.rewrites


@pytest.mark.parametrize("requested,field", [
    ("净利润是多少", "n_net"), ("纯利润是多少", "n_net"), ("税后利润是多少", "n_net"),
    ("毛利润是多少", "n_gross"), ("毛利有多少", "n_gross"), ("税前利润是多少", "n_pretax"),
])
def test_typed_profit_definitions_choose_the_declared_matching_scope(requested, field):
    tables, rules = _schema("unseen_r29", {"n_net": "净利润", "n_gross": "毛利", "n_pretax": "税前利润"})
    result = SemanticGraph().normalize("2025年" + requested, tables, rules)
    assert result.clarification is None
    # A complete, already annotated phrase can remain unchanged. If edited,
    # its real field must be the matching definition, not the easiest metric.
    assert all(rewrite.column == field for rewrite in result.rewrites)
    assert "毛利" not in result.normalized_question if field != "n_gross" else True


@pytest.mark.parametrize("owner", ["marketing_campaigns", "x_031", "f_817"])
def test_generic_revenue_excludes_targets_attribution_per_item_and_percentages(owner):
    tables, rules = _schema(owner, {"n_actual": "销售额", "n_target": "目标销售额",
        "n_attributed": "归因销售额", "n_per_unit": "平均每件收入", "revenue_ratio": "收入比例"})
    result = SemanticGraph().normalize("2025年营收", tables, rules)
    assert result.clarification is None
    assert result.normalized_question == "2025年销售额"
    assert result.rewrites[0].column == "n_actual"


@pytest.mark.parametrize("field,label", [("target_revenue", "目标销售额"), ("attributed_revenue", "归因销售额"),
                                        ("revenue_per_unit", "平均每件收入"), ("revenue_ratio", "收入比例")])
def test_nonactual_income_alone_cannot_answer_generic_realized_revenue(field, label):
    tables, rules = _schema("facts", {field: label})
    result = SemanticGraph().normalize("2025年卖出多少钱", tables, rules)
    assert result.clarification and not result.changed


@pytest.mark.parametrize("requested,column", [("目标营收", "n_goal"), ("预算收入", "n_goal"), ("归因营收", "n_attr")])
def test_explicit_revenue_scope_is_retained(requested, column):
    tables, rules = _schema("facts", {"n_actual": "销售额", "n_goal": "目标销售额", "n_attr": "归因销售额"})
    result = SemanticGraph().normalize("2025年" + requested, tables, rules)
    assert result.clarification is None
    assert result.rewrites[0].column == column


def test_forecast_profit_is_not_money_already_earned():
    tables, rules = _schema("facts", {"profit_forecast": "预计利润"})
    result = SemanticGraph().normalize("赚了多少钱", tables, rules)
    assert result.clarification and not result.changed


@pytest.mark.parametrize("phrase", [
    "淨利润是多少", "淨利潤是多少", "純利润是多少", "純利潤是多少",
    "稅后利润是多少", "稅後利潤是多少", "净利潤是多少",
])
def test_traditional_profit_modifiers_never_fall_back_to_gross_profit(phrase):
    tables, rules = _schema("facts", {"gross_profit": "毛利"})
    question = "2025年華東" + phrase
    assert needs_normalization(question)
    result = SemanticGraph().normalize(question, tables, rules)
    assert result.clarification and not result.changed
    assert result.ambiguities[0].concept == "net_profit"
    assert result.ambiguities[0].source_text == phrase
    assert not result.rewrites


@pytest.mark.parametrize("phrase", [
    "貸款本金多少", "貸款本錢多少", "投資本金多少", "債券本金多少",
    "商品貸款本金多少", "商品貸款本钱多少",
])
def test_traditional_financial_context_cannot_be_answered_as_business_cost(phrase):
    tables, rules = _schema()
    question = "2025年" + phrase
    result = SemanticGraph().normalize(question, tables, rules)
    assert result.clarification and not result.changed
    assert not result.rewrites
    assert "金融" in result.ambiguities[0].reason


@pytest.mark.parametrize("phrase", ["貸款本金多少", "投資本金多少", "債券本金多少"])
def test_traditional_financial_context_selects_real_principal_over_cost(phrase):
    a, ar = _schema("f_loan", {"loan_principal": "借款本金"})
    b, br = _schema("f_sales", {"total_cost": "成本"})
    question = "2025年" + phrase
    result = SemanticGraph().normalize(question, a + b, ar + br)
    assert result.clarification is None
    assert len(result.rewrites) == 1
    assert result.rewrites[0].table == "f_loan"
    assert result.rewrites[0].column == "loan_principal"
    assert question[result.rewrites[0].start:result.rewrites[0].end] == result.rewrites[0].source_text


@pytest.mark.parametrize("question,column", [
    ("2025年員工基本工資花了多少錢", "n_base"),
    ("2025年基本工資發了多少錢", "n_base"),
    ("2025年工資發了多少錢", "n_total"),
    ("2025年發出去的工資", "n_total"),
])
def test_traditional_salary_component_and_total_keep_their_declared_scopes(question, column):
    tables, rules = _schema("f_019", {"n_base": "基本工资", "n_total": "薪酬总额"})
    result = SemanticGraph().normalize(question, tables, rules)
    assert result.clarification is None
    assert result.rewrites and all(rewrite.column == column for rewrite in result.rewrites)
    assert result.original_question == question


@pytest.mark.parametrize("value", ["貸款本金", "淨利潤", "基本工資發了多少錢"])
def test_traditional_actual_values_remain_byte_for_byte(value):
    tables, rules = _schema()
    question = "2025年产品名称为" + value + "的销售额"
    result = SemanticGraph().normalize(question, tables, rules, protected_values=[value])
    assert result.normalized_question == question
    assert not result.rewrites and not result.ambiguities
    quoted = "2025年产品名称为'" + value + "'的销售额"
    protected = SemanticGraph().normalize(quoted, tables, rules)
    assert protected.normalized_question == quoted
    assert not protected.rewrites and not protected.ambiguities


@pytest.mark.parametrize("marker", ["新问题", "重新查询", "换个主题", "换一个主题", "换个问题", "新問題", "換一個主題"])
def test_explicit_new_topic_discards_old_sales_or_finance_semantic_hint(marker):
    sales, sales_rules = _schema("sales_orders", {"total_cost": "成本"})
    loans, loan_rules = _schema("loans", {"loan_principal": "借款本金"})
    graph = SemanticGraph()
    for hint in ({"table": "sales_orders", "metric_column": "total_cost", "question": "商品成本"},
                 {"table": "loans", "metric_column": "loan_principal", "question": "贷款本金"}):
        result = graph.normalize(marker + "，本金多少", sales + loans, sales_rules + loan_rules, hint)
        assert result.clarification and not result.changed
        assert len(result.ambiguities[0].candidates) == 2
        assert not result.rewrites
    # A normal dependent fragment can still use the trusted previous domain.
    followup = graph.normalize("那本金呢", sales + loans, sales_rules + loan_rules,
        {"table": "sales_orders", "metric_column": "total_cost", "question": "商品成本"})
    assert followup.clarification is None
    assert followup.rewrites[0].column == "total_cost"
