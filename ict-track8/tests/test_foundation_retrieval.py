from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from backend.knowledge_store import KnowledgeStore, SourceRevisionError, SourceIntegrityError
from backend.dense_retrieval import DenseRetrievalError


class Embedder:
    identity = 'test-model@1'
    def __init__(self): self.calls = 0
    def embed(self, texts, *, query=False):
        self.calls += int(not query)*len(texts)
        return [[1., .2] for _ in texts]


def put(store, did, text, title='record'):
    return store.ingest(text.encode(), document_id=did, title=title, modality='txt', filename='r.txt')


def test_scoped_query_does_not_decode_unrelated_corrupt_payload(tmp_path):
    s = KnowledgeStore(tmp_path); put(s, 'good', 'The warranty is 18 months.'); put(s, 'other', 'noise')
    with s.connect() as c: c.execute("UPDATE chunks SET payload='broken' WHERE document_id='other'")
    assert s.search('warranty', document_id='good')
    with pytest.raises(json.JSONDecodeError): s.records()


def test_logical_replacement_delete_and_restart_preserve_revision_guards(tmp_path):
    s = KnowledgeStore(tmp_path); first = put(s, 'p', 'warranty 12 months')
    second = put(s, 'p', 'warranty 24 months')
    with pytest.raises(SourceRevisionError): s.delete('p', expected_sha256=first['sha256'])
    assert '24' in KnowledgeStore(tmp_path).search('warranty')[0].snippet
    s.delete('p', expected_sha256=second['sha256'])
    assert KnowledgeStore(tmp_path).search('warranty') == []
    with pytest.raises(SourceRevisionError): s.verify_source('p', expected_sha256=second['sha256'])
    assert (s.assets/first['asset']).exists() and (s.assets/second['asset']).exists()


def test_dense_scoped_reads_ignore_unrelated_corrupt_cache_but_validate_requested(tmp_path):
    e = Embedder(); s = KnowledgeStore(tmp_path, embedder=e); put(s, 'p', '保修12个月')
    s.search('保修', document_id='p'); assert e.calls == 1
    with s.connect() as c: c.execute("INSERT INTO vectors VALUES ('unrelated-old-version','broken')")
    s.search('保修', document_id='p'); assert e.calls == 1
    with s.connect() as c: c.execute("UPDATE vectors SET payload='broken' WHERE cache_key!='unrelated-old-version'")
    with pytest.raises(DenseRetrievalError, match='损坏'): s.search('保修', document_id='p')


def test_concurrent_replacement_and_retrieval_emit_consistent_chunk_versions(tmp_path):
    s = KnowledgeStore(tmp_path); put(s, 'p', '保修12个月')
    def read(_):
        hits = s.search('保修', document_id='p')
        assert len({h.metadata['source_sha256'] for h in hits}) <= 1
        return bool(hits)
    def update():
        for i in range(6): put(s, 'p', f'保修{12+i}个月')
    with ThreadPoolExecutor(max_workers=5) as pool:
        future = pool.submit(update)
        assert all(pool.map(read, range(30))); future.result()
    path, _ = s.original('p'); path.write_bytes(b'changed')
    with pytest.raises(SourceIntegrityError): s.answer('保修', document_id='p')
