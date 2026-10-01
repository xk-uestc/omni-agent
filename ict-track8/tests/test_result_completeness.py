"""A bounded grouped query must not silently imply complete enumeration."""
import sqlite3

from backend.nl2sql.engine import Nl2SqlEngine


def answer(tmp_path, groups):
    database = tmp_path/'groups.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE Receipts (ReceiptId INTEGER PRIMARY KEY, Region TEXT, Amount NUMERIC)')
        connection.executemany('INSERT INTO Receipts VALUES (?,?,?)',
                               [(i+1, f'R{i:03d}', i+1) for i in range(groups)])
    return Nl2SqlEngine(database, metric_catalog_path=tmp_path/'absent.json').answer('按Region统计Amount合计')


def test_capped_grouped_results_warn_that_total_is_unverified(tmp_path):
    result = answer(tmp_path, 110)
    assert result.status == 'ok' and len(result.rows) == 100
    assert result.provenance['result_completeness'] == 'limit_reached_total_unknown'
    assert result.provenance['row_limit'] == 100
    assert any('全部分组' in notice for notice in result.notices)


def test_exact_limit_does_not_claim_truncation_or_completeness(tmp_path):
    result = answer(tmp_path, 100)
    assert result.provenance['result_completeness'] == 'limit_reached_total_unknown'


def test_small_result_does_not_emit_a_limit_warning(tmp_path):
    result = answer(tmp_path, 5)
    assert result.status == 'ok' and len(result.rows) == 5
    assert result.provenance['result_completeness'] == 'within_return_limit'
    assert not result.notices
