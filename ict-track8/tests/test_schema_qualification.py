"""Explicit table owners disambiguate fields without domain aliases."""
import sqlite3

import pytest

from backend.nl2sql.schema import SchemaIntrospector, SchemaLinker


@pytest.fixture
def schema():
    with sqlite3.connect(':memory:') as connection:
        connection.executescript('''
            CREATE TABLE Vendors(VendorId INTEGER PRIMARY KEY, Name TEXT, Amount NUMERIC);
            CREATE TABLE Receipts(ReceiptId INTEGER PRIMARY KEY, VendorId INTEGER,
                Name TEXT, Amount NUMERIC, FOREIGN KEY(VendorId) REFERENCES Vendors(VendorId));
        ''')
        return SchemaIntrospector().introspect(connection, include_row_count=False)


@pytest.mark.parametrize('qualified', ['Vendors.Name', 'Vendors的Name', 'Vendors表的Name',
                                      'Vendors中的Name', 'Vendors中Name', 'Vendors内的Name'])
def test_natural_qualified_dimension_preserves_owner(schema, qualified):
    links = SchemaLinker(rules=()).link('按'+qualified+'统计Receipts.Amount合计', schema)
    assert {(link.table, link.column) for link in links if link.column == 'Name'} == {('Vendors','Name')}
    assert {(link.table, link.column) for link in links if link.role == 'metric'} == {('Receipts','Amount')}


def test_natural_qualified_numeric_field_preserves_owner(schema):
    links = SchemaLinker(rules=()).link('Receipts的VendorId记录数', schema)
    assert {(link.table, link.column) for link in links if link.column == 'VendorId'} == {('Receipts','VendorId')}


def test_unqualified_same_name_fields_remain_ambiguous(schema):
    links = SchemaLinker(rules=()).link('按Name统计Amount合计', schema)
    assert {link.table for link in links if link.column == 'Name'} == {'Vendors','Receipts'}
    assert {link.table for link in links if link.column == 'Amount'} == {'Vendors','Receipts'}


def test_owner_inside_longer_identifier_is_not_qualification(schema):
    links = SchemaLinker(rules=()).link('OtherVendors.Name统计Receipts.Amount', schema)
    assert {link.table for link in links if link.column == 'Name'} == {'Vendors','Receipts'}


def test_table_with_regex_metacharacters_is_literal():
    with sqlite3.connect(':memory:') as connection:
        connection.executescript('CREATE TABLE "Bills+" (Amount NUMERIC); CREATE TABLE Other (Amount NUMERIC);')
        tables = SchemaIntrospector().introspect(connection, include_row_count=False)
    links = SchemaLinker(rules=()).link('Bills+的Amount合计', tables)
    assert {link.table for link in links if link.column == 'Amount'} == {'Bills+'}
