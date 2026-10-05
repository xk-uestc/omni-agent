"""Complete, version-pinned native-grid page routing index.

This index contains labels and cell identities, never answer values. Only a
fully completed extraction is persisted. The winning page is freshly extracted
before rendering/answering. Native-grid absence is not OCR/visual absence.
The caller owns the shared visual work slot; this module never acquires it.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

import fitz

from .visual_tables import extract_pdf_tables, VisualTableError
from .visual_work_budget import VisualWorkBusy


INDEX_SCHEMA = 'complete-native-grid-routing-v1'
# Acceptance/projection changes invalidate persisted registries even when the
# dependency version and human-maintained schema label remain unchanged.
ALGORITHM_SHA256 = hashlib.sha256(b'\0'.join(
    Path(__file__).with_name(filename).read_bytes()
    for filename in ('pdf_page_index.py', 'visual_tables.py', 'visual_table_reader.py'))).hexdigest()
EXTRACTOR_VERSION = f'PyMuPDF:{fitz.VersionBind}:lines_strict:unrotated:algorithm:{ALGORITHM_SHA256}'
MAX_INDEX_BYTES = 16 * 1024 * 1024
MAX_CACHE_BYTES = 128 * 1024 * 1024
MAX_CACHE_FILES = 128
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_PAGES = 1000
MAX_BUILD_SECONDS = 60
_INDEX_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_BUILD_LOCKS = {}
MAX_ACTIVE_BUILD_KEYS = 16
MAX_BUILD_WAIT_SECONDS = 60


@contextmanager
def _source_build(path):
    """Bounded single-flight per cache identity; unrelated PDFs build in parallel."""
    key = str(path.resolve())
    with _INDEX_LOCK:
        entry = _BUILD_LOCKS.get(key)
        if entry is None:
            if len(_BUILD_LOCKS) >= MAX_ACTIVE_BUILD_KEYS:
                raise VisualWorkBusy('PDF索引构建任务已满，请稍后重试')
            entry = [threading.Lock(), 0]
            _BUILD_LOCKS[key] = entry
        entry[1] += 1
    acquired = False
    try:
        acquired = entry[0].acquire(timeout=MAX_BUILD_WAIT_SECONDS)
        if not acquired:
            raise VisualWorkBusy('等待同一PDF的完整页索引超时，请稍后重试')
        yield
    finally:
        if acquired:
            entry[0].release()
        with _INDEX_LOCK:
            entry[1] -= 1
            if entry[1] == 0:
                del _BUILD_LOCKS[key]


class PageIndexError(ValueError):
    pass


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def routing_page(manifest):
    """Retain exactly the binder's literal key registry, not numeric evidence."""
    return {'page_no': manifest['page_no'], 'source_sha256': manifest['source_sha256'],
            'native_text_present': bool(manifest['native_words']),
            'rejected_table_count': len(manifest['rejected_tables']),
            'tables': [{'table_id': table['table_id'], 'row_headers': table['row_headers'],
                        'column_header_paths': table['column_header_paths'],
                        'facts': [{'fact_id': fact['fact_id'], 'fact_key': fact['fact_key']}
                                  for fact in table['facts']]} for table in manifest['tables']]}


def _validate(payload, source_sha256, source_bytes, page_count):
    if (not isinstance(payload, dict) or payload.get('schema_version') != INDEX_SCHEMA
            or payload.get('extractor_version') != EXTRACTOR_VERSION
            or payload.get('source_sha256') != source_sha256
            or payload.get('source_bytes') != source_bytes
            or payload.get('page_count') != page_count
            or payload.get('complete') is not True):
        raise PageIndexError('page_index_version_or_coverage_mismatch')
    pages = payload.get('pages')
    if not isinstance(pages, list) or len(pages) != page_count:
        raise PageIndexError('page_index_incomplete_coverage')
    for number, page in enumerate(pages, 1):
        if (not isinstance(page, dict) or type(page.get('page_no')) is not int
                or page['page_no'] != number or page.get('source_sha256') != source_sha256
                or type(page.get('native_text_present')) is not bool
                or type(page.get('rejected_table_count')) is not int
                or page['rejected_table_count'] < 0 or not isinstance(page.get('tables'), list)
                or len(page['tables']) > 32):
            raise PageIndexError('page_index_invalid_page')
        for table in page['tables']:
            if (not isinstance(table, dict) or not isinstance(table.get('table_id'), str)
                    or not isinstance(table.get('row_headers'), list)
                    or not isinstance(table.get('column_header_paths'), list)
                    or not isinstance(table.get('facts'), list) or len(table['facts']) > 4000
                    or any(not isinstance(label, str) or not label for label in table['row_headers'])
                    or any(not isinstance(path, list) or not path or any(not isinstance(label, str) or not label for label in path)
                           for path in table['column_header_paths'])):
                raise PageIndexError('page_index_invalid_table')
            seen = set()
            for fact in table['facts']:
                key = fact.get('fact_key') if isinstance(fact, dict) else None
                if (not isinstance(fact, dict) or not isinstance(fact.get('fact_id'), str)
                        or fact['fact_id'] in seen or not isinstance(key, dict)
                        or key.get('table_id') != table['table_id']
                        or type(key.get('row_index')) is not int
                        or not 1 <= key['row_index'] <= len(table['row_headers'])
                        or type(key.get('column_index')) is not int
                        or not 1 <= key['column_index'] < len(table['column_header_paths'])
                        or key.get('row_header') != table['row_headers'][key['row_index'] - 1]
                        or key.get('column_header_path') != table['column_header_paths'][key['column_index']]
                        or fact['fact_id'] != f'{table["table_id"]}:r:{key["row_index"]}:c:{key["column_index"]}'):
                    raise PageIndexError('page_index_invalid_fact')
                seen.add(fact['fact_id'])
    return payload


