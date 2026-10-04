import sqlite3
from backend.nl2sql.date_profile import storage_profiles
from backend.nl2sql.schema import SchemaIntrospector


def profile(values):
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE facts(id INT,event_date TEXT)')
        db.executemany('INSERT INTO facts VALUES(?,?)',enumerate(values))
        tables=SchemaIntrospector().introspect(db,include_row_count=False)
        return storage_profiles(db,tables,'列出按event_date过滤的记录')[0]


def test_all_rows_not_only_first_twenty_are_checked():
    result=profile(['2024-01-01']*21+['01/02/2024'])
    assert result['format']=='unknown' and result['non_null_rows']==22


def test_iso_calendar_validation_and_empty_storage_are_not_guessed():
    assert profile(['2024-02-30'])['format']=='unknown'
    assert profile([None])['format']=='unknown'


def test_verified_iso_profile_contains_no_values():
    result=profile(['2024-01-01','2025-01-02 13:30:00',None])
    assert result['format']=='iso_text' and result['null_rows']==1
    assert '2024-01-01' not in str(result)


def test_mixed_epoch_units_are_not_collapsed():
    assert profile([1700000000,1700000000000])['format']=='unknown'


def test_numeric_calendar_encoding_is_not_claimed_to_be_unix_time():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE facts(event_timestamp INTEGER)')
        db.execute('INSERT INTO facts VALUES(20240101000000)')
        schema=SchemaIntrospector().introspect(db,include_row_count=False)
        result=storage_profiles(db,schema,'event_timestamp')[0]
        assert result['format']=='unknown'
        assert result['numeric_unit_verification']=='not_proven_by_magnitude'


def test_budget_exhaustion_is_unknown_and_connection_recovers():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE facts(event_date TEXT)')
        db.executemany('INSERT INTO facts VALUES(?)',[('2024-01-01',)]*1000)
        tables=SchemaIntrospector().introspect(db,include_row_count=False)
        result=storage_profiles(db,tables,'event_date',max_seconds=0)
        assert result[0]['format']=='unknown'
        assert db.execute('SELECT COUNT(*) FROM facts').fetchone()[0]==1000
