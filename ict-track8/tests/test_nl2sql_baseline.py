from __future__ import annotations

import sqlite3
import json

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.nl2sql.security import SqlSafetyError, execute_read_only, validate_read_only_sql


@pytest.fixture()
def engine(tmp_path):
    return Nl2SqlEngine(initialize_database(tmp_path / "demo.sqlite"))


def test_schema_introspection_is_real(engine):
    payload = engine.schema()
    assert payload["source"] == "sqlite_read_only"
    tables = {item["name"]: item for item in payload["tables"]}
    assert set(tables) == {"customers", "sales_orders"}
    assert {column["name"] for column in tables["sales_orders"]["columns"]} >= {"region", "sales_amount", "order_date"}
    assert tables["sales_orders"]["foreign_keys"][0]["table"] == "customers"


def test_simple_metric_query_returns_trace(engine):
    result = engine.answer("销售额是多少")
    assert result.status == "ok"
    assert "SUM" in result.sql
    assert result.rows[0]["销售额"] == pytest.approx(95052.0)
    assert result.provenance["source_type"] == "structured_database"
    assert result.provenance["query_hash"]


def test_year_and_region_filters_are_parameterized(engine):
    result = engine.answer("2025年华东地区的销售额是多少")
    assert result.status == "ok"
    assert '"order_date" >= ?' in result.sql
    assert result.parameters[:3] == ("2025-01-01", "2026-01-01", "华东")
    assert result.rows[0]["销售额"] == pytest.approx(29584.0)


def test_grouped_query_links_dimension(engine):
    result = engine.answer("2025年各地区的销量")
    assert result.status == "ok"
    assert result.plan["dimensions"] == ["region"]
    assert set(result.columns) == {"region", "销量"}
    assert len(result.rows) == 3


def test_channel_grouping(engine):
    result = engine.answer("按渠道统计订单数")
    assert result.status == "ok"
    assert result.plan["metric_function"] == "COUNT"
    assert result.plan["dimensions"] == ["channel"]


def test_missing_metric_asks_for_clarification(engine):
    result = engine.answer("2025年华东地区的情况")
    assert result.status == "clarification"
    assert result.sql is None
    assert "指标" in result.clarification


def test_comparison_without_scope_asks_for_clarification(engine):
    result = engine.answer("销售额对比")
    assert result.status == "clarification"
    assert "比较维度" in result.clarification
    assert result.clarification_code == "missing_comparison_scope"
    assert {item["value"] for item in result.clarification_options} >= {"region", "channel"}


def test_trend_without_time_range_asks_for_time(engine):
    result = engine.answer("销售额趋势")
    assert result.status == "clarification"
    assert result.clarification_code == "missing_time_range"
    assert result.clarification_options[0]["value"] == "year"


def test_multiple_metrics_are_not_silently_dropped(engine):
    result = engine.answer("销售额和销量")
    assert result.status == "ok"
    assert result.columns == ("销售额", "销量")
    assert {(item["column"], item["function"]) for item in result.plan["metrics"]} == {("sales_amount", "SUM"), ("quantity", "SUM")}


def test_month_grouping_has_stable_month_label(engine):
    result = engine.answer("按月统计2025年的销售额")
    assert result.status == "ok"
    assert result.plan["dimension_transforms"]["order_date"] == "month"
    assert result.columns == ("月份", "销售额")
    assert all(len(row["月份"]) == 7 for row in result.rows)


@pytest.mark.parametrize("question", ["按周统计销售额", "每天销售额", "按季度统计销售额"])
def test_unsupported_time_grain_does_not_fall_back_to_total(engine, question):
    result = engine.answer(question)
    assert result.status == "clarification"
    assert result.clarification_code == "unsupported_time_grain"


def test_numeric_having_is_parameterized(engine):
    result = engine.answer("销售额超过10000的地区")
    assert result.status == "ok"
    assert result.plan["dimensions"] == ["region"]
    assert result.plan["having"]["mode"] == "literal"
    assert 10000.0 in result.parameters
    assert "HAVING" in result.sql


def test_average_having_uses_read_only_subquery(engine):
    result = engine.answer("销售额高于平均的产品")
    assert result.status == "ok"
    assert result.plan["having"]["mode"] == "scalar_avg"
    assert "SELECT AVG" in result.sql
    assert result.plan["filters"] == []


def test_unknown_business_schema_uses_real_column_names(tmp_path):
    path = tmp_path / "custom.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE orders (区域 TEXT NOT NULL, 金额 REAL NOT NULL)")
        connection.executemany("INSERT INTO orders VALUES (?, ?)", [("东部", 10.0), ("西部", 20.0), ("东部", 5.0)])
    result = Nl2SqlEngine(path).answer("各区域金额")
    assert result.status == "ok"
    assert result.plan["table"] == "orders"
    assert result.plan["dimensions"] == ["区域"]
    assert result.rows[0] == {"区域": "西部", "金额": 20.0}


