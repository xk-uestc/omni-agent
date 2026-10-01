import json
import os
import sqlite3
from contextlib import closing

import pytest

from backend.nl2sql.engine import Nl2SqlEngine


@pytest.fixture
def live(tmp_path):
    path = tmp_path/'live.sqlite'
    writer = sqlite3.connect(path)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.executescript("CREATE TABLE sales_orders(order_id TEXT PRIMARY KEY,region TEXT,order_date INTEGER,sales_amount REAL); INSERT INTO sales_orders VALUES('A','华东',1735689600,10);")
    writer.commit()
    writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    try:
        yield writer, Nl2SqlEngine(path), tmp_path
    finally:
        writer.close()


def test_wal_value_index_and_date_format_follow_committed_changes(live):
    writer, engine, _ = live
    assert engine.answer('2025年华东销售额').rows[0]['销售额'] == 10
    main_before = engine.database_path.stat()
    writer.execute("UPDATE sales_orders SET region='华中',order_date=1735689600000,sales_amount=42")
    writer.commit()
    main_after = engine.database_path.stat()
    assert (main_before.st_mtime_ns,main_before.st_size)==(main_after.st_mtime_ns,main_after.st_size)
    result = engine.answer('2025年华中销售额')
    assert result.status == 'ok' and result.rows[0]['销售额'] == 42
    assert result.parameters[:2] == (1735689600000,1767225600000)
    assert result.provenance['consistency'] == 'sqlite_read_transaction'


def test_writer_commit_during_planning_does_not_mix_read_versions(live, monkeypatch):
    writer, engine, _ = live
    original = engine._rules_plan
    def changed_during_plan(*args,**kwargs):
        plan = original(*args,**kwargs)
        writer.execute("UPDATE sales_orders SET sales_amount=99")
        writer.commit()
        return plan
    monkeypatch.setattr(engine,'_rules_plan',changed_during_plan)
    first = engine.answer('2025年华东销售额')
    assert first.rows[0]['销售额'] == 10
    monkeypatch.setattr(engine,'_rules_plan',original)
    second = engine.answer('2025年华东销售额')
    assert second.rows[0]['销售额'] == 99
    assert first.provenance['source_revision'] != second.provenance['source_revision']
    assert writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0


def test_failed_query_releases_pinned_read_transaction(live, monkeypatch):
    writer, engine, _ = live
    def failed(*args,**kwargs):
        raise ValueError('intent failure')
    monkeypatch.setattr(engine,'_rules_plan',failed)
    with pytest.raises(ValueError,match='intent failure'):
        engine.answer('2025年华东销售额')
    writer.execute('UPDATE sales_orders SET sales_amount=19')
    writer.commit()
    assert writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0


def test_business_alias_refresh_does_not_depend_on_mtime(live):
    _, engine, parent = live
    path = parent/'aliases.json'
    def update(alias):
        path.write_text(json.dumps([{'table':'sales_orders','column':'region','value':'华东','aliases':[alias]}],ensure_ascii=False),encoding='utf-8')
    update('东区')
    engine.value_aliases_path = path
    assert engine.answer('2025年东区销售额').rows[0]['销售额'] == 10
    before = path.stat()
    update('东方区')
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert engine.answer('2025年东方区销售额').rows[0]['销售额'] == 10


def test_reserved_uri_filename_never_creates_an_alternate_database(tmp_path):
    path = tmp_path/'业务#%20库.sqlite'
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("CREATE TABLE sales_orders(order_id TEXT PRIMARY KEY,sales_amount REAL); INSERT INTO sales_orders VALUES('A',19);")
        connection.commit()
    files_before = {file.name for file in tmp_path.iterdir()}
    result = Nl2SqlEngine(path).answer('销售额')
    assert result.rows[0]['销售额'] == 19
    assert {file.name for file in tmp_path.iterdir()} == files_before


def test_unchanged_database_reuses_value_index(live, monkeypatch):
    from backend.nl2sql.value_index import ValueIndex
    _, engine, _ = live
    original = ValueIndex.build
    calls = []
    def counted(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)
    monkeypatch.setattr(ValueIndex,'build',counted)
    for _ in range(3):
        assert engine.answer('2025年华东销售额').rows[0]['销售额'] == 10
    assert len(calls) == 1


def test_wal_schema_addition_is_visible_and_read_only(live):
    writer, engine, _ = live
    assert engine.answer('销售额').rows[0]['销售额'] == 10
    writer.execute('ALTER TABLE sales_orders ADD COLUMN channel TEXT')
    writer.execute("UPDATE sales_orders SET channel='网上商城'")
    writer.commit()
    result = engine.answer('网上商城销售额')
    assert result.status == 'ok' and result.rows[0]['销售额'] == 10
    with engine._connect() as connection:
        with pytest.raises(sqlite3.OperationalError,match='readonly'):
            connection.execute('UPDATE sales_orders SET sales_amount=0')


def test_scoped_reads_are_nested_and_thread_isolated(live):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    writer, engine, _ = live
    ready, continue_reader = Event(), Event()
    def reader():
        with engine.consistent_reads():
            before = engine.answer('销售额').rows[0]['销售额']
            ready.set()
            assert continue_reader.wait(5)
            with engine.consistent_reads():
                during = engine.answer('销售额').rows[0]['销售额']
            after_nested = engine.answer('销售额').rows[0]['销售额']
        return before,during,after_nested
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(reader)
        try:
            assert ready.wait(5)
            writer.execute('UPDATE sales_orders SET sales_amount=77')
            writer.commit()
            assert engine.answer('销售额').rows[0]['销售额'] == 77
        finally:
            continue_reader.set()
        assert task.result(timeout=5) == (10,10,10)
    assert writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
