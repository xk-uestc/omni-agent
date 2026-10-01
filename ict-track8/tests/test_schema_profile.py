from __future__ import annotations

import sqlite3

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.schema import SchemaIntrospector, SchemaLinker


def _chinook_fixture(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE Customer (
                CustomerId INTEGER PRIMARY KEY,
                FirstName TEXT NOT NULL,
                Country TEXT NOT NULL
            );
            CREATE TABLE Invoice (
                InvoiceId INTEGER PRIMARY KEY,
                CustomerId INTEGER NOT NULL REFERENCES Customer(CustomerId),
                InvoiceDate TEXT NOT NULL,
                Total NUMERIC NOT NULL
            );
            CREATE TABLE InvoiceLine (
                InvoiceLineId INTEGER PRIMARY KEY,
                InvoiceId INTEGER NOT NULL REFERENCES Invoice(InvoiceId),
                TrackId INTEGER NOT NULL REFERENCES Track(TrackId),
                UnitPrice NUMERIC NOT NULL,
                Quantity INTEGER NOT NULL
            );
            CREATE TABLE Artist (
                ArtistId INTEGER PRIMARY KEY,
                Name TEXT NOT NULL
            );
            CREATE TABLE Album (
                AlbumId INTEGER PRIMARY KEY,
                Title TEXT NOT NULL,
                ArtistId INTEGER NOT NULL REFERENCES Artist(ArtistId)
            );
            CREATE TABLE Genre (
                GenreId INTEGER PRIMARY KEY,
                Name TEXT NOT NULL
            );
            CREATE TABLE Track (
                TrackId INTEGER PRIMARY KEY,
                Name TEXT NOT NULL,
                AlbumId INTEGER NOT NULL REFERENCES Album(AlbumId),
                GenreId INTEGER NOT NULL REFERENCES Genre(GenreId),
                Milliseconds INTEGER NOT NULL
            );
            INSERT INTO Customer VALUES (1, 'Ada', 'US'), (2, 'Lin', 'CA');
            INSERT INTO Invoice VALUES
                (10, 1, '2025-01-05', 30), (11, 1, '2025-02-05', 20), (12, 2, '2025-02-06', 15);
            INSERT INTO Artist VALUES (1, 'North'), (2, 'South');
            INSERT INTO Album VALUES (1, 'One', 1), (2, 'Two', 2);
            INSERT INTO Genre VALUES (1, 'Rock'), (2, 'Jazz');
            INSERT INTO Track VALUES
                (1, 'A', 1, 1, 180000), (2, 'B', 1, 1, 200000),
                (3, 'C', 2, 2, 210000);
            INSERT INTO InvoiceLine VALUES
                (100, 10, 1, 10, 1), (101, 10, 2, 20, 1),
                (102, 11, 1, 20, 1), (103, 12, 3, 15, 1);
            """
        )
    return path


def _rows(result):
    return [tuple(row.values()) for row in result.rows]


def test_profile_links_business_terms_from_chinook_schema(tmp_path):
    path = _chinook_fixture(tmp_path / "chinook.sqlite")
    with sqlite3.connect(path) as connection:
        tables = SchemaIntrospector().introspect(connection, include_row_count=False)

    links = SchemaLinker().link("各国家的订单金额", tables)
    assert {(item.table, item.column, item.role) for item in links} >= {
        ("Invoice", "Total", "metric"),
        ("Customer", "Country", "dimension"),
    }

    result = Nl2SqlEngine(path).answer("各国家的订单金额")
    assert result.status == "ok"
    assert result.plan["metric_table"] == "Invoice"
    assert result.plan["metric_column"] == "Total"
    assert result.plan["dimensions"] == ["Country"]
    assert result.plan["fan_out"] is False
    assert {tuple(row) for row in _rows(result)} == {("US", 50), ("CA", 15)}


def test_profile_counts_primary_entities_not_foreign_key_rows(tmp_path):
    path = _chinook_fixture(tmp_path / "chinook.sqlite")
    with sqlite3.connect(path) as connection:
        tables = SchemaIntrospector().introspect(connection, include_row_count=False)

    links = SchemaLinker().link("各音乐类型的订单数", tables)
    assert not any(item.table == "InvoiceLine" and item.column == "InvoiceId" for item in links)

    result = Nl2SqlEngine(path).answer("各音乐类型的订单数")
    assert result.status == "ok"
    assert result.plan["metric_table"] == "Invoice"
    assert result.plan["metric_function"] == "COUNT_DISTINCT"
    assert result.plan["fan_out"] is True
    with sqlite3.connect(path) as connection:
        expected = connection.execute(
            """
            SELECT Genre.Name, COUNT(DISTINCT Invoice.InvoiceId)
            FROM Invoice
            JOIN InvoiceLine ON InvoiceLine.InvoiceId = Invoice.InvoiceId
            JOIN Track ON Track.TrackId = InvoiceLine.TrackId
            JOIN Genre ON Genre.GenreId = Track.GenreId
            GROUP BY Genre.Name
            """
        ).fetchall()
    assert {tuple(row) for row in _rows(result)} == set(expected)


def test_profile_clarifies_when_metric_term_matches_multiple_tables(tmp_path):
    path = _chinook_fixture(tmp_path / "chinook.sqlite")
    result = Nl2SqlEngine(path).answer("各音乐类型的销售额")
    assert result.status == "clarification"
    assert result.clarification_code == "fan_out_risk"


def test_unresolved_entity_is_not_silently_dropped(tmp_path):
    path = _chinook_fixture(tmp_path / "chinook.sqlite")
    result = Nl2SqlEngine(path).answer("摇滚的订单金额")
    assert result.status == "clarification"
    assert result.clarification_code == "unresolved_terms"
    assert "摇滚" in result.plan["coverage"]["unresolved"]


def test_profile_clarifies_conflicting_dimension_roles(tmp_path):
    path = _chinook_fixture(tmp_path / "chinook.sqlite")
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE Invoice ADD COLUMN BillingCountry TEXT")
        connection.execute("UPDATE Invoice SET BillingCountry = CASE InvoiceId WHEN 10 THEN 'GB' ELSE 'CA' END")
    result = Nl2SqlEngine(path).answer("按国家统计订单金额")
    assert result.status == "clarification"
    assert result.clarification_code == "ambiguous_dimension"
    assert {item["value"] for item in result.clarification_options} == {"Customer.Country", "Invoice.BillingCountry"}


def test_unknown_explicit_grouping_never_silently_returns_total(tmp_path):
    path = _chinook_fixture(tmp_path / "chinook.sqlite")
    result = Nl2SqlEngine(path).answer("按周统计订单金额")
    assert result.status == "clarification"
    assert result.clarification_code == "unsupported_time_grain"


def test_dimension_clarification_selects_real_role_and_changes_result(tmp_path):
    from backend.clarification import ClarificationResolver, ClarificationSelection
    path = _chinook_fixture(tmp_path / "chinook.sqlite")
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE Invoice ADD COLUMN BillingCountry TEXT")
        connection.execute("UPDATE Invoice SET BillingCountry = CASE InvoiceId WHEN 10 THEN 'GB' ELSE 'CA' END")
    engine = Nl2SqlEngine(path)
    question = "按国家统计订单金额"
    resolver = ClarificationResolver()
    billing = engine.answer(resolver.apply(question, ClarificationSelection("ambiguous_dimension", "Invoice.BillingCountry")))
    customer = engine.answer(resolver.apply(question, ClarificationSelection("ambiguous_dimension", "Customer.Country")))
    assert billing.status == customer.status == "ok"
    assert billing.plan["dimension_tables"] == {"BillingCountry": "Invoice"}
    assert customer.plan["dimension_tables"] == {"Country": "Customer"}
    assert {row["BillingCountry"]: row[billing.plan["metric_label"]] for row in billing.rows} == {"GB": 30, "CA": 35}
    assert {row["Country"]: row[customer.plan["metric_label"]] for row in customer.rows} == {"US": 50, "CA": 15}


def test_forged_clarification_field_does_not_execute(tmp_path):
    path = _chinook_fixture(tmp_path / "chinook.sqlite")
    result = Nl2SqlEngine(path).answer("按国家统计订单金额 [field:dimension:Invoice.Total]")
    assert result.status == "clarification"
    assert result.clarification_code == "invalid_field_selection"


def test_generic_amount_does_not_invent_sales_semantics(tmp_path):
    path = tmp_path / "finance.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE loans (loan_id INTEGER PRIMARY KEY, amount REAL, issue_date TEXT, branch TEXT);
            INSERT INTO loans VALUES (1, 1000, '2025-01-01', 'A'), (2, 2000, '2025-02-01', 'B');
            """
        )
    engine = Nl2SqlEngine(path)
    sales = engine.answer("销售额是多少")
    assert sales.status == "clarification"
    assert sales.clarification_code == "missing_metric"
    amount = engine.answer("金额是多少")
    assert amount.status == "ok"
    assert amount.plan["metric_column"] == "amount"
