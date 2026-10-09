"""End-to-end checks for normalization, routing, memory and model grounding."""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.semantics import MetricCatalog
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore
from backend.unified_routing import source_hint, term_definition_request


def _engine(tmp_path, *, provider=None, anonymous=False, derived=False):
    path = tmp_path / "semantic.sqlite"
    table = "facts_f83" if anonymous else "sales_orders"
    fields = {"order_id": "key_b02", "order_date": "date_t01", "region": "area_r11",
              "product_name": "name_k91", "sales_amount": "n_731", "total_cost": "n_582",
              "profit": "n_990"} if anonymous else {name: name for name in (
                  "order_id", "order_date", "region", "product_name", "sales_amount", "total_cost", "profit")}
    with sqlite3.connect(path) as db:
        db.execute(f'CREATE TABLE "{table}" ("{fields["order_id"]}" INTEGER PRIMARY KEY, '
                   f'"{fields["order_date"]}" TEXT, "{fields["region"]}" TEXT, '
                   f'"{fields["product_name"]}" TEXT, "{fields["sales_amount"]}" REAL, '
                   f'"{fields["total_cost"]}" REAL, "{fields["profit"]}" REAL)')
        db.executemany(f'INSERT INTO "{table}" VALUES (?,?,?,?,?,?,?)', [
            (1, "2025-01-02", "华东", "本金科技", 1000, 600, 400),
            (2, "2025-05-11", "华东", "净利润旗舰版", 400, 200, 200),
            (3, "2025-08-10", "华南", "商品B", 800, 300, 500),
            (4, "2024-10-03", "华东", "商品A", 100, 80, 20),
        ])
    rules = [{"table": table, "column": fields[name], "aliases": aliases, "role": role,
              **({"metric_function": "SUM"} if role == "metric" else {})}
             for name, aliases, role in (
                 ("order_date", ["订单日期", "日期"], "dimension"),
                 ("region", ["地区"], "dimension"),
                 ("product_name", ["产品名称"], "dimension"),
                 ("sales_amount", ["销售额"], "metric"),
                 ("total_cost", ["成本"], "metric"),
                 ("profit", ["利润"], "metric"))]
    if derived:
        rules = [rule for rule in rules if rule["column"] != fields["total_cost"]]
        with sqlite3.connect(path) as db:
            # A non-cost native name prevents this fixture's independent
            # amount from claiming a second cost semantic path.
            db.execute(f'ALTER TABLE "{table}" RENAME COLUMN "{fields["total_cost"]}" TO misc_value')
        catalog = {"version": "semantic-integration-v1", "metrics": [
            {"id": "revenue", "table": table, "column": fields["sales_amount"], "function": "SUM",
             "label": "销售额", "aliases": ["销售额"], "query_alias": "销售额", "unit": "currency"},
            {"id": "profit", "table": table, "column": fields["profit"], "function": "SUM",
             "label": "利润", "aliases": ["利润"], "query_alias": "利润", "unit": "currency"}],
            "derived_metrics": [{"id": "sales_cost", "label": "销售成本", "aliases": ["销售成本"],
                "expression": {"op": "subtract", "left": {"ref": "revenue"}, "right": {"ref": "profit"}}}]}
        catalog_path = tmp_path / "catalog.json"
        catalog_path.write_text(json.dumps(catalog, ensure_ascii=False), encoding="utf-8")
    else:
        catalog_path = tmp_path / "intentionally-no-catalog.json"
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return Nl2SqlEngine(path, aliases_path=aliases, metric_catalog_path=catalog_path,
                       model_plan_provider=provider), fields, table


def _payload(*, metric="profit", label="利润"):
    return {"version": 1, "table": "sales_orders",
        "metric": {"table": "sales_orders", "column": metric, "function": "SUM", "label": label},
        "dimensions": [], "filters": [{"table": "sales_orders", "column": "region", "operator": "=", "value": "华东"}],
        "analysis_mode": "aggregate", "limit": 20, "confidence": 0.98,
        "rewritten_question": "华东利润"}


