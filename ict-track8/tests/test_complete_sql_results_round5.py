"""Real SQLite complete results, independent of hidden benchmark questions."""
from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest

from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.metric_compiler import MetricCompiler
from backend.nl2sql.models import MetricSpec, QueryPlan
from backend.nl2sql.planner import SingleTablePlanner
from backend.nl2sql.result_artifact import (ResultBudgets, ResultRevisionError,
    execute_complete_read_only, pin_database, query_hash, read_result_page)
from backend.nl2sql.result_scope import configure_complete_scope
from backend.nl2sql.schema import SchemaIntrospector
from backend.nl2sql.security import SqlSafetyError


@pytest.fixture
def database(tmp_path):
    path = tmp_path / 'facts.sqlite'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE facts(seq INTEGER PRIMARY KEY, region TEXT, amount REAL, label TEXT)')
        db.executemany('INSERT INTO facts VALUES (?,?,?,?)',
            [(i, f'group-{i:03}', float(i), None if i % 11 == 0 else 'same label') for i in range(240)]
            + [(240, None, None, None), (241, 'group-000', 0.0, 'same label')])
    return path


def run(path, directory, sql, parameters=(), **kwargs):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('BEGIN')
        return execute_complete_read_only(connection, sql, parameters,
            database_path=path, artifact_dir=directory, **kwargs)


def all_pages(path, directory, metadata, **kwargs):
    rows, offset = [], 0
    while True:
        page = read_result_page(directory, metadata['artifact_id'], database_path=path,
            offset=offset, expected_query_sha256=metadata['query_sha256'],
            expected_binding_sha256=metadata['binding_sha256'], **kwargs)
        rows.extend(page['rows'])
        if page['next_offset'] is None:
            return rows
        offset = page['next_offset']


def base_plan(*, metrics=False, top_n=None):
    plan = QueryPlan(table='facts', metric_table='facts', metric_column='amount',
        metric_function='SUM', metric_label='sum_amount', dimensions=['region'],
        dimension_tables={'region': 'facts'}, top_n=top_n, rewritten_question='按region汇总amount')
    if metrics:
        plan.metrics = [MetricSpec('gross', 'facts', 'amount', 'SUM', 'sum_amount')]
    return plan


@pytest.mark.parametrize('metrics', [False, True])
def test_full_grouped_result_over_100_is_an_actual_artifact_not_a_count_claim(database, tmp_path, metrics):
    directory = tmp_path / 'results'
    engine = Nl2SqlEngine(database, result_artifact_dir=directory)
    # Test execution and typed compilers; no mock result rows or provider calls.
    engine._rules_plan = lambda *args, **kwargs: base_plan(metrics=metrics)
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    legacy = engine.answer('按region汇总amount')
    assert len(legacy.rows) == 100 and 'LIMIT ?' in legacy.sql
    assert legacy.provenance['result_completeness'] == 'limit_reached_total_unknown'
    result = engine.answer('按region汇总amount', complete_results=True)
    metadata = result.provenance['complete_result']
    assert result.status == 'ok' and len(result.rows) == 100
    assert 'LIMIT' not in result.sql and metadata['row_count'] == 241
    assert metadata['cursor_eof_verified'] and metadata['preview_truncated']
    actual = all_pages(database, directory, metadata)
    with sqlite3.connect(database) as db:
        expected = db.execute('SELECT region,SUM(amount) FROM facts GROUP BY region ORDER BY SUM(amount) DESC').fetchall()
    assert {tuple(row) for row in actual} == set(expected)
    assert len(actual) == len(expected) and any(row[0] is None for row in actual)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert metadata['query_sha256'] == query_hash(result.sql, result.parameters)


def test_arrays_preserve_multicolumn_duplicate_labels_nulls_duplicate_rows_and_blobs(database, tmp_path):
    directory = tmp_path / 'results'
    sql = 'SELECT label AS value, label AS value, NULL AS missing, x\'00FF\' AS raw FROM facts ORDER BY seq'
    result = run(database, directory, sql)
    assert result.columns == ('value', 'value', 'missing', 'raw')
    assert len(result.preview_cells) == 100 and len(result.preview_cells[0]) == 4
    actual = all_pages(database, directory, result.metadata)
    assert len(actual) == 242
    assert actual[1] == actual[2] and actual[0][:3] == [None, None, None]
    assert actual[1][:3] == ['same label', 'same label', None]
    assert actual[1][3] == {'$sqlite_type': 'blob', 'base64': 'AP8='}


