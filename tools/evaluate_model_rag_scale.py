"""Three text-PDF scales with actual hybrid retrieval and authorized generation.

Synthetic performance samples, not official QA accuracy or scan-OCR coverage.
Every request uses the ordinary production answer method. No expected answer,
document ID or evidence page is supplied to that method or to the model.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.dense_retrieval import LocalBgeEmbedder
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import StructuredResponses
from evaluate_ohr_bench import implementation_snapshot, safe_audits, normalize_answer
from evaluate_rag_scale import make_pdf, stats
from model_runtime import enable_local_model, local_model_headers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().parent != (ROOT/'docs').resolve():
        parser.error('New performance report directly in docs required')
    configured = enable_local_model('gpt-6-luna')
    if configured['reasoning'] != 'medium':
        parser.error('Only authorized medium reasoning allowed')
    before = implementation_snapshot()
    run = ROOT/'runtime'/('model-rag-scale-'+uuid.uuid4().hex)
    run.mkdir()
    embedder = LocalBgeEmbedder(ROOT/'models/bge-small-zh-v1.5')
    records = []
    auth_failed = False
    for pages in (10, 100, 500):
        if auth_failed:
            break
        location = run/str(pages)
        raw = make_pdf(pages)
        store = KnowledgeStore(location, embedder=embedder)
        started = time.perf_counter()
        document = store.ingest(raw, document_id='contracts', title='合成合同档案',
            modality='pdf', filename='contracts.pdf')
        ingest_ms = round((time.perf_counter()-started)*1000, 3)
        target = pages//2
        question = f'CASE{target:04d}约定保修期限是多少个月？'
        reference = f'{12+target%7}个月'
        def execute(index):
            client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
                model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
            local = KnowledgeStore(location, embedder=embedder, generator=GroundedGenerator(client))
            started = time.perf_counter()
            try:
                result = local.answer(question, top_k=4)
                sample = {'request': index, 'status': result['status'],
                    'answer_mode': result['answer_mode'], 'answer': result['answer'],
                    'retrieval': result['retrieval'], 'trace': result.get('trace', []),
                    'citations': result['citations'],
                    'literal_answer_match_scoring_only': normalize_answer(result['answer']) == normalize_answer(reference),
                    'expected_page_cited_scoring_only': any(
                        c['metadata'].get('document_id') == 'contracts'
                        and c['metadata'].get('page_no') == target
                        and c['metadata'].get('source_sha256') == document['sha256']
                        for c in result['citations'])}
            except Exception as exc:
                sample = {'request': index, 'status': 'failed', 'error_type': type(exc).__name__,
                    'literal_answer_match_scoring_only': False, 'expected_page_cited_scoring_only': False}
            sample['wall_ms'] = round((time.perf_counter()-started)*1000, 3)
            sample['api_audits'] = safe_audits(client)
            sample['reference_sent_to_model'] = False
            return sample
        cold = execute('cold')
        auth_failed = any(a.get('http_status') in (401, 403) for a in cold['api_audits'])
        groups = []
        for workers in (1, 2, 4):
            if auth_failed:
                break
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                samples = list(pool.map(execute, [f'{workers}:{i}' for i in range(workers)]))
            elapsed = time.perf_counter()-started
            auth_failed = any(a.get('http_status') in (401, 403) for s in samples for a in s['api_audits'])
            # Two diagnostics together still do not certify semantic accuracy.
            supported = sum(s['status'] == 'ok' and s['literal_answer_match_scoring_only']
                and s['expected_page_cited_scoring_only'] for s in samples)
            groups.append({'workers': workers, 'requests': len(samples),
                'wall_seconds': round(elapsed, 3), 'completed_qps': round(len(samples)/elapsed, 6),
                'literal_and_source_supported_qps': round(supported/elapsed, 6),
                'literal_and_source_supported': supported, 'latency': stats([s['wall_ms'] for s in samples]),
                'samples': samples})
        record = {'pages': pages, 'pdf_bytes': len(raw), 'pdf_sha256': hashlib.sha256(raw).hexdigest(),
            'chunks': document['chunk_count'], 'ingestion_ms': ingest_ms, 'question': question,
            'reference_scoring_only': reference, 'expected_page_scoring_only': target,
            'cold_answer': cold, 'concurrency': groups}
        records.append(record)
        print(json.dumps({'pages': pages, 'cold_ms': cold['wall_ms'], 'groups': [
            {k:g[k] for k in ('workers', 'requests', 'completed_qps', 'literal_and_source_supported')}
            for g in groups]}, ensure_ascii=False), flush=True)
    after = implementation_snapshot()
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'synthetic_text_PDF_hybrid_and_real_model_end_to_end_performance_not_official_accuracy',
        'model': 'gpt-6-luna', 'reasoning': 'medium', 'reference_sent_to_model': False,
        'environment': {'python': platform.python_version(), 'platform': platform.platform(),
            'cpu_count': os.cpu_count(), 'embedding': embedder.identity,
            'exclusive_hardware': False, 'other_regression_and_model_jobs_may_be_running': True},
        'records': records, 'auth_failure_stopped_new_requests': auth_failed,
        'implementation_stable': before == after, 'implementation_file_sha256_start': before,
        'implementation_file_sha256_end': after, 'run_directory': str(run),
        'limitations': ['Synthetic searchable text PDFs; no scan OCR, no official accuracy claim.',
            'Only one warm group per concurrency; small samples, not capacity or stable P95 proof.',
            'Remote provider, network, load and caches may affect timings; no controlled speedup claim.',
            'Cold query follows corpus ingestion; embedding object reused between scales.',
            'Expected literal and source page are diagnostics, not whole-question correctness.',
            'Failing requests remain in timing and completion denominators.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'scales': len(records), 'implementation_stable': before == after,
        'auth_failed': auth_failed}))
    return 0 if len(records) == 3 and before == after and not auth_failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