def test_model_and_grounding_share_canonical_question(tmp_path, monkeypatch):
    monkeypatch.setenv("ICT8_FAST_SQL", "0")
    questions = []
    def provider(question, tables):
        questions.append(question)
        return _payload()
    engine, _, _ = _engine(tmp_path, provider=provider)
    result = engine.answer("华东赚了多少钱")
    assert questions == ["华东利润"]
    assert result.status == "ok"
    assert result.plan["planner_source"] == "model_validated"
    assert next(iter(result.rows[0].values())) == 620
    assert result.plan["metric_column"] == "profit"
    assert result.question == "华东赚了多少钱"
    assert result.plan["semantic_audit"]["language_normalization"]["normalized_question"] == "华东利润"


def test_model_cannot_swap_normalized_profit_for_revenue(tmp_path, monkeypatch):
    monkeypatch.setenv("ICT8_FAST_SQL", "0")
    engine, _, _ = _engine(tmp_path, provider=lambda q, t: _payload(metric="sales_amount", label="销售额"))
    result = engine.answer("华东赚了多少钱")
    assert result.status == "ok"
    assert next(iter(result.rows[0].values())) == 620
    assert result.plan["metric_column"] == "profit"
    assert result.plan["planner_source"] == "rules_fallback"
    assert "profit" in result.sql and "SUM(\"sales_amount\")" not in result.sql


@pytest.mark.parametrize("question", ["2025年贷款本金多少", "2025年理财本金多少", "2025年借款本金多少"])
def test_financial_ambiguity_blocks_execution_and_model(tmp_path, question):
    def forbidden(*args):
        raise AssertionError("financial ambiguity must not reach model planning")
    engine, _, _ = _engine(tmp_path, provider=forbidden)
    result = engine.answer(question)
    assert result.status == "clarification"
    assert result.clarification_code == "ambiguous_business_expression"
    assert result.sql is None and result.rows == ()
    assert result.provenance["execution_status"] == "not_executed"


@pytest.mark.parametrize("question", ["本金是多少", "成本是多少", "利润是多少"])
def test_unscoped_business_terms_request_clarification_before_model_or_sql(tmp_path, question):
    def forbidden(*args):
        raise AssertionError("an unscoped business term must not reach model planning")
    engine, _, _ = _engine(tmp_path, provider=forbidden)
    result = engine.answer(question)
    assert result.status == "clarification"
    assert result.clarification_code == "ambiguous_business_expression"
    assert result.sql is None and result.rows == ()


def test_financial_ambiguity_preserves_previous_successful_history(tmp_path):
    engine, _, _ = _engine(tmp_path)
    store = ConversationStore()
    agent = OmniAgent(engine, KnowledgeStore(tmp_path / "knowledge"), store)
    good = agent.query("2025年华东销售额", session_id="financial-control")
    assert good["status"] == "ok"
    before = store.context("financial-control")
    rejected = agent.query("2025年贷款本金多少", session_id="financial-control")
    assert rejected["status"] == "clarification"
    assert rejected["result"]["sql"] is None
    assert store.context("financial-control") == before
    followup = agent.query("那华南呢", session_id="financial-control")
    assert followup["status"] == "ok"
    assert followup["result"]["rows"][0]["销售额"] == 800


def test_original_question_survives_ui_audit_and_conversation_memory(tmp_path):
    engine, _, _ = _engine(tmp_path)
    store = ConversationStore()
    agent = OmniAgent(engine, KnowledgeStore(tmp_path / "knowledge"), store)
    question = "2025年华东本钱"
    result = agent.query(question, session_id="colloquial-memory")
    assert result["status"] == "ok"
    assert result["question"] == question
    assert result["result"]["question"] == question
    assert result["language_normalization"]["original_question"] == question
    assert next(iter(result["result"]["rows"][0].values())) == 800
    assert store.context("colloquial-memory")[-1].question == question
    followup = agent.query("那华南呢", session_id="colloquial-memory")
    assert followup["status"] == "ok"
    assert next(iter(followup["result"]["rows"][0].values())) == 300


