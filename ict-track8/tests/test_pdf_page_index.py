"""Persistence must preserve complete candidate coverage and fail closed."""
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import fitz
import pytest

from backend import pdf_page_index as module
from backend.visual_work_budget import VisualWorkBusy
from tests.test_visual_table_reader import grid_pdf


def source(pages=2):
    with fitz.open() as document, fitz.open(stream=grid_pdf(), filetype='pdf') as grid:
        for _ in range(pages - 1):
            document.new_page().insert_text((40, 40), 'Narrative page without a grid.')
        document.insert_pdf(grid)
        raw = document.tobytes()
    return raw, hashlib.sha256(raw).hexdigest()


def test_reuses_persisted_complete_registry_without_reextraction(tmp_path, monkeypatch):
    raw, digest = source(14)
    index, status = module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    assert status == 'built_complete' and len(index['pages']) == 14 and index['complete']
    assert index['pages'][13]['tables'] and not index['pages'][0]['tables']
    assert 'raw_value' not in json.dumps(index) and 'native_words' not in json.dumps(index)
    monkeypatch.setattr(module, 'extract_pdf_tables', lambda *a, **kw: pytest.fail('cache re-extracted pages'))
    cached, status = module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    assert status == 'cache_hit' and cached == index


@pytest.mark.parametrize('damage', ['checksum', 'coverage', 'version', 'source', 'page_number', 'facts'])
def test_corrupt_or_stale_cache_rebuilt_from_pinned_source(tmp_path, damage):
    raw, digest = source()
    original, _ = module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    path = next(tmp_path.glob('*.json'))
    wrapper = json.loads(path.read_bytes())
    if damage == 'checksum':
        wrapper['payload_sha256'] = '0' * 64
    else:
        payload = wrapper['payload']
        if damage == 'coverage':
            payload['pages'].pop()
        elif damage == 'version':
            payload['extractor_version'] = 'old-extractor'
        elif damage == 'source':
            payload['source_sha256'] = '0' * 64
        elif damage == 'page_number':
            payload['pages'][1]['page_no'] = 1
        elif damage == 'facts':
            payload['pages'][1]['tables'][0]['facts'][0]['fact_key']['row_header'] = 'Not a row'
        wrapper['payload_sha256'] = hashlib.sha256(module._encoded(payload)).hexdigest()
    path.write_text(json.dumps(wrapper), encoding='utf-8')
    rebuilt, status = module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    assert status == 'built_complete' and rebuilt == original


def test_failed_page_build_never_persists_partial_index(tmp_path, monkeypatch):
    raw, digest = source(3)
    extract = module.extract_pdf_tables
    def fail_second(*args, **kwargs):
        if kwargs['page_no'] == 2:
            raise module.VisualTableError('page2 invalid')
        return extract(*args, **kwargs)
    monkeypatch.setattr(module, 'extract_pdf_tables', fail_second)
    with pytest.raises(module.PageIndexError, match='extraction_failed'):
        module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    assert not list(tmp_path.iterdir())


def test_build_budget_failure_no_partial_cache(tmp_path, monkeypatch):
    raw, digest = source()
    monkeypatch.setattr(module, 'MAX_BUILD_SECONDS', -1)
    with pytest.raises(module.PageIndexError, match='time_budget'):
        module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    assert not list(tmp_path.iterdir())


def test_build_key_budget_and_release_on_failure(tmp_path, monkeypatch):
    raw, digest = source()
    monkeypatch.setattr(module, 'MAX_ACTIVE_BUILD_KEYS', 0)
    with pytest.raises(VisualWorkBusy):
        module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    monkeypatch.setattr(module, 'MAX_ACTIVE_BUILD_KEYS', 16)
    monkeypatch.setattr(module, 'MAX_BUILD_SECONDS', -1)
    with pytest.raises(module.PageIndexError):
        module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    assert not module._BUILD_LOCKS


def test_different_cold_sources_build_concurrently_with_complete_coverage(tmp_path, monkeypatch):
    sources = [source(2), source(3)]
    rendezvous = threading.Barrier(2)
    extract = module.extract_pdf_tables
    def synchronized(*args, **kwargs):
        if kwargs['page_no'] == 1:
            rendezvous.wait(timeout=10)
        return extract(*args, **kwargs)
    monkeypatch.setattr(module, 'extract_pdf_tables', synchronized)
    def build(item):
        raw, digest = item
        return module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(build, sources))
    assert [len(payload['pages']) for payload, status in results] == [2, 3]
    assert all(status == 'built_complete' and payload['complete'] for payload, status in results)
    assert len(list(tmp_path.glob('*.json'))) == 2
    assert not list(tmp_path.glob('*.tmp')) and not module._BUILD_LOCKS


def test_same_cold_source_reuses_one_build(tmp_path, monkeypatch):
    raw, digest = source(3)
    started, release = threading.Event(), threading.Event()
    extract = module.extract_pdf_tables
    calls = []
    def synchronized(*args, **kwargs):
        calls.append(kwargs['page_no'])
        if kwargs['page_no'] == 1:
            started.set()
            assert release.wait(timeout=10)
        return extract(*args, **kwargs)
    monkeypatch.setattr(module, 'extract_pdf_tables', synchronized)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(module.complete_pdf_page_index, tmp_path, raw, expected_source_sha256=digest)
        assert started.wait(timeout=10)
        second = pool.submit(module.complete_pdf_page_index, tmp_path, raw, expected_source_sha256=digest)
        release.set()
        a, b = first.result(timeout=15), second.result(timeout=15)
    assert a[0] == b[0] and {a[1], b[1]} == {'built_complete', 'cache_hit'}
    assert calls == [1, 2, 3] and not module._BUILD_LOCKS


def test_same_source_wait_timeout_does_not_leak_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'MAX_BUILD_WAIT_SECONDS', 0)
    path = tmp_path / 'identity.json'
    with module._source_build(path):
        with pytest.raises(VisualWorkBusy, match='超时'):
            with module._source_build(path):
                pytest.fail('contended build entered')
        assert len(module._BUILD_LOCKS) == 1
    assert not module._BUILD_LOCKS


def test_invalid_source_or_source_budget_fails_before_cache(tmp_path, monkeypatch):
    raw, digest = source()
    with pytest.raises(module.PageIndexError, match='source_invalid'):
        module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256='0' * 64)
    monkeypatch.setattr(module, 'MAX_SOURCE_BYTES', len(raw) - 1)
    with pytest.raises(module.PageIndexError, match='over_budget'):
        module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    assert not list(tmp_path.iterdir())


def test_algorithm_signature_changes_cache_identity_and_rebuilds(tmp_path, monkeypatch):
    raw, digest = source()
    old, _ = module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    monkeypatch.setattr(module, 'EXTRACTOR_VERSION', module.EXTRACTOR_VERSION + ':changed-acceptance')
    new, status = module.complete_pdf_page_index(tmp_path, raw, expected_source_sha256=digest)
    assert status == 'built_complete' and new['extractor_version'] != old['extractor_version']
    assert len(list(tmp_path.glob('*.json'))) == 2
    assert new['pages'] == old['pages']
