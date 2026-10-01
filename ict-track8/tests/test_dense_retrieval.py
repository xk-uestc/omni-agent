import pytest

from backend.dense_retrieval import DenseRetrievalError
from backend.knowledge_store import KnowledgeStore


class CountingEmbedder:
    identity = 'test-model@1'

    def __init__(self):
        self.documents = 0

    def embed(self, texts, *, query=False):
        if not query:
            self.documents += len(texts)
        return [[1.0, 0.0] if '保修' in text or query else [0.0, 1.0] for text in texts]


def test_dense_cache_invalidated_by_content_and_model(tmp_path):
    embedder = CountingEmbedder()
    store = KnowledgeStore(tmp_path, embedder=embedder)
    store.ingest('硬件保修12个月'.encode(), document_id='policy', title='保修', modality='txt', filename='p.txt')
    store.search('多长时间能免费维修')
    assert embedder.documents == 1
    store.search('多久能维修')
    assert embedder.documents == 1
    store.ingest('硬件保修24个月'.encode(), document_id='policy', title='保修', modality='txt', filename='p.txt')
    assert '24个月' in store.answer('多久能维修')['answer']
    assert embedder.documents == 2
    embedder.identity = 'test-model@2'
    store.search('多久能维修')
    assert embedder.documents == 3


def test_zero_vectors_are_not_silent_bm25_success(tmp_path):
    embedder = CountingEmbedder()
    embedder.embed = lambda texts, **kwargs: [[0, 0] for _ in texts]
    store = KnowledgeStore(tmp_path, embedder=embedder)
    store.ingest(b'policy', document_id='p', title='p', modality='txt', filename='p.txt')
    with pytest.raises(DenseRetrievalError, match='零向量'):
        store.answer('policy')


def test_dimension_mismatch_rejected(tmp_path):
    embedder = CountingEmbedder()
    embedder.embed = lambda texts, query=False: [[1, 0, 0] if query else [1, 0] for _ in texts]
    store = KnowledgeStore(tmp_path, embedder=embedder)
    store.ingest(b'policy', document_id='p', title='p', modality='txt', filename='p.txt')
    with pytest.raises(DenseRetrievalError, match='维度'):
        store.search('policy')