def test_anonymous_schema_uses_actual_aliases_instead_of_native_names(tmp_path):
    engine, fields, _ = _engine(tmp_path, anonymous=True)
    result = engine.answer("2025年华东赚了多少钱")
    assert result.status == "ok"
    assert next(iter(result.rows[0].values())) == 600
    assert result.plan["metric_column"] == fields["profit"]
    audit = result.plan["semantic_audit"]["language_normalization"]
    assert audit["rewrites"][0]["column"] == fields["profit"]
    assert fields["profit"] in result.sql


@pytest.mark.parametrize("question,amount", [
    ("2025年本金科技的销售额", 1000),
    ("2025年产品名称为'本金科技'的销售额", 1000),
    ("2025年赚了多少钱的销售额", 800),
])
def test_true_values_retain_their_value_meaning(tmp_path, question, amount):
    engine, _, _ = _engine(tmp_path)
    if "赚了多少钱" in question:
        with sqlite3.connect(engine.database_path) as db:
            db.execute("UPDATE sales_orders SET product_name='赚了多少钱' WHERE order_id=3")
    normalized = engine.normalize_question(question)
    assert normalized.normalized_question == question
    assert not normalized.ambiguities
    result = engine.answer(question)
    assert result.status == "ok"
    assert result.rows[0]["销售额"] == amount


def test_unknown_constraints_survive_and_block_rules_execution(tmp_path):
    engine, _, _ = _engine(tmp_path)
    result = engine.answer("2025年华东赚了多少钱，仅包含取得区块链认证的订单")
    assert result.status == "clarification"
    assert result.sql is None
    assert "区块链认证" in result.rewritten_question