@pytest.mark.parametrize('question, limit', [('返回3行', 3), ('显示前十一条', 11),
    ('show only 5 rows', 5), ('return at most 120 records', 120), ('limit 7', 7)])
def test_explicit_row_limit_is_preserved_in_both_compilers(database, tmp_path, question, limit):
    with sqlite3.connect(database) as db:
        tables = SchemaIntrospector().introspect(db)
        expected_count = len(db.execute('SELECT region FROM facts GROUP BY region LIMIT ?', (limit,)).fetchall())
    for metrics in (False, True):
        plan = base_plan(metrics=metrics)
        scope = configure_complete_scope(plan, question)
        sql, parameters = (MetricCompiler(SingleTablePlanner()).compile(plan, tables)
                           if metrics else SingleTablePlanner().build_sql(plan))
        assert sql.endswith('LIMIT ?') and parameters[-1] == limit
        result = run(database, tmp_path / f'results-{metrics}', sql, parameters, scope=scope)
        assert result.metadata['status'] == 'complete' and result.metadata['row_count'] == expected_count
        assert result.metadata['scope']['semantic_row_limit'] == limit


def test_dense_rank_top_n_keeps_all_ties_and_preserves_order(database, tmp_path):
    with sqlite3.connect(database) as db:
        db.execute('UPDATE facts SET amount=1 WHERE region IS NOT NULL')
        tables = SchemaIntrospector().introspect(db)
        # Top two dense ranks contain every non-NULL group, over 100 rows.
        expected = db.execute('SELECT region,total FROM (SELECT region,SUM(amount) total,'
            'DENSE_RANK() OVER (ORDER BY SUM(amount) DESC) r FROM facts WHERE region IS NOT NULL '
            'GROUP BY region) WHERE r<=2 ORDER BY r,total DESC').fetchall()
    plan = base_plan(top_n=2)
    configure_complete_scope(plan, '金额前2名的region（保留并列）')
    sql, parameters = SingleTablePlanner().build_sql(plan)
    assert 'DENSE_RANK' in sql and 'WHERE "排名" <= ?' in sql and 'ORDER BY "排名" ASC' in sql
    assert 'LIMIT' not in sql and parameters == (2,)
    result = run(database, tmp_path / 'results', sql, parameters)
    assert result.metadata['row_count'] == len(expected) == 240
    assert all_pages(database, tmp_path / 'results', result.metadata) == [list(row) for row in expected]


@pytest.mark.parametrize('question', ['返回3行，再显示5行', '最多500000行', '只看第十五行', 'limit everything'])
def test_ambiguous_or_unsupported_row_scope_cannot_silently_expand_user_intent(question):
    with pytest.raises(SqlSafetyError):
        configure_complete_scope(base_plan(), question)


@pytest.mark.parametrize('budgets, reason', [
    (ResultBudgets(max_rows=110), 'complete_result_row_budget_exceeded'),
    (ResultBudgets(max_bytes=128), 'complete_result_byte_budget_exceeded'),
    (ResultBudgets(max_steps=1000), 'complete_result_step_budget_exceeded'),
])
def test_resource_overflow_does_not_publish_a_complete_artifact(database, tmp_path, budgets, reason):
    sql = ('SELECT a.region,a.amount FROM facts a CROSS JOIN facts b ORDER BY a.seq,b.seq'
           if 'step' in reason else 'SELECT region,amount FROM facts ORDER BY seq')
    directory = tmp_path / 'results'
    result = run(database, directory, sql, budgets=budgets)
    assert result.metadata['status'] == 'partial' and result.metadata['reason'] == reason
    assert result.metadata['row_count'] is None and result.metadata['artifact_id'] is None
    assert not result.metadata['cursor_eof_verified'] and len(result.preview_rows) <= 100
    assert list(directory.iterdir()) == []


def test_wallclock_limit_is_enforced_during_cursor_fetch(database, tmp_path):
    directory = tmp_path / 'results'
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        def slow(value):
            time.sleep(.005)
            return value
        db.create_function('slow', 1, slow)
        with pytest.raises(SqlSafetyError, match='time_budget'):
            execute_complete_read_only(db, 'SELECT slow(amount) FROM facts', database_path=database,
                artifact_dir=directory, budgets=ResultBudgets(max_seconds=.03))
    assert list(directory.iterdir()) == []