def test_schema_alias_file_can_be_loaded(tmp_path):
    database = tmp_path / "custom.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE orders (zone TEXT NOT NULL, amount REAL NOT NULL)")
        connection.executemany("INSERT INTO orders VALUES (?, ?)", [("东部", 10.0), ("西部", 20.0)])
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps([
        {"table": "orders", "column": "amount", "aliases": ["收入"], "role": "metric"},
        {"table": "orders", "column": "zone", "aliases": ["区域"], "role": "dimension"},
    ], ensure_ascii=False), encoding="utf-8")
    result = Nl2SqlEngine(database, aliases_path=aliases).answer("各区域收入")
    assert result.status == "ok"
    assert result.plan["metric_column"] == "amount"
    assert result.plan["dimensions"] == ["zone"]


def test_missing_subject_has_machine_readable_code(engine):
    result = engine.answer("情况")
    assert result.status == "clarification"
    assert result.clarification_code == "missing_data_subject"


def test_month_filter(engine):
    result = engine.answer("2025年2月华南销售额")
    assert result.status == "ok"
    assert "2025-02-01" in result.parameters
    assert "2025-03-01" in result.parameters


def test_limit_is_bounded(engine):
    result = engine.answer("前9999个产品的销量")
    assert result.status == "ok"
    assert result.plan["limit"] == 100


def test_schema_linking_and_join_for_customer_dimension(engine):
    result = engine.answer("按客户等级统计销售额")
    assert result.status == "ok"
    assert result.plan["join_tables"] == ["customers"]
    assert 'JOIN "customers"' in result.sql
    assert result.plan["dimension_tables"]["customer_level"] == "customers"
    assert {row["customer_level"] for row in result.rows} == {"高", "中", "低"}


def test_cross_table_filter_and_metric(engine):
    result = engine.answer("高等级客户的销售额")
    assert result.status == "ok"
    assert result.plan["join_tables"] == ["customers"]
    assert result.parameters == ("高", 100)
    assert result.rows[0]["销售额"] == pytest.approx(49179.0)