@pytest.mark.parametrize("question,image", [
    ("文档中的本金是什么意思", None),
    ("这张图片中的本金是什么", [{"id": "opaque-image-id"}]),
])
def test_document_and_image_routes_skip_sql_normalization(tmp_path, monkeypatch, question, image):
    engine, _, _ = _engine(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("document/image input must bypass SQL normalization")
    monkeypatch.setattr(engine, "normalize_question", forbidden)
    agent = OmniAgent(engine, KnowledgeStore(tmp_path / "knowledge"), ConversationStore())
    if image:
        agent.client = object()
    seen = []
    def query_turn(actual, **kwargs):
        seen.append((actual, kwargs.get("image_attachments")))
        return {"status": "ok", "route": "document", "question": actual,
                "effective_question": actual, "result": {"answer": "本金术语定义"}, "trace": []}
    monkeypatch.setattr(agent, "_query_turn", query_turn)
    result = agent.query(question, image_attachments=image)
    assert result["status"] == "ok"
    assert seen == [(question, image)]
    assert "language_normalization" not in result


@pytest.mark.parametrize("question,expected", [
    ("2025年华东销售本金", {"销售成本": 800}),
    ("2025年华东销售额和本金", {"销售额": 1400, "销售成本": 800}),
])
def test_configured_derived_cost_and_multi_metric_result(tmp_path, question, expected):
    engine, _, _ = _engine(tmp_path, derived=True)
    result = engine.answer(question)
    assert result.status == "ok"
    assert dict(result.rows[0]) == expected
    audit = result.plan["semantic_audit"]["language_normalization"]
    assert audit["rewrites"][0]["metric_id"] == "sales_cost"
    assert set(audit["rewrites"][0]["source_fields"]) == {"sales_orders.sales_amount", "sales_orders.profit"}


def test_verified_catalog_formula_skips_external_model_without_losing_source_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("ICT8_FAST_SQL", "1")
    calls = []
    engine, _, _ = _engine(tmp_path, derived=True)
    engine.model_plan_provider = SimpleNamespace(propose=lambda *args: calls.append(args))
    result = engine.answer("2025年华东销售成本")
    assert result.status == "ok"
    assert result.plan["planner_audit"]["model_called"] is False
    assert result.plan["grain_audit"]["formulas"][0]["metric_id"] == "sales_cost"
    assert calls == []


def test_external_schema_alias_configuration_does_not_inherit_demo_catalog(tmp_path, monkeypatch):
    monkeypatch.delenv("ICT8_METRIC_CATALOG", raising=False)
    fixture_engine, _, _ = _engine(tmp_path)
    with sqlite3.connect(fixture_engine.database_path) as db:
        db.execute("ALTER TABLE sales_orders RENAME COLUMN profit TO gross_profit")
    aliases_path = tmp_path / "aliases.json"
    rules = json.loads(aliases_path.read_text(encoding="utf-8"))
    for rule in rules:
        if rule["column"] == "profit":
            rule.update(column="gross_profit", aliases=["净收益"])
    aliases_path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    engine = Nl2SqlEngine(fixture_engine.database_path, aliases_path=aliases_path)
    assert engine.semantic_graph.metric_catalog is None
    result = engine.normalize_question("2025年华东赚了多少钱")
    assert result.normalized_question == "2025年华东净收益"
    assert result.rewrites[0].metric_id is None
    assert engine.answer("2025年华东毛利").status == "clarification"


def test_schema_mutation_invalidates_normalization_and_graph_snapshot_cache(tmp_path):
    engine, _, _ = _engine(tmp_path)
    before = engine.normalize_question("2025年华东本钱")
    assert before.changed
    with sqlite3.connect(engine.database_path) as db:
        db.execute("ALTER TABLE sales_orders RENAME COLUMN total_cost TO opaque_n117")
    after = engine.normalize_question("2025年华东本钱")
    assert after.clarification and not after.changed
    assert engine.semantic_graph.snapshot_count == 2


def test_value_mutation_invalidates_previously_safe_colloquial_normalization(tmp_path):
    engine, _, _ = _engine(tmp_path)
    before = engine.normalize_question("2025年华东赚了多少钱")
    assert before.normalized_question == "2025年华东利润"
    with sqlite3.connect(engine.database_path) as db:
        db.execute("UPDATE sales_orders SET product_name='赚了多少钱' WHERE order_id=1")
    after = engine.normalize_question("2025年华东赚了多少钱")
    assert after.normalized_question == "2025年华东赚了多少钱"
    assert not after.changed


def test_ordinary_legacy_question_normalization_does_not_open_database(tmp_path, monkeypatch):
    engine, _, _ = _engine(tmp_path)
    def forbidden():
        raise AssertionError("legacy lexical fast path must not open the database")
    monkeypatch.setattr(engine, "_connect", forbidden)
    for _ in range(20):
        assert engine.normalize_question("2025年各地区销售额") is None


def test_engine_business_unit_principal_never_executes_financial_principal(tmp_path):
    path = tmp_path / "mixed-units.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE products (id INTEGER PRIMARY KEY, unit_cost REAL);"
                         "CREATE TABLE loans (id INTEGER PRIMARY KEY, principal_amount REAL);"
                         "INSERT INTO products VALUES (1, 9), (2, 11);"
                         "INSERT INTO loans VALUES (1, 200000);")
    aliases_path = tmp_path / "unit-aliases.json"
    aliases_path.write_text(json.dumps([
        {"table": "products", "column": "unit_cost", "aliases": ["单位成本"], "role": "metric", "metric_function": "AVG"},
        {"table": "loans", "column": "principal_amount", "aliases": ["本金"], "role": "metric", "metric_function": "SUM"},
    ], ensure_ascii=False), encoding="utf-8")
    engine = Nl2SqlEngine(path, aliases_path=aliases_path)
    normalized = engine.normalize_question("每件商品本金")
    assert normalized.rewrites[0].table == "products"
    assert normalized.rewrites[0].column == "unit_cost"
    result = engine.answer("每件商品本金")
    # The existing planner may ask to clarify an unsupported listing grammar;
    # it must never use an unrelated loan ledger to provide a numeric answer.
    if result.status == "ok":
        assert result.plan["metric_column"] == "unit_cost"
        assert "loans" not in result.sql
        assert next(iter(result.rows[0].values())) == 10
    else:
        assert result.sql is None and result.rows == ()


