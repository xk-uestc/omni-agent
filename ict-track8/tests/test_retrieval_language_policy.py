"""An unsupported encoder language cannot displace literal source evidence."""
from backend.knowledge_store import KnowledgeStore


class ChineseEncoder:
    identity = 'test-chinese@1'
    supported_query_languages = ('zh',)

    def __init__(self):
        self.calls = 0

    def embed(self, texts, *, query=False):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


def test_english_uses_supported_lexical_channel_with_explicit_audit(tmp_path):
    encoder = ChineseEncoder()
    store = KnowledgeStore(tmp_path, embedder=encoder)
    store.ingest(b'System evacuation interval is twenty minutes.', document_id='a', title='Evacuation', modality='txt', filename='a.txt')
    result = store.search('What is the system evacuation interval?')
    assert result and encoder.calls == 0
    assert result[0].metadata['retrieval_language_policy']['strategy'] == 'lexical_unsupported_encoder_language'
    assert result[0].metadata['retrieval_channel'] == 'bm25'


def test_chinese_and_supported_multilingual_queries_keep_dense(tmp_path):
    for language, question in [(('zh',), '系统撤离需要多久？'), (('zh','en'), 'What is the system evacuation interval?')]:
        encoder = ChineseEncoder()
        encoder.supported_query_languages = language
        store = KnowledgeStore(tmp_path / ('-'.join(language)), embedder=encoder)
        store.ingest('系统撤离需要20分钟。 System evacuation interval is twenty minutes.'.encode(),
            document_id='a', title='Evacuation', modality='txt', filename='a.txt')
        result = store.search(question)
        assert result and encoder.calls > 0
        assert result[0].metadata['retrieval_channel'] == 'hybrid'


def test_fallback_keeps_explicit_source_and_exact_identifier_constraints(tmp_path):
    encoder = ChineseEncoder()
    store = KnowledgeStore(tmp_path, embedder=encoder)
    for identifier in ('UNIT7', 'UNIT8'):
        store.ingest(f'{identifier} system evacuation interval is twenty minutes.'.encode(),
            document_id=identifier, title=identifier, modality='txt', filename=identifier+'.txt')
    hits = store.search('What is the evacuation interval for UNIT7?', document_id='UNIT8')
    assert hits == [] and encoder.calls == 0