def test_foreign_key_graph_adds_intermediate_join(tmp_path):
    database = tmp_path / "three_hop.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE segments (
                segment_id TEXT PRIMARY KEY,
                segment_name TEXT NOT NULL
            );
            CREATE TABLE customers (
                customer_id TEXT PRIMARY KEY,
                segment_id TEXT NOT NULL REFERENCES segments(segment_id)
            );
            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL REFERENCES customers(customer_id),
                amount REAL NOT NULL
            );
            INSERT INTO segments VALUES ('S1', '企业'), ('S2', '零售');
            INSERT INTO customers VALUES ('C1', 'S1'), ('C2', 'S2');
            INSERT INTO orders VALUES ('O1', 'C1', 10.0), ('O2', 'C2', 20.0);
            """
        )
    aliases = tmp_path / "aliases.json"
    aliases.write_text(
        json.dumps(
            [
                {"table": "orders", "column": "amount", "aliases": ["金额"], "role": "metric"},
                {"table": "segments", "column": "segment_name", "aliases": ["客户分组"], "role": "dimension"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = Nl2SqlEngine(database, aliases_path=aliases).answer("按客户分组统计金额")
    assert result.status == "ok"
    assert result.plan["join_tables"] == ["customers", "segments"]
    assert [item["to_table"] for item in result.plan["join_path"]] == ["customers", "segments"]
    assert result.sql.count(" JOIN ") == 2
    assert result.rows == ({"segment_name": "零售", "amount": 20.0}, {"segment_name": "企业", "amount": 10.0})


def test_unreachable_required_table_returns_clarification(tmp_path):
    database = tmp_path / "disconnected.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE orders (order_id TEXT PRIMARY KEY, amount REAL NOT NULL);
            CREATE TABLE segments (segment_id TEXT PRIMARY KEY, segment_name TEXT NOT NULL);
            INSERT INTO orders VALUES ('O1', 10.0);
            INSERT INTO segments VALUES ('S1', '企业');
            """
        )
    aliases = tmp_path / "aliases.json"
    aliases.write_text(
        json.dumps(
            [
                {"table": "orders", "column": "amount", "aliases": ["金额"], "role": "metric"},
                {"table": "segments", "column": "segment_name", "aliases": ["客户分组"], "role": "dimension"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = Nl2SqlEngine(database, aliases_path=aliases).answer("按客户分组统计金额")
    assert result.status == "clarification"
    assert "外键" in result.clarification


def test_multiple_shortest_join_paths_request_explicit_choice(tmp_path):
    database = tmp_path / "ambiguous.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE orders (order_id TEXT PRIMARY KEY, customer_id TEXT REFERENCES customers(customer_id), amount REAL);
            CREATE TABLE customers (customer_id TEXT PRIMARY KEY, region_id TEXT REFERENCES regions(region_id), customer_name TEXT);
            CREATE TABLE regions (region_id TEXT PRIMARY KEY, region_name TEXT);
            CREATE TABLE order_regions (order_id TEXT REFERENCES orders(order_id), region_id TEXT REFERENCES regions(region_id));
            INSERT INTO regions VALUES ('R1', '华东');
            INSERT INTO customers VALUES ('C1', 'R1', '客户一');
            INSERT INTO orders VALUES ('O1', 'C1', 10.0);
            INSERT INTO order_regions VALUES ('O1', 'R1');
            """
        )
    aliases = tmp_path / "aliases.json"
    aliases.write_text(
        json.dumps(
            [
                {"table": "orders", "column": "amount", "aliases": ["金额"], "role": "metric"},
                {"table": "regions", "column": "region_name", "aliases": ["区域"], "role": "dimension"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = Nl2SqlEngine(database, aliases_path=aliases).answer("按区域统计金额")
    assert result.status == "clarification"
    assert result.clarification_code == "ambiguous_join_path"
    assert len(result.clarification_options) >= 2

    chosen = result.clarification_options[0]["value"]
    replanned = Nl2SqlEngine(database, aliases_path=aliases).answer(
        f"按区域统计金额 [join_path:{chosen}]"
    )
    assert replanned.status == "ok"
    assert replanned.plan["join_path"]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM sales_orders",
        "UPDATE sales_orders SET quantity=0",
        "SELECT * FROM sales_orders; DROP TABLE sales_orders",
        "PRAGMA table_info(sales_orders)",
        "SELECT * FROM sales_orders -- bypass",
        "SELECT load_extension('malicious.so')",
        "SELECT writefile('output.txt', 'data')",
    ],
)
def test_sql_guard_rejects_writes_and_injection(sql):
    with pytest.raises(SqlSafetyError):
        validate_read_only_sql(sql)


def test_sqlite_authorizer_rejects_write(tmp_path):
    path = initialize_database(tmp_path / "guard.sqlite")
    connection = sqlite3.connect(path)
    with pytest.raises(SqlSafetyError):
        execute_read_only(connection, "WITH changed AS (UPDATE sales_orders SET quantity=0 RETURNING order_id) SELECT * FROM changed")
    connection.close()


def test_sql_guard_rejects_oversized_statement():
    with pytest.raises(SqlSafetyError, match="长度上限"):
        validate_read_only_sql("SELECT " + "1 " * 50_001)


def test_execution_budget_can_handle_bounded_large_read(tmp_path):
    path = initialize_database(tmp_path / "large.sqlite", force=True)
    with sqlite3.connect(path) as connection:
        rows = []
        for index in range(100_000):
            source = ("SO-001", "2025-01-15", "华东", "线上", "手机", "星河 Pro", 1, 1.0, 1.0, "C-001")
            rows.append((f"L-{index:06d}", *source[1:]))
        connection.executemany(
            "INSERT INTO sales_orders (order_id, order_date, region, channel, product_category, product_name, quantity, unit_price, sales_amount, customer_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    result = Nl2SqlEngine(path).answer("2025年华东地区的销售额")
    assert result.status == "ok"
    assert result.rows[0]["销售额"] == pytest.approx(129584.0)


def test_distinct_customer_count_is_explicitly_planned(engine):
    result = engine.answer("不同客户数是多少")
    assert result.status == "ok"
    assert result.plan["metric_function"] == "COUNT_DISTINCT"
    assert "COUNT(DISTINCT" in result.sql
    assert result.rows[0]["客户数"] == 8


def test_rank_query_uses_auditable_window_function(engine):
    result = engine.answer("各地区销售额排名")
    assert result.status == "ok"
    assert result.plan["analysis_mode"] == "rank"
    assert "DENSE_RANK() OVER" in result.sql
    assert result.columns == ("region", "销售额", "排名")
    assert result.rows[0]["排名"] == 1


def test_share_query_returns_percentages_that_sum_to_100(engine):
    result = engine.answer("各地区销售额占比")
    assert result.status == "ok"
    assert result.plan["analysis_mode"] == "share"
    assert "OVER ()" in result.sql
    assert result.columns == ("region", "销售额", "占比(%)")
    assert sum(row["占比(%)"] for row in result.rows) == pytest.approx(100.0)


def test_rank_without_dimension_requests_clarification(engine):
    result = engine.answer("销售额排名")
    assert result.status == "clarification"
    assert result.clarification_code == "missing_analysis_dimension"
