import sqlite3

from backend.nl2sql.engine import Nl2SqlEngine


def test_ascii_values_do_not_match_inside_table_names(tmp_path):
    database=tmp_path/'schema.sqlite'
    with sqlite3.connect(database) as c:
        c.executescript('CREATE TABLE Invoice(InvoiceId INTEGER PRIMARY KEY, BillingCountry TEXT, Total REAL, InvoiceDate TEXT); CREATE TABLE Customer(CustomerId INTEGER PRIMARY KEY, Country TEXT); INSERT INTO Invoice VALUES(1,"NV",15,"2025-01-01"),(2,"CN",30,"2025-02-01"); INSERT INTO Customer VALUES(1,"NV");')
    e=Nl2SqlEngine(database)
    result=e.answer('Invoice Total')
    assert result.status=='ok' and result.rows[0]['Total']==45
    result=e.answer('Invoice Total按BillingCountry排名')
    assert result.status=='ok' and len(result.rows)==2
    result=e.answer('2025年按月统计Invoice Total')
    assert result.status=='ok' and len(result.rows)==2
