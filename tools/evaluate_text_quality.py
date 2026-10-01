"""Paired development audit of reversible text quality, never a blind benchmark."""
import hashlib
import json
import platform
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))


def main():
    from backend.text_quality import text_quality, repair_preview, simplify_for_retrieval, NORMALIZATION_ID
    from backend.knowledge_store import KnowledgeStore
    pairs = [
        ('traditional', '緊急工單應在2小時內首次響應。', '紧急工单应在2小时内首次响应。'),
        ('traditional_formula', '目標銷售額 = 基準銷售額 * (1 + 目標增長率)', '目标销售额 = 基准销售额 * (1 + 目标增长率)'),
        ('mixed', '保修期為12个月，金額1,234.50元。', '保修期为12个月，金额1,234.50元。'),
        ('context_name', '乾隆年間，張先生簽訂訂單。', '乾隆年间，张先生签订订单。'),
        ('typo_sales', '销受额为123元。', '销售额为123元。'),
        ('typo_warranty', '保修其为12个月。', '保修期为12个月。'),
        ('typo_response', '首欠响应为2小时。', '首次响应为2小时。'),
        ('traditional_typo', '緊急工單首欠響應為2小時。', '紧急工单首次响应为2小时。'),
        ('typo_return', '退货申清应在7日内提交。', '退货申请应在7日内提交。'),
        ('typo_expense', '报销标淮为200元。', '报销标准为200元。'),
        ('typo_amount', '订单金颔为10元。', '订单金额为10元。'),
        ('unknown_numeric_not_guessed', '保修期为l2个月，金额O元。', '保修期为l2个月，金额O元。'),
        ('code_preserved', '`銷售額`，政策為2小時。', '`銷售額`，政策为2小时。'),
        ('url_preserved', 'https://x.test/訂單，網頁連結。', 'https://x.test/訂單，网页连结。'),
        ('unchanged', '合同CASE0005：响应2小时。', '合同CASE0005：响应2小时。')]
    results = []
    for name, source, expected in pairs:
        quality = text_quality(source)
        response = repair_preview(source, source_sha256=quality['source_sha256'],
            accepted_ids=[item['id'] for item in quality['typo_candidates']])
        results.append({'id':name,'source':source,'expected':expected,'actual':response['revised_text'],
            'passed':response['revised_text']==expected,'reviewed_typo_count':len(response['applied_edits']),
            'source_sha256':quality['source_sha256']})
    with tempfile.TemporaryDirectory(prefix='ict8-text-quality-') as directory:
        store = KnowledgeStore(directory)
        source = '緊急工單應在2小時內首次響應。'
        raw = source.encode()
        record = store.ingest(raw, document_id='traditional', title='售后规则', modality='txt', filename='policy.txt')
        answer = store.answer('紧急工单首次响应')
        literal = all(citation['snippet'] in source for citation in answer['citations']) and bool(answer['citations'])
        intact = store.original('traditional')[0].read_bytes() == raw
        original_hash = record['sha256'] == hashlib.sha256(raw).hexdigest()
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'bounded_lexicon_and_script_development_pairs_not_general_spelling_or_blind_accuracy',
        'normalization':NORMALIZATION_ID,'python':platform.python_version(),'pairs':results,
        'retrieval':{'cross_script_status':answer['status'],'literal_citations':literal,'original_bytes_preserved':intact,'source_hash_preserved':original_hash},
        'summary':{'passed':sum(row['passed'] for row in results),'total':len(results)},
        'limits':['finite_typo_lexicon','unknown_numeric_ocr_errors_not_corrected','typo_changes_require_confirmation','no_model_called']}
    (ROOT/'docs/TEXT_QUALITY_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'summary':report['summary'],'retrieval':report['retrieval']},ensure_ascii=False))
    if not all(row['passed'] for row in results) or not literal or not intact or not original_hash:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
