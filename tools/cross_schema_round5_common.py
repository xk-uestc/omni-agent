"""Offline contracts shared by isolated runner and scorer; contains no questions."""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = Path('D:/ICT8-OfficialDatasets/sakila-round5-cross-schema-20261002')
EVALUATION_SUBDIR = Path('evaluation/v2')
READINESS_FILE = 'READINESS_WORKERS_V1.json'
MODEL = 'gpt-6-luna'
PRIVATE_KEYS = {'gold_sql', 'reference_sql', 'expected_rows', 'full_expected_rows',
                'expected_output_roles', 'comparison_contract', 'future_questions'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def value_digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def inside(directory, relative):
    directory = Path(directory).resolve()
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory) or path == directory:
        raise ValueError('asset_path_outside_directory')
    return path


def read_sql(database, sql, parameters=(), *, seconds=30, max_rows=200000):
    """Execute the unchanged SQL on an independent bounded read-only connection."""
    if not isinstance(sql, str) or not sql.strip().lower().startswith(('select', 'with')):
        raise ValueError('reference_must_be_select_or_with')
    deadline = time.monotonic() + seconds
    reads = set()
    allow = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
             getattr(sqlite3, 'SQLITE_RECURSIVE', 33)}
    def authorize(action, first, second, database_name, origin):
        if action not in allow:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and str(second).lower() in {'load_extension', 'writefile', 'readfile'}:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ:
            reads.add((first, second))
        return sqlite3.SQLITE_OK
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.set_authorizer(authorize)
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        cursor = connection.execute(sql, parameters)
        names = [column[0] for column in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
        if len(rows) > max_rows:
            raise ValueError('reference_result_budget_exceeded')
    return names, [list(row) for row in rows], [list(item) for item in sorted(reads)]


def verify_source(directory):
    directory = Path(directory).resolve()
    manifest_path = directory / 'SOURCE_MANIFEST.json'
    manifest = load(manifest_path)
    for asset in [*manifest['assets'], manifest['database']]:
        if digest(inside(directory, asset['file'])) != asset['sha256']:
            raise ValueError('source_asset_sha_mismatch')
    database = inside(directory, manifest['database']['file'])
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        objects = [list(row) for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
        if value_digest(objects) != manifest['schema_sha256']:
            raise ValueError('source_schema_sha_mismatch')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('source_integrity_failed')
        if connection.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('source_foreign_keys_failed')
    return manifest, database


def verify_public(directory):
    directory = Path(directory).resolve()
    source, database = verify_source(directory)
    manifest = load(directory / EVALUATION_SUBDIR / 'PUBLIC_MANIFEST.json')
    if (manifest['source_manifest_sha256'] != digest(directory / 'SOURCE_MANIFEST.json')
            or manifest['database_sha256'] != digest(database)):
        raise ValueError('frozen_source_changed')
    inputs = inside(directory, manifest['system_input_file'])
    if digest(inputs) != manifest['system_input_sha256']:
        raise ValueError('frozen_system_input_changed')
    bundle = load(inputs)
    if set(bundle) != {'reference_date', 'cases'} or len(bundle['cases']) != 56:
        raise ValueError('system_input_shape_invalid')
    ids = [c['case_id'] for c in bundle['cases']]
    if len(set(ids)) != 56 or any(PRIVATE_KEYS.intersection(c) for c in bundle['cases']):
        raise ValueError('system_input_private_or_duplicate_case')
    singles = [c for c in bundle['cases'] if c['kind'] == 'single']
    sessions = Counter(c['session_id'] for c in bundle['cases'] if c['kind'] == 'session')
    if len(singles) != 36 or len(sessions) != 4 or set(sessions.values()) != {5}:
        raise ValueError('planned_denominator_changed')
    return source, database, manifest, bundle


def implementation_hashes():
    files = sorted((ROOT / 'ict-track8' / 'backend').rglob('*.py'))
    files += [ROOT / 'tools' / name for name in (
        'cross_schema_round5_common.py', 'run_cross_schema_round5.py',
        'score_cross_schema_round5.py', 'validate_cross_schema_round5.py', 'model_runtime.py')]
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in files}


def forbidden_payload_fields(value):
    if isinstance(value, dict):
        return bool(PRIVATE_KEYS.intersection(value)) or any(forbidden_payload_fields(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(forbidden_payload_fields(v) for v in value)
    return False


def numeric_equal(left, right, contract):
    if left is None or right is None:
        return left is None and right is None
    kind = contract['type']
    if kind in {'integer', 'identifier'}:
        return type(left) is int and type(right) is int and left == right
    if kind in {'text', 'timestamp'}:
        return isinstance(left, str) and isinstance(right, str) and left == right
    if kind not in {'money', 'real'} or type(left) not in (int, float) or type(right) not in (int, float):
        return False
    try:
        actual, expected = Decimal(str(left)), Decimal(str(right))
        tolerance = Decimal(contract.get('absolute_tolerance', '0'))
        if not actual.is_finite() or not expected.is_finite() or abs(actual - expected) > tolerance:
            return False
        if kind == 'money' and actual.quantize(Decimal('.01')) != expected.quantize(Decimal('.01')):
            return False
        return True
    except InvalidOperation:
        return False


def rows_equal(actual, expected, contract):
    columns = contract['columns']
    if (not isinstance(actual, list) or len(actual) != len(expected)
            or any(not isinstance(row, (list, tuple)) or len(row) != len(columns) for row in [*actual, *expected])):
        return False
    def row_equal(a, b):
        return all(numeric_equal(x, y, c) for x, y, c in zip(a, b, columns))
    if contract['mode'] == 'ordered':
        return all(row_equal(a, b) for a, b in zip(actual, expected))
    if contract['mode'] != 'bag':
        return False
    # Fixed lexicographic order preserves duplicate multiplicities. Numeric
    # tolerances only compare aligned rows; never deduplicate or guess columns.
    def key(row):
        return tuple((0, '') if v is None else (1, Decimal(str(v)))
                     if type(v) in (int, float) and Decimal(str(v)).is_finite()
                     else (2, str(v)) for v in row)
    return all(row_equal(a, b) for a, b in zip(sorted(actual, key=key), sorted(expected, key=key)))


def projection(result, roles):
    """Map frozen physical roles, independent of answer values or display labels."""
    columns = result.get('columns', [])
    if len(columns) != len(roles) or len(set(columns)) != len(columns):
        return None
    plan = result.get('plan', {})
    metrics = plan.get('metrics') or [{'table': plan.get('metric_table') or plan.get('table'),
        'column': plan.get('metric_column'), 'function': plan.get('metric_function'), 'label': plan.get('metric_label')}]
    labels = []
    for role in roles:
        kind = role['kind']
        if kind == 'dimension':
            column = role['column']
            if (column not in plan.get('dimensions', [])
                    or plan.get('dimension_tables', {}).get(column, plan.get('table')) != role['table']
                    or plan.get('dimension_transforms', {}).get(column, 'raw') != role.get('transform', 'raw')):
                return None
            label = plan.get('dimension_labels', {}).get(column, column)
        elif kind == 'metric':
            candidates = [m for m in metrics if all(m.get(k) == role[k] for k in ('table', 'column', 'function'))]
            if len(candidates) != 1:
                return None
            label = candidates[0]['label']
        elif kind == 'sql_output':
            # Complex outputs without a representable current plan must use
            # the explicit output alias requested by the frozen user question.
            label = role['name']
        else:
            return None
        if label not in columns or label in labels:
            return None
        labels.append(label)
    rows = result.get('rows', [])
    if any(not isinstance(row, dict) or set(row) != set(columns) for row in rows):
        return None
    return [[row[label] for label in labels] for row in rows]


def now():
    return datetime.now(timezone.utc).isoformat()