def _cache_path(root, source_sha256):
    identity = hashlib.sha256(_encoded([INDEX_SCHEMA, EXTRACTOR_VERSION, source_sha256])).hexdigest()
    return root / (identity + '.json')


def _load(path, source_sha256, source_bytes, page_count):
    try:
        if path.is_symlink() or path.stat().st_size > MAX_INDEX_BYTES:
            return None
        wrapper = json.loads(path.read_bytes())
        payload = wrapper['payload']
        if wrapper['payload_sha256'] != hashlib.sha256(_encoded(payload)).hexdigest():
            return None
        return _validate(payload, source_sha256, source_bytes, page_count)
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return None


def _persist(root, path, payload):
    encoded = _encoded({'payload': payload, 'payload_sha256': hashlib.sha256(_encoded(payload)).hexdigest()})
    if len(encoded) > MAX_INDEX_BYTES:
        raise PageIndexError('page_index_byte_budget_exceeded')
    # This module only manages its own hash-named cache files. Source files and
    # other runtime artifacts are never candidates for cache eviction.
    entries = []
    for candidate in root.iterdir():
        if re.fullmatch(r'[a-f0-9]{64}\.json', candidate.name) and not candidate.is_symlink():
            stat = candidate.stat()
            entries.append((stat.st_mtime_ns, candidate, stat.st_size))
    entries.sort(key=lambda item: item[0])
    total = sum(item[2] for item in entries)
    while entries and (total + len(encoded) > MAX_CACHE_BYTES or len(entries) >= MAX_CACHE_FILES):
        _, candidate, size = entries.pop(0)
        candidate.unlink()
        total -= size
    temp = root / ('.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def complete_pdf_page_index(root, pdf_bytes, *, expected_source_sha256):
    """Return complete native-grid routing coverage or raise, never a prefix.

    Source page count comes from the actual pinned PDF, not ingestion stats.
    A bounded per-source build lock reuses concurrent requests for the same PDF.
    Cache checksums detect corruption, not a hostile writer controlling disk.
    """
    if (not isinstance(pdf_bytes, bytes) or not pdf_bytes or len(pdf_bytes) > MAX_SOURCE_BYTES
            or not isinstance(expected_source_sha256, str)
            or not re.fullmatch(r'[a-f0-9]{64}', expected_source_sha256)
            or hashlib.sha256(pdf_bytes).hexdigest() != expected_source_sha256):
        raise PageIndexError('page_index_source_invalid_or_over_budget')
    try:
        with fitz.open(stream=pdf_bytes, filetype='pdf') as document:
            if document.needs_pass or not 1 <= len(document) <= MAX_PAGES:
                raise PageIndexError('page_index_pdf_page_budget_or_encryption')
            page_count = len(document)
    except PageIndexError:
        raise
    except Exception as exc:
        raise PageIndexError('page_index_pdf_unreadable') from exc
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise PageIndexError('page_index_cache_symlink')
    path = _cache_path(root, expected_source_sha256)
    cached = _load(path, expected_source_sha256, len(pdf_bytes), page_count)
    if cached is not None:
        return cached, 'cache_hit'
    with _source_build(path):
        cached = _load(path, expected_source_sha256, len(pdf_bytes), page_count)
        if cached is not None:
            return cached, 'cache_hit'
        payload = {'schema_version': INDEX_SCHEMA, 'extractor_version': EXTRACTOR_VERSION,
                   'source_sha256': expected_source_sha256, 'source_bytes': len(pdf_bytes),
                   'page_count': page_count, 'complete': True, 'pages': []}
        started, bytes_used = time.monotonic(), 0
        for page_no in range(1, page_count + 1):
            if time.monotonic() - started > MAX_BUILD_SECONDS:
                raise PageIndexError('page_index_build_time_budget_exceeded')
            try:
                manifest = extract_pdf_tables(pdf_bytes, page_no=page_no,
                                              expected_source_sha256=expected_source_sha256)
            except VisualTableError as exc:
                raise PageIndexError('page_index_page_extraction_failed') from exc
            page = routing_page(manifest)
            bytes_used += len(_encoded(page))
            if bytes_used > MAX_INDEX_BYTES - 4096:
                raise PageIndexError('page_index_byte_budget_exceeded')
            payload['pages'].append(page)
        if time.monotonic() - started > MAX_BUILD_SECONDS:
            raise PageIndexError('page_index_build_time_budget_exceeded')
        _validate(payload, expected_source_sha256, len(pdf_bytes), page_count)
        try:
            with _CACHE_LOCK:
                _persist(root, path, payload)
        except OSError as exc:
            raise PageIndexError('page_index_cache_write_failed') from exc
        return payload, 'built_complete'
