import hashlib

import pytest
from fastapi.testclient import TestClient

from backend.document_analysis import DocumentAnalyzer
from backend.knowledge_store import KnowledgeStore
from backend.text_quality import repair_preview, simplify_for_retrieval, text_quality


def test_full_dictionary_conversion_and_context_preserve_numbers_names():
    text = '乾隆年間，緊急工單應在2小時內首次響應；金額為1,234.50元。'
    assert simplify_for_retrieval(text) == '乾隆年间，紧急工单应在2小时内首次响应；金额为1,234.50元。'
    assert simplify_for_retrieval(simplify_for_retrieval(text)) == simplify_for_retrieval(text)


def test_protected_code_url_and_unknown_ocr_numbers_not_rewritten():
    text = '政策為2小時。`訂單ID`\n```sql\nSELECT 銷售額\n```\nhttps://x.test/訂單\n保修期為l2個月，张叁。'
    result = simplify_for_retrieval(text)
    assert '`訂單ID`' in result and 'SELECT 銷售額' in result and 'https://x.test/訂單' in result
    assert 'l2个月，张叁' in result
    assert text_quality('`销受额` https://x.test/保修其')['typo_candidates'] == []


def test_edits_use_original_unicode_offsets_and_do_not_apply_implicitly():
    original = '🙂\n銷售额和销受额\n保修其为12个月。'
    report = text_quality(original, include_preview=True)
    assert report['typo_candidate_count'] == 2
    for item in report['typo_candidates']:
        assert original[item['start']:item['end']] == item['before']
        assert item['line_no'] in (2, 3)
    no_edits = repair_preview(original, source_sha256=report['source_sha256'], accepted_ids=[], simplify=False)
    assert no_edits['revised_text'] == original
    result = repair_preview(original, source_sha256=report['source_sha256'], accepted_ids=[report['typo_candidates'][1]['id']])
    assert '销受额' in result['revised_text'] and '保修期为12个月' in result['revised_text']
    assert result['original_preserved'] and result['status'] == 'preview_only'


def test_changed_source_forged_or_duplicate_selection_rejected():
    text = '销受额'
    report = text_quality(text)
    item = report['typo_candidates'][0]['id']
    with pytest.raises(ValueError, match='原文已变化'):
        repair_preview(text+'。', source_sha256=report['source_sha256'], accepted_ids=[item])
    for ids in (['unknown'], [item,item]):
        with pytest.raises(ValueError, match='校正选项'):
            repair_preview(text, source_sha256=report['source_sha256'], accepted_ids=ids)


def test_large_candidate_count_explicitly_bounded():
    report = text_quality('销受额\n'*150)
    assert report['typo_candidate_count'] == 150 and len(report['typo_candidates']) == 100
    assert report['candidates_truncated'] is True


def test_document_quality_emits_alerts_and_keeps_formula_source():
    source = '銷售額 = 120 * 3\n保修其為12個月。'
    report = DocumentAnalyzer().analyze(source).to_dict()
    assert {'traditional_variant_detected','possible_typo_requires_confirmation'} <= {item['code'] for item in report['issues']}
    assert report['formulas'][0]['label'] == '銷售額'


def test_ingest_typo_routes_to_review_without_rewriting(tmp_path):
    store = KnowledgeStore(tmp_path)
    raw = '保修其为12个月。'.encode()
    report = store.ingest(raw, document_id='typo', title='保修', modality='txt', filename='typo.txt')
    assert report['routing']['review_required'] is True
    assert store.document('typo')['chunks'][0]['text'] == raw.decode()


@pytest.mark.parametrize('source,question', [('緊急工單應在2小時內首次響應。','紧急工单首次响应'),
                                          ('紧急工单应在2小时内首次响应。','緊急工單首次響應')])
def test_cross_script_retrieval_returns_literal_untouched_source(tmp_path, source, question):
    store = KnowledgeStore(tmp_path)
    raw = source.encode()
    store.ingest(raw, document_id='policy', title='规则', modality='txt', filename='policy.txt')
    answer = store.answer(question)
    assert answer['status'] == 'ok'
    assert answer['citations'][0]['snippet'] in source
    assert store.original('policy')[0].read_bytes() == raw
    assert store.document('policy')['sha256'] == hashlib.sha256(raw).hexdigest()


def test_text_quality_api_preview_and_stale_guard():
    from backend.app import app
    client = TestClient(app)
    text = '緊急工單首欠响应時間為2小時。'
    report = client.post('/api/v1/documents/text-quality', json={'text':text}).json()
    payload = {'text':text,'source_sha256':report['source_sha256'],'accepted_ids':[item['id'] for item in report['typo_candidates']]}
    response = client.post('/api/v1/documents/text-repair', json=payload)
    assert response.status_code == 200
    assert response.json()['revised_text'] == '紧急工单首次响应时间为2小时。'
    assert client.post('/api/v1/documents/text-repair', json={**payload,'text':text+'。'}).status_code == 422
    assert client.post('/api/v1/documents/text-quality', json={'text':'x'*20001}).status_code == 422


def test_normalized_match_maps_back_to_relevant_literal_excerpt(tmp_path):
    from backend.cross_source import JsonDocumentRetriever
    source = '前言。'*100+'緊急工單應在2小時內首次響應。'
    excerpt = JsonDocumentRetriever._snippet(source, {'首次响应'})
    assert '首次響應' in excerpt and excerpt in source


def test_chinese_url_separator_does_not_hide_prose_and_traditional_typos():
    text = 'https://x.test/訂單，緊急工單首欠響應為2小時。'
    report = text_quality(text)
    assert report['typo_candidate_count'] == 1
    preview = repair_preview(text, source_sha256=report['source_sha256'], accepted_ids=[report['typo_candidates'][0]['id']])
    assert preview['revised_text'] == 'https://x.test/訂單，紧急工单首次响应为2小时。'