@pytest.mark.parametrize("question", ["2025年华东净利润是多少", "2025年华东纯利润是多少", "2025年华东税后利润是多少"])
def test_missing_exact_profit_definition_never_executes_a_different_profit(tmp_path, question):
    engine, _, _ = _engine(tmp_path, derived=True)
    # The fixture declares ordinary profit, not a tax/net-profit contract.
    # A schema column that looks convenient does not prove a net definition.
    result = engine.answer(question)
    assert result.status == "clarification"
    assert result.sql is None and result.rows == ()
    assert result.clarification_code == "ambiguous_business_expression"
    assert result.provenance["execution_status"] == "not_executed"


def test_catalog_semantics_change_invalidates_normalization_without_database_mutation(tmp_path):
    engine, _, _ = _engine(tmp_path, derived=True)
    question = "2025年华东净利润是多少"
    before_revision = engine._source_revision()
    before = engine.normalize_question(question)
    assert before.clarification and not before.changed
    previous_digest = engine.semantic_graph.metric_catalog.digest
    payload = json.loads(json.dumps(engine.semantic_graph.metric_catalog.payload))
    profit = next(metric for metric in payload["metrics"] if metric["id"] == "profit")
    profit.update(label="净利润", aliases=["净利润"], query_alias="净利润")
    engine.semantic_graph.metric_catalog = MetricCatalog(payload)
    assert engine.semantic_graph.metric_catalog.digest != previous_digest
    assert engine._source_revision() == before_revision
    after = engine.normalize_question(question)
    assert after is not before
    assert after.clarification is None
    assert after.rewrites[0].column == "profit"
    assert after.rewrites[0].concept == "net_profit"
    assert after.rewrites[0].metric_id == "profit"
    assert engine.normalize_question(question) is after
    assert engine.semantic_graph.snapshot_count == 2


@pytest.mark.parametrize("question", ["销售额是什么", "销售额是什么意思", "销售额是啥"])
def test_engine_cannot_execute_unscoped_aggregates_for_pure_definitions(tmp_path, question):
    engine, _, _ = _engine(tmp_path)
    result = engine.answer(question)
    assert result.status == "clarification"
    assert result.sql is None and result.rows == ()
    assert result.plan["clarification"]


@pytest.mark.parametrize("ending", ["是什么", "是谁", "是什么？", "是谁？"])
def test_ranked_entity_interrogative_only_executes_with_group_and_top_n(tmp_path, ending):
    engine, _, _ = _engine(tmp_path)
    question = "2025年销售额最高的地区" + ending
    result = engine.answer(question)
    assert result.status == "ok", result.to_dict()
    assert result.plan["dimensions"] == ["region"]
    assert result.plan["top_n"] == 1
    assert result.plan["order_desc"] is True
    assert "GROUP BY" in result.sql and "ORDER BY" in result.sql
    assert "2025-01-01" in result.parameters and "2026-01-01" in result.parameters
    with sqlite3.connect(engine.database_path) as connection:
        gold = connection.execute(
            "SELECT region,SUM(sales_amount) FROM sales_orders WHERE order_date>=? AND order_date<? "
            "GROUP BY region ORDER BY SUM(sales_amount) DESC LIMIT 1",
            ("2025-01-01", "2026-01-01")).fetchone()
    assert result.rows[0]["region"] == gold[0] == "华东"
    assert result.rows[0]["销售额"] == gold[1] == 1400