def test_engine_reports_incomplete_on_hard_full_result_limit(database, tmp_path):
    engine = Nl2SqlEngine(database, result_artifact_dir=tmp_path / 'results',
                         complete_result_budgets=ResultBudgets(max_rows=120))
    engine._rules_plan = lambda *args, **kwargs: base_plan()
    result = engine.answer('按region汇总amount', complete_results=True)
    assert result.status == 'incomplete' and result.result_state == 'partial_rows'
    assert result.provenance['result_completeness'] == 'partial'
    assert result.provenance['complete_result']['artifact_id'] is None


def test_parameters_and_distinct_queries_cannot_be_substituted_in_artifact_read(database, tmp_path):
    directory = tmp_path / 'results'
    sql = 'SELECT region,amount FROM facts WHERE amount > ? ORDER BY seq'
    result = run(database, directory, sql, (120,))
    with pytest.raises(ResultRevisionError, match='binding'):
        read_result_page(directory, result.metadata['artifact_id'], database_path=database,
                         expected_query_sha256=query_hash(sql, (119,)),
                         expected_binding_sha256=result.metadata['binding_sha256'])
    assert len(all_pages(database, directory, result.metadata)) == 119


@pytest.mark.parametrize('mutation', ['data', 'metadata', 'database', 'replace_database', 'source_code'])
def test_artifact_requires_unchanged_data_source_and_producer(database, tmp_path, mutation):
    directory, code = tmp_path / 'results', tmp_path / 'producer.py'
    code.write_text('version = 1\n')
    result = run(database, directory, 'SELECT region,amount FROM facts ORDER BY seq', source_code_paths=(code,))
    artifact_id = result.metadata['artifact_id']
    if mutation == 'data':
        with (directory / (artifact_id + '.jsonl')).open('ab') as stream:
            stream.write(b'["tampered",null]\n')
    elif mutation == 'metadata':
        path = directory / (artifact_id + '.json')
        record = json.loads(path.read_bytes())
        record['query_sha256'] = '0' * 64
        path.write_text(json.dumps(record))
    elif mutation == 'database':
        with sqlite3.connect(database) as db:
            db.execute('UPDATE facts SET amount=999 WHERE seq=1')
    elif mutation == 'replace_database':
        replacement = tmp_path / 'new.sqlite'
        with sqlite3.connect(replacement) as db:
            db.execute('CREATE TABLE other(value TEXT)')
        db.close()
        replacement.replace(database)
    else:
        code.write_text('version = 2\n')
    with pytest.raises(ResultRevisionError):
        read_result_page(directory, artifact_id, database_path=database, source_code_paths=(code,),
                         expected_binding_sha256=result.metadata['binding_sha256'])


def test_rehashed_forged_artifact_cannot_replace_the_original_trusted_binding(database, tmp_path):
    directory = tmp_path / 'results'
    result = run(database, directory, 'SELECT region,amount FROM facts ORDER BY seq')
    artifact_id = result.metadata['artifact_id']
    metadata_path, data_path = (directory / (artifact_id + '.json'), directory / (artifact_id + '.jsonl'))
    original_binding = result.metadata['binding_sha256']
    record = json.loads(metadata_path.read_bytes())
    rows = data_path.read_bytes().splitlines(keepends=True)
    rows[0] = b'["invented group",999999]\n'
    forged_data = b''.join(rows)
    data_path.write_bytes(forged_data)
    record['byte_count'] = len(forged_data)
    record['data_sha256'] = hashlib.sha256(forged_data).hexdigest()
    del record['binding_sha256']
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    record['binding_sha256'] = hashlib.sha256(canonical).hexdigest()
    metadata_path.write_text(json.dumps(record))
    assert record['query_sha256'] == result.metadata['query_sha256']
    with pytest.raises(ResultRevisionError, match='binding'):
        read_result_page(directory, artifact_id, database_path=database,
            expected_query_sha256=result.metadata['query_sha256'], expected_binding_sha256=original_binding)


def test_engine_trusted_binding_survives_only_via_client_receipt_after_restart(database, tmp_path):
    directory = tmp_path / 'results'
    engine = Nl2SqlEngine(database, result_artifact_dir=directory)
    engine._rules_plan = lambda *args, **kwargs: base_plan()
    result = engine.answer('按region汇总amount', complete_results=True)
    metadata = result.provenance['complete_result']
    assert engine.complete_result_page(metadata['artifact_id'])['row_count'] == 241
    restarted = Nl2SqlEngine(database, result_artifact_dir=directory)
    with pytest.raises(ValueError, match='initial_result_binding_required'):
        restarted.complete_result_page(metadata['artifact_id'])
    assert restarted.complete_result_page(metadata['artifact_id'],
        expected_binding_sha256=metadata['binding_sha256'])['row_count'] == 241


