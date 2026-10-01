import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from backend.knowledge_store import KnowledgeStore


def test_corpus_persists_original_chunks_and_replacement(tmp_path):
    store = KnowledgeStore(tmp_path)
    raw = '退货申请应在签收后7日内提交。'.encode()
    report = store.ingest(raw, document_id='return-policy', title='退货政策', modality='txt', filename='policy.txt')
    reopened = KnowledgeStore(tmp_path)
    assert reopened.list_documents()[0]['sha256'] == hashlib.sha256(raw).hexdigest()
    assert reopened.original('return-policy')[0].read_bytes() == raw
    result = reopened.answer('退货申请期限')
    assert result['status'] == 'ok' and '7日' in result['answer']
    assert result['citations'][0]['metadata']['source_locator']
    store.ingest('退货期限调整为9日。'.encode(), document_id='return-policy', title='退货政策', modality='txt', filename='policy.txt')
    assert len(store.list_documents()) == 1
    assert '7日' not in str(store.document('return-policy')['chunks'])


def test_image_without_ocr_is_not_answer_evidence(tmp_path):
    from PIL import Image
    from io import BytesIO
    stream = BytesIO()
    Image.new('RGB', (100, 100), 'white').save(stream, format='PNG')
    store = KnowledgeStore(tmp_path)
    store.ingest(stream.getvalue(), document_id='scan', title='保修规则', modality='image', filename='scan.png')
    assert store.records() == []
    assert store.answer('保修规则')['status'] == 'insufficient_evidence'


@pytest.mark.parametrize('document_id', ['../escape', 'a/b', '..', '/absolute', 'a\\b'])
def test_document_id_cannot_escape_corpus(tmp_path, document_id):
    with pytest.raises(ValueError):
        KnowledgeStore(tmp_path).ingest(b'text', document_id=document_id, title='title', modality='txt', filename='file.txt')


def test_original_tampering_is_detected(tmp_path):
    store = KnowledgeStore(tmp_path)
    store.ingest(b'original', document_id='doc', title='title', modality='txt', filename='test.txt')
    path, _ = store.original('doc')
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='完整性'):
        store.original('doc')


def test_api_ingest_query_and_original_roundtrip(tmp_path, monkeypatch):
    import backend.app as app_module
    monkeypatch.setattr(app_module, 'knowledge_store', KnowledgeStore(tmp_path))
    client = TestClient(app_module.app)
    raw = '标准硬件保修期为12个月。'.encode()
    response = client.post('/api/v1/knowledge/ingest', json={'document_id': 'warranty', 'title': '保修规则', 'modality': 'txt', 'filename': 'warranty.txt', 'file_base64': base64.b64encode(raw).decode()})
    assert response.status_code == 200
    answer = client.post('/api/v1/knowledge/query', json={'question': '硬件保修期'})
    assert answer.status_code == 200 and '12个月' in answer.json()['answer']
    assert client.get('/api/v1/knowledge/documents/warranty/original').content == raw
    assert client.get('/api/v1/knowledge/documents/unknown').status_code == 404


def test_formula_api_refuses_missing_parameter(tmp_path):
    import backend.app as app_module
    client = TestClient(app_module.app)
    response = client.post('/api/v1/documents/formulas/calculate', json={'expression': '销售额 / 订单数', 'parameters': {}, 'formula_source': 'document://policy', 'formula_locator': 'page:1'})
    assert response.status_code == 422