def _web_definition_agent(tmp_path):
    initial, _, _ = _engine(tmp_path)
    with sqlite3.connect(initial.database_path) as connection:
        connection.executescript(
            "CREATE TABLE web_sessions (id INTEGER PRIMARY KEY, session_date TEXT, device_type TEXT, page_views INTEGER);"
            "INSERT INTO web_sessions VALUES (1,'2025-01-01','手机',50),(2,'2025-01-02','电脑',30),"
            "(3,'2024-01-01','手机',999);")
    aliases_path = tmp_path / "aliases.json"
    rules = json.loads(aliases_path.read_text(encoding="utf-8"))
    rules.extend([
        {"table": "web_sessions", "column": "session_date", "aliases": ["会话日期"], "role": "dimension"},
        {"table": "web_sessions", "column": "device_type", "aliases": ["设备类型"], "role": "dimension"},
        {"table": "web_sessions", "column": "page_views", "aliases": ["网页浏览量"], "role": "metric", "metric_function": "SUM"},
    ])
    aliases_path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    engine = Nl2SqlEngine(initial.database_path, aliases_path=aliases_path,
                         metric_catalog_path=tmp_path / "intentionally-no-catalog.json")
    knowledge = KnowledgeStore(tmp_path / "knowledge")
    knowledge.ingest(
        "2025年网页浏览量指标定义和统计口径：每次打开网页计一次浏览，按设备类型汇总。计算公式为浏览次数之和。".encode(),
        document_id="web-metric-guide", title="网页浏览量指标定义与统计口径", modality="txt", filename="web-metrics.txt")
    return OmniAgent(engine, knowledge, ConversationStore())


@pytest.mark.parametrize("question", [
    "2025年各设备类型网页浏览量的指标定义", "2025年各设备类型网页浏览量的统计口径",
    "2025年各设备类型网页浏览量的指标定义是什么", "2025年各设备类型网页浏览量的统计口径是什么",
])
@pytest.mark.parametrize("with_history", [False, True])
def test_versioned_metric_definitions_use_real_documents_and_bypass_sql(tmp_path, monkeypatch, question, with_history):
    agent = _web_definition_agent(tmp_path)
    if with_history:
        first = agent.query("2025年各设备类型网页浏览量", session_id="web-definition")
        assert first["status"] == "ok"
    assert term_definition_request(question)
    assert source_hint(question, agent.engine) == "document"
    assert agent.basic_plan(question, ())["route"] == "document"
    def forbidden(*args, **kwargs):
        raise AssertionError("versioned metric definition must not execute SQL")
    monkeypatch.setattr(agent.engine, "answer", forbidden)
    result = agent.query(question, session_id="web-definition")
    assert result["route"] == "document", result
    assert result["status"] == "ok", result
    assert result["effective_question"] == question
    assert not result["result"].get("sql")
    assert result["result"].get("citations")
    assert {citation["metadata"]["document_id"] for citation in result["result"]["citations"]} == {"web-metric-guide"}


@pytest.mark.parametrize("question", [
    "先统计2025年各设备类型网页浏览量，再查计算公式",
    "统计2025年各设备类型网页浏览量，同时查询统计口径",
    "先汇总2025年各设备类型网页浏览量，然后查询指标定义",
])
def test_explicit_statistic_then_definition_request_retains_fusion_intent(tmp_path, question):
    agent = _web_definition_agent(tmp_path)
    assert not term_definition_request(question)
    assert source_hint(question, agent.engine) == "fusion"
    # With no model planner, the cross-source request must ask to retry rather
    # than silently dropping either the numerical statistics or source reading.
    plan = agent.basic_plan(question, ())
    assert plan["route"] == "clarify"
    assert "联合查询" in plan["clarification"]
    assert plan["effective_question"] == question


@pytest.mark.parametrize("question", ["2025年華東淨利润是多少", "2025年華東純利潤是多少", "2025年華東稅後利潤是多少"])
def test_traditional_net_profit_definition_never_executes_generic_profit(tmp_path, question):
    engine, _, _ = _engine(tmp_path, derived=True)
    result = engine.answer(question)
    assert result.status == "clarification"
    assert result.sql is None and result.rows == ()
    assert result.clarification_code == "ambiguous_business_expression"