@pytest.mark.parametrize('suffix', ['.json', '.jsonl'])
def test_foreign_symlink_artifact_targets_are_rejected(database, tmp_path, suffix):
    directory = tmp_path / 'results'
    result = run(database, directory, 'SELECT region,amount FROM facts ORDER BY seq')
    target = directory / (result.metadata['artifact_id'] + suffix)
    outside = tmp_path / ('outside' + suffix)
    target.replace(outside)
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip('host does not allow creating symlinks')
    with pytest.raises(ResultRevisionError, match='outside_directory'):
        read_result_page(directory, result.metadata['artifact_id'], database_path=database,
                         expected_binding_sha256=result.metadata['binding_sha256'])


def test_uncheckpointed_wal_change_invalidates_complete_result(database, tmp_path):
    directory = tmp_path / 'results'
    with closing(sqlite3.connect(database)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('UPDATE facts SET amount=33 WHERE seq=1')
        writer.commit()
        result = run(database, directory, 'SELECT region,amount FROM facts ORDER BY seq')
        assert 'wal' in result.metadata['source_pin']['files']
        writer.execute('UPDATE facts SET amount=44 WHERE seq=2')
        writer.commit()
        with pytest.raises(ResultRevisionError, match='source_or_producer'):
            read_result_page(directory, result.metadata['artifact_id'], database_path=database,
                             expected_binding_sha256=result.metadata['binding_sha256'])


def test_19119_actual_rows_and_every_cell_are_saved_and_read_back(tmp_path):
    path, directory = tmp_path / 'large.sqlite', tmp_path / 'results'
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE facts(group_name TEXT, amount REAL, condition TEXT)')
        db.executemany('INSERT INTO facts VALUES (?,?,?)',
            [(f'group-{i:05}', None if i % 211 == 0 else i / 7, None if i % 71 == 0 else 'eligible')
             for i in range(19119)])
        reference = db.execute('SELECT group_name,SUM(amount),condition FROM facts '
            'GROUP BY group_name,condition ORDER BY group_name').fetchall()
    result = run(path, directory, 'SELECT group_name,SUM(amount) AS amount,condition FROM facts '
        'GROUP BY group_name,condition ORDER BY group_name')
    assert result.metadata['row_count'] == 19119 and len(result.preview_rows) == 100
    assert result.metadata['cursor_eof_verified'] and result.metadata['preview_truncated']
    actual = all_pages(path, directory, result.metadata)
    assert len(actual) == 19119 and [tuple(row) for row in actual] == reference


def test_result_http_opt_in_and_pagination_return_actual_cells_with_initial_binding(database, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.app as app_module
    engine = Nl2SqlEngine(database, result_artifact_dir=tmp_path / 'results')
    engine._rules_plan = lambda *args, **kwargs: base_plan()
    monkeypatch.setattr(app_module, 'engine', engine)
    client = TestClient(app_module.app)
    legacy = client.post('/api/v1/nl2sql/query', json={'question': '按region汇总amount'}).json()
    assert 'complete_result' not in legacy['provenance'] and len(legacy['rows']) == 100
    response = client.post('/api/v1/nl2sql/query', json={'question': '按region汇总amount', 'complete_results': True})
    assert response.status_code == 200
    result = response.json()
    receipt = result['provenance']['complete_result']
    assert receipt['row_count'] == 241 and len(result['rows']) == 100
    route = '/api/v1/nl2sql/results/' + receipt['artifact_id']
    assert client.get(route).status_code == 422
    request = {'binding_sha256': receipt['binding_sha256'], 'query_sha256': receipt['query_sha256'],
               'offset': 200, 'page_size': 100}
    page = client.get(route, params=request)
    assert page.status_code == 200 and len(page.json()['rows']) == 41
    assert page.json()['next_offset'] is None and page.json()['row_count'] == 241
    assert client.get(route, params={**request, 'binding_sha256': '0' * 64}).status_code == 409
    assert client.get(route, params={**request, 'page_size': 101}).status_code == 422


def test_http_partial_budget_failure_is_explicit_and_has_no_complete_artifact(database, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.app as app_module
    engine = Nl2SqlEngine(database, result_artifact_dir=tmp_path / 'results',
                         complete_result_budgets=ResultBudgets(max_rows=110))
    engine._rules_plan = lambda *args, **kwargs: base_plan()
    monkeypatch.setattr(app_module, 'engine', engine)
    response = TestClient(app_module.app).post('/api/v1/nl2sql/query',
        json={'question': '按region汇总amount', 'complete_results': True})
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'incomplete' and payload['result_state'] == 'partial_rows'
    assert payload['provenance']['complete_result']['artifact_id'] is None


@pytest.mark.parametrize('question', ['按金额排名，返回前2行（保留并列）',
    'return only 2 rows with ties', '排名前3名含并列，最多3行',
    'return only 2 rows including all ties', '按金额排名，返回前2行（包括所有并列）',
    'list 2 rows while retaining order, including all the equal-score ties',
    'return 2 rows and preserve all tied rows', '返回前2行，并保留全部的同分项'])
def test_rank_ties_and_explicit_row_cap_conflict_fails_closed(question):
    plan = base_plan(top_n=2)
    with pytest.raises(SqlSafetyError, match='conflicts_with_ties'):
        configure_complete_scope(plan, question)
    assert plan.complete_results is False


@pytest.mark.parametrize('question, preview', [
    ('只展示100行预览，并将全部结果保存供分页下载', 100),
    ('只展示50行预览，并将全部结果保存供分页下载', 50),
    ('预览前50行，然后导出全部结果', 50),
    ('show only 50 rows as preview and export all results', 50),
    ('preview first 50 rows; return all rows in the artifact', 50),
    ('只展示200行预览，并将全部结果保存', 100),
])
def test_preview_language_changes_actual_preview_only_not_complete_sql(database, tmp_path, question, preview):
    engine = Nl2SqlEngine(database, result_artifact_dir=tmp_path / 'results')
    engine._rules_plan = lambda *args, **kwargs: base_plan()
    result = engine.answer(question, complete_results=True)
    receipt = result.provenance['complete_result']
    assert result.status == 'ok' and receipt['row_count'] == 241
    assert len(result.rows) == len(receipt['preview_cells']) == preview
    assert receipt['preview_limit'] == preview and receipt['scope']['effective_preview_limit'] == preview
    assert result.plan['semantic_row_limit'] is None and 'LIMIT' not in result.sql
    assert len(all_pages(database, tmp_path / 'results', receipt)) == 241


@pytest.mark.parametrize('question', ['return ten rows', 'show at most twenty five records',
    'display exactly a dozen rows', '仅展示一千行预览', 'list ten rows',
    'fetch no more than a dozen records', 'get exactly ten rows',
    'retrieve ten rows', 'output ten rows', 'emit ten records',
    'list ' + 'the next requested ' * 5 + 'ten rows'])
def test_unknown_explicit_row_counts_never_silently_remove_a_limit(question):
    with pytest.raises(SqlSafetyError, match='ambiguous_or_unsupported'):
        configure_complete_scope(base_plan(), question)


@pytest.mark.parametrize('verb', ['list', 'fetch', 'get', 'retrieve', 'output', 'emit'])
def test_supported_row_request_verbs_preserve_limits_and_all_rows(verb):
    limited = base_plan()
    configure_complete_scope(limited, f'{verb} only 2 rows')
    assert limited.semantic_row_limit == 2
    all_rows = base_plan()
    configure_complete_scope(all_rows, f'{verb} all rows')
    assert all_rows.semantic_row_limit is None


def _replace_data_and_recompute_every_public_hash(directory, receipt):
    data = directory / (receipt['artifact_id'] + '.jsonl')
    meta = directory / (receipt['artifact_id'] + '.json')
    record = json.loads(meta.read_bytes())
    lines = data.read_bytes().splitlines(keepends=True)
    lines[0] = b'["forged-region",999999]\n'
    raw = b''.join(lines)
    data.write_bytes(raw)
    record['byte_count'] = len(raw)
    record['data_sha256'] = hashlib.sha256(raw).hexdigest()
    signature = record.pop('receipt_signature')
    record.pop('binding_sha256')
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    record['binding_sha256'] = hashlib.sha256(canonical).hexdigest()
    record['receipt_signature'] = signature  # attacker cannot recompute the private HMAC
    meta.write_text(json.dumps(record))
    return record['binding_sha256']


@pytest.mark.parametrize('lose_cache', ['restart', 'eviction'])
def test_server_signature_rejects_forgery_even_if_client_passes_new_public_binding(database, tmp_path, lose_cache):
    directory = tmp_path / 'results'
    engine = Nl2SqlEngine(database, result_artifact_dir=directory)
    engine._rules_plan = lambda *args, **kwargs: base_plan()
    receipt = engine.answer('按region汇总amount', complete_results=True).provenance['complete_result']
    forged_binding = _replace_data_and_recompute_every_public_hash(directory, receipt)
    if lose_cache == 'restart':
        engine = Nl2SqlEngine(database, result_artifact_dir=directory)
    else:
        engine._result_bindings.clear()
    with pytest.raises(ResultRevisionError, match='server_receipt_invalid'):
        engine.complete_result_page(receipt['artifact_id'], expected_binding_sha256=forged_binding,
                                    expected_query_sha256=receipt['query_sha256'])


@pytest.mark.parametrize('change', ['missing', 'replaced'])
def test_private_receipt_key_missing_or_replaced_never_regenerates_on_read(database, tmp_path, change):
    directory = tmp_path / 'results'
    receipt = run(database, directory, 'SELECT region,amount FROM facts ORDER BY seq').metadata
    key = directory.parent / '.query-result-private' / 'receipt-signing.key'
    if change == 'missing':
        key.unlink()
    else:
        key.write_bytes(bytes(32))
    with pytest.raises(ResultRevisionError, match='private_key_missing|server_receipt_invalid'):
        read_result_page(directory, receipt['artifact_id'], database_path=database,
                         expected_binding_sha256=receipt['binding_sha256'])
    assert key.exists() == (change == 'replaced')


def test_shared_operation_deadline_includes_source_pins_sql_and_postverification(database, tmp_path, monkeypatch):
    import backend.nl2sql.result_artifact as module
    original = module._hash_file
    def delayed_hash(*args, **kwargs):
        time.sleep(.04)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, '_hash_file', delayed_hash)
    directory = tmp_path / 'results'
    with closing(sqlite3.connect(database)) as db:
        db.create_function('slow', 1, lambda value: (time.sleep(.04), value)[1])
        with pytest.raises(SqlSafetyError, match='time_budget'):
            execute_complete_read_only(db, 'SELECT slow(amount) FROM facts LIMIT 1',
                database_path=database, artifact_dir=directory, budgets=ResultBudgets(max_seconds=.06))
    assert list(directory.glob('*.json')) == [] and list(directory.glob('*.jsonl')) == []


def test_page_deadline_includes_both_source_pins_without_renewal(database, tmp_path, monkeypatch):
    import backend.nl2sql.result_artifact as module
    directory = tmp_path / 'results'
    clock = {'now': 1000.0}
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock['now'])
    receipt = run(database, directory, 'SELECT region,amount FROM facts LIMIT 1',
                  budgets=ResultBudgets(max_seconds=.06)).metadata
    original = module._hash_file
    def delayed_hash(*args, **kwargs):
        clock['now'] += .04
        return original(*args, **kwargs)
    monkeypatch.setattr(module, '_hash_file', delayed_hash)
    with pytest.raises(SqlSafetyError, match='time_budget'):
        read_result_page(directory, receipt['artifact_id'], database_path=database,
                         expected_binding_sha256=receipt['binding_sha256'])


def test_source_change_between_planning_and_execution_is_rejected(database, tmp_path):
    budgets = ResultBudgets()
    source = pin_database(database, budgets)
    with sqlite3.connect(database) as db:
        db.execute('UPDATE facts SET amount=999 WHERE seq=1')
    with pytest.raises(ResultRevisionError, match='after_planning'):
        run(database, tmp_path / 'results', 'SELECT amount FROM facts', expected_source=source)


def test_producer_code_change_during_cursor_execution_never_publishes_artifact(database, tmp_path):
    directory, code = tmp_path / 'results', tmp_path / 'producer.py'
    code.write_text('version = 1\n')
    with sqlite3.connect(database) as db:
        def mutate(value):
            code.write_text('version = 2\n')
            return value
        db.create_function('mutate', 1, mutate)
        with pytest.raises(ResultRevisionError, match='producer'):
            execute_complete_read_only(db, 'SELECT mutate(amount) FROM facts', database_path=database,
                artifact_dir=directory, source_code_paths=(code,))
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize('sql', ['DELETE FROM facts', 'ATTACH DATABASE ":memory:" AS x',
                                'SELECT load_extension(?)', 'SELECT writefile(?)'])
def test_full_artifacts_do_not_bypass_readonly_security(database, tmp_path, sql):
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    with pytest.raises(SqlSafetyError):
        run(database, tmp_path / 'results', sql)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
