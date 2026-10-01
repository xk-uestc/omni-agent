"""Bounded, immutable complete SQLite results, separate from <=100-row previews.

Only reaching the cursor's EOF certifies completeness. Artifacts use ordered
cell arrays, preserving duplicate column labels, NULLs and duplicate rows.
Every read verifies SQL/parameter, source/WAL and producer-code bindings.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Sequence
import uuid

from .security import SqlSafetyError, _authorizer, validate_read_only_sql


class ResultRevisionError(SqlSafetyError):
    pass


@dataclass(frozen=True)
class ResultBudgets:
    max_rows: int = 25_000
    max_bytes: int = 8 * 1024 * 1024
    max_seconds: float = 5.0
    max_steps: int = 50_000_000
    max_source_bytes: int = 256 * 1024 * 1024

    def __post_init__(self):
        if (type(self.max_rows) is not int or not 1 <= self.max_rows <= 100_000
                or type(self.max_bytes) is not int or not 128 <= self.max_bytes <= 32 * 1024 * 1024
                or not 0.01 <= self.max_seconds <= 120
                or type(self.max_steps) is not int or not 1 <= self.max_steps <= 500_000_000
                or type(self.max_source_bytes) is not int or not 1024 <= self.max_source_bytes <= 1024**3):
            raise ValueError('invalid_complete_result_budgets')


@dataclass(frozen=True)
class CompleteExecution:
    columns: tuple[str, ...]
    preview_rows: tuple[dict[str, Any], ...]
    preview_cells: tuple[tuple[Any, ...], ...]
    metadata: dict[str, Any]


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _check_deadline(deadline: float | None):
    if deadline is not None and time.monotonic() > deadline:
        raise SqlSafetyError('complete_result_operation_time_budget_exceeded')


def _cell(value):
    if isinstance(value, bytes):
        return {'$sqlite_type': 'blob', 'base64': base64.b64encode(value).decode('ascii')}
    if value is None or type(value) in (str, int, float):
        return value
    raise SqlSafetyError('unsupported_sqlite_result_cell')


def _file_state(path: Path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _artifact_member(directory: Path, name: str) -> Path:
    """Opaque leaf names only; foreign symlinks/junction targets fail closed."""
    member = directory / name
    if member.is_symlink() or member.resolve().parent != directory:
        raise ResultRevisionError('result_artifact_target_outside_directory')
    return member


def _protect_private_path(path: Path, *, directory: bool = False):
    if os.name != 'nt':
        path.chmod(0o700 if directory else 0o600)
        return
    # The file/directory owner and SYSTEM only; protected DACL disables broad
    # inherited workspace permissions. This needs ownership, not elevation.
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    descriptor = ctypes.c_void_p()
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    convert.restype = wintypes.BOOL
    set_security = advapi.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_security.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not convert('D:P(A;;FA;;;OW)(A;;FA;;;SY)', 1, ctypes.byref(descriptor), None):
        raise ResultRevisionError('result_receipt_private_acl_creation_failed')
    try:
        if not set_security(str(path), 0x00000004 | 0x80000000, descriptor):
            raise ResultRevisionError('result_receipt_private_acl_creation_failed')
    finally:
        kernel.LocalFree(descriptor)


def _receipt_key(artifact_dir: Path, *, create: bool = False) -> bytes:
    private = artifact_dir.parent / '.query-result-private'
    if private.is_symlink() or private.resolve().parent != artifact_dir.parent:
        raise ResultRevisionError('result_receipt_private_path_invalid')
    path = private / 'receipt-signing.key'
    if create:
        private.mkdir(mode=0o700, exist_ok=True)
        _protect_private_path(private, directory=True)
        path = _artifact_member(private.resolve(), 'receipt-signing.key')
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(os.urandom(32))
                stream.flush()
                os.fsync(stream.fileno())
            _protect_private_path(path)
    path = _artifact_member(private.resolve(), 'receipt-signing.key')
    try:
        with path.open('rb') as stream:
            key = stream.read(33)
    except FileNotFoundError as exc:
        raise ResultRevisionError('result_receipt_private_key_missing') from exc
    if len(key) != 32:
        raise ResultRevisionError('result_receipt_private_key_invalid')
    if os.name != 'nt' and (path.stat().st_mode & 0o077 or private.stat().st_mode & 0o077):
        raise ResultRevisionError('result_receipt_private_permissions_invalid')
    return key


def _receipt_message(metadata: dict, binding: str) -> bytes:
    return _json({'artifact_id': metadata['artifact_id'], 'binding_sha256': binding,
                  'query_sha256': metadata['query_sha256'],
                  'source_revision': metadata['source_revision'],
                  'producer_sha256': metadata['producer_sha256'],
                  'receipt_key_id': metadata['receipt_key_id']})


def _hash_file(path: Path, *, max_bytes: int, deadline: float):
    _check_deadline(deadline)
    before = _file_state(path)
    if before[2] > max_bytes:
        raise SqlSafetyError('complete_result_source_byte_budget_exceeded')
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            if time.monotonic() > deadline:
                raise SqlSafetyError('complete_result_source_time_budget_exceeded')
            size += len(chunk)
            if size > max_bytes:
                raise SqlSafetyError('complete_result_source_byte_budget_exceeded')
            digest.update(chunk)
    after = _file_state(path)
    if before != after or size != after[2]:
        raise ResultRevisionError('complete_result_source_changed_while_pinning')
    _check_deadline(deadline)
    return {'sha256': digest.hexdigest(), 'bytes': size, 'generation': list(after)}


def pin_database(path: Path, budgets: ResultBudgets, *, generation: Callable | None = None,
                 expected_generation=None, deadline: float | None = None) -> dict:
    deadline = time.monotonic() + budgets.max_seconds if deadline is None else deadline
    _check_deadline(deadline)
    if generation is not None and generation() != expected_generation:
        raise ResultRevisionError('complete_result_source_snapshot_changed')
    _check_deadline(deadline)
    files = {}
    remaining = budgets.max_source_bytes
    for suffix in ('', '-wal'):
        member = Path(str(path.resolve()) + suffix)
        if not member.exists() and suffix:
            continue
        state = _hash_file(member, max_bytes=remaining, deadline=deadline)
        remaining -= state['bytes']
        files['wal' if suffix else 'database'] = state
    if generation is not None and generation() != expected_generation:
        raise ResultRevisionError('complete_result_source_snapshot_changed')
    return {'files': files, 'sha256': _sha(_json(files)),
            'kind': 'database_and_uncheckpointed_wal_content_hash_and_generation'}


def producer_paths() -> tuple[Path, ...]:
    root = Path(__file__).resolve().parent
    return tuple(root / name for name in (
        'result_artifact.py', 'result_scope.py', 'security.py', 'engine.py',
        'planner.py', 'metric_compiler.py', 'models.py'))


def pin_producer(paths: Sequence[Path] | None = None, *, deadline: float | None = None) -> str:
    members = paths if paths is not None else producer_paths()
    result = []
    for path in members:
        _check_deadline(deadline)
        result.append((str(path.resolve()), _sha(path.read_bytes())))
    _check_deadline(deadline)
    return _sha(_json(result))


def query_hash(sql: str, parameters: Sequence[Any]) -> str:
    return _sha(_json({'sql': sql, 'parameters': [_cell(value) for value in parameters]}))


def execute_complete_read_only(
    connection: sqlite3.Connection, sql: str, parameters: Sequence[Any] = (), *,
    database_path: Path, artifact_dir: Path, budgets: ResultBudgets | None = None,
    preview_limit: int = 100, expected_source: dict | None = None,
    generation: Callable | None = None, expected_generation=None,
    source_code_paths: Sequence[Path] | None = None, scope: dict | None = None,
) -> CompleteExecution:
    budgets = budgets or ResultBudgets()
    deadline = time.monotonic() + budgets.max_seconds
    if type(preview_limit) is not int or not 1 <= preview_limit <= 100:
        raise ValueError('preview_limit_must_be_between_1_and_100')
    validated = validate_read_only_sql(sql, max_rows=100)
    parameters = tuple(parameters)
    source = pin_database(database_path, budgets, generation=generation,
                          expected_generation=expected_generation, deadline=deadline)
    if expected_source is not None and expected_source != source:
        raise ResultRevisionError('complete_result_source_changed_after_planning')
    producer = pin_producer(source_code_paths, deadline=deadline)
    binding = query_hash(validated.sql, parameters)
    artifact_dir = artifact_dir.resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_id = uuid.uuid4().hex
    temporary = _artifact_member(artifact_dir, artifact_id + '.partial')
    columns, preview, preview_cells, count, total_bytes, steps = (), [], [], 0, 0, 0
    digest = hashlib.sha256()
    failure = None
    _check_deadline(deadline)

    def progress():
        nonlocal steps, failure
        steps += 1000
        if steps > budgets.max_steps:
            failure = 'complete_result_step_budget_exceeded'
            return 1
        if time.monotonic() > deadline:
            failure = 'complete_result_time_budget_exceeded'
            return 1
        return 0

    previous_length_limit = connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, budgets.max_bytes)
    connection.set_authorizer(_authorizer)
    connection.set_progress_handler(progress, 1000)
    try:
        cursor = connection.execute(validated.sql, parameters)
        columns = tuple(column[0] for column in cursor.description or ())
        with temporary.open('xb') as stream:
            while True:
                if time.monotonic() > deadline:
                    failure = 'complete_result_time_budget_exceeded'
                    break
                row = cursor.fetchone()
                if row is None:
                    break
                if count >= budgets.max_rows:
                    failure = 'complete_result_row_budget_exceeded'
                    break
                encoded = _json([_cell(value) for value in row]) + b'\n'
                if total_bytes + len(encoded) > budgets.max_bytes:
                    failure = 'complete_result_byte_budget_exceeded'
                    break
                stream.write(encoded)
                digest.update(encoded)
                total_bytes += len(encoded)
                count += 1
                if len(preview) < preview_limit:
                    cells = tuple(_cell(value) for value in row)
                    preview_cells.append(cells)
                    preview.append({column: cells[index] for index, column in enumerate(columns)})
    except sqlite3.DatabaseError as exc:
        if failure is None:
            failure = 'complete_result_sql_or_cell_budget_failed'
    except (TypeError, ValueError) as exc:
        failure = 'complete_result_cell_encoding_failed'
    finally:
        connection.set_progress_handler(None, 0)
        connection.set_authorizer(None)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, previous_length_limit)
    try:
        current = pin_database(database_path, budgets, generation=generation,
                               expected_generation=expected_generation, deadline=deadline)
        if current != source or pin_producer(source_code_paths, deadline=deadline) != producer:
            raise ResultRevisionError('complete_result_source_or_producer_changed')
        metadata = {'format': 'sqlite-ordered-cell-arrays-jsonl-v1',
            'status': 'partial' if failure else 'complete', 'reason': failure,
            'query_sha256': binding, 'source_revision': source['sha256'],
            'producer_sha256': producer, 'columns': list(columns),
            'row_count': None if failure else count, 'observed_rows': count,
            'preview_row_count': len(preview), 'preview_limit': preview_limit,
            'preview_truncated': failure is not None or count > len(preview),
            'scope': scope or {}, 'budgets': budgets.__dict__,
            'artifact_id': None if failure else artifact_id,
            'data_sha256': None if failure else digest.hexdigest(),
            'byte_count': total_bytes, 'cursor_eof_verified': failure is None,
            'source_pin': source}
        if not failure:
            # Metadata is published last: interrupted executions never leave
            # a discoverable complete artifact. No SQL/parameter values leak.
            data_path = _artifact_member(artifact_dir, artifact_id + '.jsonl')
            key = _receipt_key(artifact_dir, create=True)
            metadata['receipt_key_id'] = _sha(key)
            metadata['binding_sha256'] = _sha(_json(metadata))
            metadata['receipt_signature'] = hmac.new(key,
                _receipt_message(metadata, metadata['binding_sha256']), hashlib.sha256).hexdigest()
            _check_deadline(deadline)
            temporary.replace(data_path)
            with _artifact_member(artifact_dir, artifact_id + '.json').open('xb') as stream:
                stream.write(_json(metadata))
            try:
                _check_deadline(deadline)
            except SqlSafetyError:
                _artifact_member(artifact_dir, artifact_id + '.json').unlink(missing_ok=True)
                data_path.unlink(missing_ok=True)
                raise
        return CompleteExecution(columns, tuple(preview), tuple(preview_cells), metadata)
    finally:
        temporary.unlink(missing_ok=True)


def read_result_page(
    artifact_dir: Path, artifact_id: str, *, database_path: Path, offset: int = 0,
    page_size: int = 100, expected_query_sha256: str | None = None,
    expected_binding_sha256: str,
    source_code_paths: Sequence[Path] | None = None,
) -> dict:
    # The operation starts before metadata/key/producer/source reads. No
    # phase receives a fresh budget. Blocking OS calls are cooperative: when
    # they return after deadline the operation must fail, never claim complete.
    started = time.monotonic()
    if not isinstance(artifact_id, str) or not re.fullmatch(r'[0-9a-f]{32}', artifact_id):
        raise ValueError('invalid_result_artifact_id')
    if type(offset) is not int or offset < 0 or type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError('invalid_result_page')
    if (not isinstance(expected_binding_sha256, str)
            or not re.fullmatch(r'[0-9a-f]{64}', expected_binding_sha256)):
        raise ValueError('initial_result_binding_required')
    directory = artifact_dir.resolve()
    metadata_path = _artifact_member(directory, artifact_id + '.json')
    with metadata_path.open('rb') as stream:
        metadata_raw = stream.read(64 * 1024 + 1)
    if len(metadata_raw) > 64 * 1024:
        raise ResultRevisionError('result_artifact_metadata_too_large')
    metadata = json.loads(metadata_raw)
    if not isinstance(metadata, dict):
        raise ResultRevisionError('result_artifact_metadata_invalid')
    signature = metadata.pop('receipt_signature', None)
    binding = metadata.pop('binding_sha256', None)
    if (binding != expected_binding_sha256 or binding != _sha(_json(metadata)) or metadata['artifact_id'] != artifact_id
            or metadata['status'] != 'complete' or not metadata['cursor_eof_verified']
            or (expected_query_sha256 is not None and metadata['query_sha256'] != expected_query_sha256)):
        raise ResultRevisionError('result_artifact_binding_mismatch')
    key = _receipt_key(directory)
    if (metadata.get('receipt_key_id') != _sha(key) or not isinstance(signature, str)
            or not hmac.compare_digest(signature,
                hmac.new(key, _receipt_message(metadata, binding), hashlib.sha256).hexdigest())):
        raise ResultRevisionError('result_artifact_server_receipt_invalid')
    budgets = ResultBudgets(**metadata['budgets'])
    deadline = started + budgets.max_seconds
    _check_deadline(deadline)
    before = pin_database(database_path, budgets, deadline=deadline)
    producer = pin_producer(source_code_paths, deadline=deadline)
    if before != metadata['source_pin'] or producer != metadata['producer_sha256']:
        raise ResultRevisionError('result_artifact_source_or_producer_changed')
    data = _artifact_member(directory, artifact_id + '.jsonl')
    before_data = _file_state(data)
    digest, rows, count, size = hashlib.sha256(), [], 0, 0
    with data.open('rb') as stream:
        while line := stream.readline(budgets.max_bytes + 1):
            size += len(line)
            count += 1
            if (size > budgets.max_bytes or count > budgets.max_rows or time.monotonic() > deadline):
                raise ResultRevisionError('result_artifact_read_budget_exceeded')
            digest.update(line)
            if offset <= count - 1 < offset + page_size:
                cells = json.loads(line)
                if not isinstance(cells, list) or len(cells) != len(metadata['columns']):
                    raise ResultRevisionError('result_artifact_cell_contract_mismatch')
                rows.append(cells)
    if (digest.hexdigest() != metadata['data_sha256'] or size != metadata['byte_count']
            or count != metadata['row_count'] or before_data != _file_state(data)
            or metadata_path.read_bytes() != metadata_raw or pin_database(database_path, budgets, deadline=deadline) != before
            or pin_producer(source_code_paths, deadline=deadline) != producer):
        raise ResultRevisionError('result_artifact_data_or_source_changed')
    _check_deadline(deadline)
    return {'status': 'complete', 'artifact_id': artifact_id, 'columns': metadata['columns'],
            'rows': rows, 'offset': offset, 'page_size': page_size, 'row_count': count,
            'next_offset': offset + len(rows) if offset + len(rows) < count else None,
            'query_sha256': metadata['query_sha256'], 'source_revision': metadata['source_revision'],
            'data_sha256': metadata['data_sha256'], 'format': metadata['format']}
