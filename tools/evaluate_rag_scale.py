"""Actual PDF ingestion + BM25/BGE/RRF + attributed answer scale test.

Synthetic searchable text pages; excludes OCR and external model generation.
"""
import json
import argparse
import platform
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.dense_retrieval import LocalBgeEmbedder


def make_pdf(pages):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    stream = BytesIO()
    document = canvas.Canvas(stream, invariant=1)
    for page in range(1, pages+1):
        document.setFont('STSong-Light', 13)
        for line, text in enumerate([
            f'档案编号CASE{page:04d} · 合成合同资料',
            f'CASE{page:04d}约定保修期限为{12+page%7}个月。',
            f'CASE{page:04d}首次响应期限为{2+page%5}小时。',
            '保修自签收日期起算，保留订单号、产品序列号和检修报告。',
            '客户未授权时不推断个人资料；过期或版本冲突的条款需澄清。',
            '本文件仅用于性能与证据定位测试，不代表真实商业承诺。',
        ]):
            document.drawString(40, 780-line*32, text)
        document.showPage()
    document.save()
    return stream.getvalue()


def stats(values):
    ordered = sorted(values)
    return {'p50_ms': round(statistics.median(ordered),3),
            'p95_ms': round(ordered[min(len(ordered)-1, int(.95*len(ordered)))],3)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--work-dir',type=Path,help='独立性能样本与缓存目录，避免占满项目盘')
    args=parser.parse_args()
    embedder = LocalBgeEmbedder(ROOT/'models/bge-small-zh-v1.5')
    records = []
    # Each run has a new corpus so cold measurements do not reuse old vectors.
    run = str(time.time_ns())
    for pages in (10, 100, 500):
        store = KnowledgeStore((args.work_dir or ROOT/'runtime/rag-scale')/run/str(pages), embedder=embedder)
        raw = make_pdf(pages)
        start = time.perf_counter()
        ingested = store.ingest(raw, document_id='contracts', title='合成合同档案', modality='pdf', filename='contracts.pdf')
        ingest_ms = (time.perf_counter()-start)*1000
        target = pages//2
        question = f'CASE{target:04d}约定保修期限是多少个月？'
        start = time.perf_counter()
        cold = store.answer(question)
        cold_ms = (time.perf_counter()-start)*1000
        times, checks = [], []
        for _ in range(7):
            start = time.perf_counter()
            answer = store.answer(question)
            times.append((time.perf_counter()-start)*1000)
            checks.append(any(hit['metadata']['source_locator'].startswith(f'page:{target}:')
                              and f'{12+target%7}个月' in hit['snippet'] for hit in answer['citations']))
        # Also measure a query without an entity ID, so exact filtering cannot hide
        # the cost of embedding/ranking the complete collection.
        global_question = '保修应保留哪些核验凭证？'
        start = time.perf_counter()
        global_answer = store.answer(global_question)
        global_cold_ms = (time.perf_counter()-start)*1000
        global_times, global_checks = [], []
        for _ in range(7):
            start = time.perf_counter()
            answer = store.answer(global_question)
            global_times.append((time.perf_counter()-start)*1000)
            global_checks.append(any(all(word in hit['snippet'] for word in ('订单号', '产品序列号', '检修报告')) for hit in answer['citations']))
        concurrency = {}
        for workers in (1,4,8):
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                answers = list(pool.map(lambda _: store.answer(question), range(16)))
            elapsed = time.perf_counter()-start
            concurrency[str(workers)] = {'qps': round(16/elapsed,3), 'completed': len(answers),
                'citation_missing': sum(not answer['citations'] for answer in answers)}
        record = {'pages':pages,'pdf_bytes':len(raw),'chunks':ingested['chunk_count'],
            'ingest_ms':round(ingest_ms,3),'cold_first_answer_ms':round(cold_ms,3),
            'warm':stats(times),'warm_evidence_passed':sum(checks),'warm_evidence_total':len(checks),
            'query_scenario': 'exact_identifier_lookup',
            'global_semantic_query': {'first_answer_ms':round(global_cold_ms,3), 'warm':stats(global_times),
                'evidence_passed':sum(global_checks), 'evidence_total':len(global_checks)},
            'answer_mode':cold['answer_mode'],'retrieval':cold['retrieval'], 'concurrency':concurrency}
        records.append(record)
        print(json.dumps(record,ensure_ascii=False),flush=True)
    report = {'created_at':datetime.now(timezone.utc).isoformat(),'scope':'synthetic_text_pdf_local_hybrid_extractive_no_ocr_no_external_generation',
        'environment':{'python':platform.python_version(),'platform':platform.platform(),'embedding':embedder.identity},'records':records}
    (ROOT/'docs/RAG_SCALE_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(record['warm_evidence_passed']==record['warm_evidence_total'] and record['global_semantic_query']['evidence_passed']==7 for record in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
