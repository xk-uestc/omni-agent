"""Actual OHR-Bench PDF ingestion/retrieval/OCR pilot, optional real generation.

Official QA and GT are scoring-only. They are never inserted into the corpus or
passed to the generator. This resource-biased pilot is not full OHR-Bench score.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import string
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / 'benchmarks/ohr_bench/MANIFEST.json'
sys.path.insert(0, str(ROOT / 'ict-track8'))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def checked_path(data_root, relative):
    path = (data_root / relative).resolve()
    if not path.is_relative_to(data_root.resolve()):
        raise ValueError('asset_outside_data_root')
    return path


def load_frozen(manifest_path, data_root=None):
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    root = Path(data_root or manifest['data_root_default']).resolve()
    raw = checked_path(root, manifest['selected_qa_path']).read_bytes()
    if digest(raw) != manifest['selected_qa_sha256']:
        raise ValueError('frozen_questions_sha_mismatch')
    questions = json.loads(raw)
    expected = {row['ID']: row for row in manifest['cases']}
    if len(questions) != len(expected) or {row['ID'] for row in questions} != set(expected):
        raise ValueError('frozen_question_ids_mismatch')
    for row in questions:
        observed = digest(json.dumps(row, ensure_ascii=False, sort_keys=True).encode('utf-8'))
        if observed != expected[row['ID']]['original_row_sha256']:
            raise ValueError('official_question_row_changed')
    for asset in manifest['documents']:
        for path_key, hash_key in [('pdf_path', 'pdf_sha256'), ('gt_path', 'gt_sha256')]:
            if digest(checked_path(root, asset[path_key]).read_bytes()) != asset[hash_key]:
                raise ValueError('official_asset_sha_mismatch')
    return manifest, root, questions


def doc_id(name):
    return 'ohr-' + digest(name.encode('utf-8'))[:20]


def expected_pages(row):
    pages = row['evidence_page_no']
    pages = pages if isinstance(pages, list) else [pages]
    if not pages or any(type(page) is not int or page < 0 for page in pages):
        raise ValueError('invalid_official_zero_based_pages')
    return {page + 1 for page in pages}


def normalize_answer(value):
    # Equivalent lightweight English normalization to official common.py.
    if isinstance(value, list):
        value = '\n'.join(str(item) for item in value)
    value = ''.join(ch for ch in str(value).lower() if ch not in string.punctuation)
    return ' '.join(re.sub(r'\b(a|an|the)\b', ' ', value).split())


def answer_scores(prediction, gold):
    p, g = normalize_answer(prediction), normalize_answer(gold)
    pt, gt = p.split(), g.split()
    common = sum((Counter(pt) & Counter(gt)).values())
    if not common or (p in {'yes', 'no', 'noanswer'} or g in {'yes', 'no', 'noanswer'}) and p != g:
        f1 = 0.0
    else:
        precision, recall = common / len(pt), common / len(gt)
        f1 = 2 * precision * recall / (precision + recall)
    return {'normalized_exact_match': int(p == g), 'english_token_f1': round(f1, 6),
            'normalized_gold_substring': bool(g and g in p)}


def reference_lcs_recall(prediction, reference):
    """Official-style normalized word LCS / reference words, linear memory."""
    predicted, gold = normalize_answer(prediction).split(), normalize_answer(reference).split()
    if not gold:
        return None
    previous = [0] * (len(predicted) + 1)
    for word in gold:
        current = [0]
        for column, candidate in enumerate(predicted, start=1):
            current.append(previous[column-1] + 1 if word == candidate else max(previous[column], current[-1]))
        previous = current
    return round(previous[-1] / len(gold), 6)


def retrieval_scores(hits, row):
    expected_doc = doc_id(row['doc_name'])
    pages = expected_pages(row)
    found = {hit.metadata.get('page_no') for hit in hits if hit.metadata.get('document_id') == expected_doc}
    relevant = [hit.snippet for hit in hits if hit.metadata.get('document_id') == expected_doc
                and hit.metadata.get('page_no') in pages]
    return {'document_hit': any(hit.metadata.get('document_id') == expected_doc for hit in hits),
            'any_evidence_page_hit': bool(pages & found), 'all_evidence_pages_hit': pages <= found,
            'gold_pages_one_based': sorted(pages), 'retrieved_gold_document_pages': sorted(p for p in found if type(p) is int),
            'evidence_text_lcs_recall': reference_lcs_recall('\n\n'.join(relevant), row['evidence_context']) if relevant else 0,
            'gold_answer_lexically_present': answer_scores('\n'.join(hit.snippet for hit in hits), row['answers'])['normalized_gold_substring']}


def reduced_hits(hits):
    return [{'document_id': hit.metadata.get('document_id'), 'page_no': hit.metadata.get('page_no'),
             'locator': hit.metadata.get('source_locator'), 'snippet': hit.snippet, 'score': hit.score,
             'channels': hit.metadata.get('retrieval_channels', [hit.metadata.get('retrieval_channel')])} for hit in hits]


def inspect_rendered_ocr(pdf_raw, gt_raw, pipeline):
    """Render ALL PDF pages, no gold page filtering. GT is used after OCR only.

    These diagnostics do not replace native PDF text in the retrieval corpus and
    are not the official formatting/semantic-noise variants.
    """
    import fitz
    references = {page['page_idx']: page['text'] for page in json.loads(gt_raw)}
    records = []
    with fitz.open(stream=pdf_raw, filetype='pdf') as pdf:
        for index, page in enumerate(pdf):
            started = time.perf_counter()
            record = {'page_idx_zero_based': index, 'page_no_one_based': index + 1}
            try:
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                raw = pixmap.tobytes('png')
                result = pipeline.run(raw, language='eng').to_dict()
                record.update({'render_sha256': digest(raw), 'render_pixels': [pixmap.width, pixmap.height],
                               'status': result['status'], 'confidence': result.get('confidence'),
                               'warnings': result.get('warnings'), 'selected_transforms': result.get('selected_transforms'),
                               'actual_ocr_text': result.get('text', ''),
                               'gt_available': index in references,
                               'gt_text_lcs_recall': reference_lcs_recall(result.get('text', ''), references[index]) if index in references else None})
            except Exception as exc:
                record.update({'status': 'failed', 'error_type': type(exc).__name__})
            record['wall_ms'] = round((time.perf_counter() - started) * 1000, 3)
            records.append(record)
    return records


def query_generated(store, question):
    # Deliberately accepts only the question; no gold/evidence/page/doc hints.
    return store.answer(question, top_k=4)


def safe_audits(client):
    fields = ('provider', 'model', 'reasoning', 'operation', 'http_status', 'status',
              'model_verified', 'response_model', 'input_tokens', 'output_tokens', 'total_tokens',
              'cached_input_tokens', 'reasoning_tokens', 'call_index', 'latency_ms')
    return [{key: row[key] for key in fields if key in row} for row in client.audit_history]


def summarise_cases(cases):
    result = {'attempted': len(cases)}
    for mode in ('bm25', 'hybrid'):
        rows = [row['retrieval'][mode] for row in cases if mode in row.get('retrieval', {})]
        result[mode] = {'measured': len(rows), 'document_hits': sum(row['document_hit'] for row in rows),
                        'any_page_hits': sum(row['any_evidence_page_hit'] for row in rows),
                        'all_page_hits': sum(row['all_evidence_pages_hit'] for row in rows),
                        'lexical_answer_presence': sum(row['gold_answer_lexically_present'] for row in rows),
                        'mean_evidence_lcs_recall': round(sum(row['evidence_text_lcs_recall'] or 0 for row in rows) / len(rows), 6) if rows else None}
    models = [row['generation'] for row in cases if 'generation' in row]
    if models:
        result['model'] = {'attempted': len(models), 'grounded_generations': sum(row.get('answer_mode') == 'model_grounded' for row in models),
                           'grounded_substantive_answers': sum(row.get('answer_mode') == 'model_grounded' and row.get('status') == 'ok' for row in models),
                           'model_abstentions': sum(row.get('answer_mode') == 'model_grounded' and row.get('status') == 'insufficient_evidence' for row in models),
                           'extractive_fallbacks': sum(row.get('answer_mode') == 'extractive_fallback' for row in models),
                           'normalized_exact_matches': sum(row.get('scores', {}).get('normalized_exact_match', 0) for row in models),
                           'mean_english_token_f1': round(sum(row.get('scores', {}).get('english_token_f1', 0) for row in models) / len(models), 6),
                           'metric_scope': 'program_end_to_end_outputs_including_extractive_fallback_not_bare_model_accuracy'}
        grounded = [row for row in models if row.get('answer_mode') == 'model_grounded']
        result['model']['grounded_only_count'] = len(grounded)
        result['model']['grounded_only_mean_english_token_f1'] = round(sum(row['scores']['english_token_f1'] for row in grounded) / len(grounded), 6) if grounded else None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--with-model', action='store_true')
    parser.add_argument('--reuse-store', type=Path, help='Reuse only previously ingested actual PDFs with exact manifest hashes')
    parser.add_argument('--skip-rendered-ocr', action='store_true', help='Explicitly skip separate raster OCR diagnostics, not native ingest OCR')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    manifest, data_root, questions = load_frozen(args.manifest, args.data_root)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = args.output or ROOT / f'docs/OHR_BENCH_{"MODEL" if args.with_model else "LOCAL"}_{stamp}.json'
    if output.resolve().parent != (ROOT / 'docs').resolve() or output.suffix != '.json' or output.exists():
        parser.error('Output must be a new JSON directly in docs; cannot overwrite history')
    from backend.knowledge_store import KnowledgeStore
    from backend.ocr import OcrPipeline, RapidOcrExecutor
    from backend.dense_retrieval import LocalBgeEmbedder
    pipeline = OcrPipeline(RapidOcrExecutor())
    embedder = LocalBgeEmbedder(ROOT / 'models/bge-small-zh-v1.5')
    store_path = args.reuse_store or data_root / 'evaluations' / stamp / 'knowledge'
    if not store_path.resolve().is_relative_to((data_root / 'evaluations').resolve()):
        parser.error('Evaluation store must be within data_root/evaluations')
    if args.reuse_store and not (store_path / 'knowledge.sqlite').exists():
        parser.error('Reuse store does not exist; cannot silently rebuild')
    store = KnowledgeStore(store_path, ocr_pipeline=pipeline, embedder=embedder)
    if args.reuse_store and {row['document_id'] for row in store.list_documents()} != {doc_id(row['doc_name']) for row in manifest['documents']}:
        parser.error('Reuse corpus documents differ from frozen original PDF set')
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'scope': 'official_ohr_bench_frozen_resource_biased_pilot_actual_pdf_corpus',
              'dataset_revision': manifest['hf_revision'], 'official_code_revision': manifest['official_code_revision'],
              'selection_sha256': manifest['selected_qa_sha256'], 'selection_limit': manifest['selection_limit'],
              'manifest_sha256': digest(args.manifest.read_bytes()), 'store_path': str(store_path.resolve()),
              'implementation_file_sha256': {name: digest((ROOT / name).read_bytes()) for name in (
                  'tools/evaluate_ohr_bench.py', 'ict-track8/backend/knowledge_store.py',
                  'ict-track8/backend/chunk_cleaning.py', 'ict-track8/backend/dense_retrieval.py',
                  'ict-track8/backend/grounded_generation.py', 'ict-track8/backend/responses_client.py',
                  'ict-track8/backend/pdf_native_context.py', 'ict-track8/backend/pdf_page_index.py',
                  'ict-track8/backend/visual_routing.py')},
              'gold_used_as_corpus_or_model_input': False, 'official_pages_zero_based_project_pages_one_based': True,
              'model_enabled': args.with_model, 'model_workers': 1, 'documents': [], 'cases': [],
              'limits': ['Only 7 selected documents are candidate corpus, not 8,500+ full benchmark pages.',
                         'Document/page hits are proxy diagnostics, not official text-block recall.',
                         'English normalization/F1/LCS reproduce lightweight definitions from pinned official src/metric/common.py; not complete official evaluator protocol.',
                         'Lexical gold substring is not semantic correctness and can match irrelevant numeric occurrences.',
                         'Rendered OCR processes every original page but is diagnostic only; native PDF text remains generation input when present.',
                         'The existing Chinese embedding model BGE-small-zh is used on this English benchmark, not the official bge-m3 baseline.',
                         'Model may translate English answers; lexical metrics can undercount semantically correct translations; no gold correction is made.'],
              'status': 'running'}
    for asset in manifest['documents']:
        started = time.perf_counter()
        raw = checked_path(data_root, asset['pdf_path']).read_bytes()
        item = {'doc_name': asset['doc_name'], 'document_id': doc_id(asset['doc_name']), 'pdf_sha256': asset['pdf_sha256']}
        try:
            if args.reuse_store:
                parsed = store.document(item['document_id'])
                if parsed['sha256'] != asset['pdf_sha256']:
                    raise ValueError('reuse_store_pdf_hash_mismatch')
                item['reuse_verified'] = True
            else:
                parsed = store.ingest(raw, document_id=item['document_id'], title=asset['doc_name'], modality='pdf', filename=Path(asset['pdf_path']).name, language='eng')
            item.update({key: parsed[key] for key in ('chunk_count', 'stats', 'warnings', 'routing')})
            item['ingestion_status'] = 'ok'
        except Exception as exc:
            item.update({'ingestion_status': 'failed', 'error_type': type(exc).__name__})
        item['ingestion_wall_ms'] = round((time.perf_counter() - started) * 1000, 3)
        if not args.skip_rendered_ocr:
            item['rendered_ocr'] = inspect_rendered_ocr(raw, checked_path(data_root, asset['gt_path']).read_bytes(), pipeline)
        report['documents'].append(item)
        print(json.dumps({'ingested': asset['doc_name'], 'status': item['ingestion_status'], 'chunks': item.get('chunk_count')}, ensure_ascii=False), flush=True)
    client = None
    if args.with_model:
        from model_runtime import enable_local_model, local_model_headers
        from backend.responses_client import StructuredResponses
        from backend.grounded_generation import GroundedGenerator
        configured = enable_local_model('gpt-6-luna')
        if configured.get('reasoning') != 'medium':
            raise ValueError('Only authorized medium reasoning allowed')
        client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
                                     model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
        store.generator = GroundedGenerator(client)
    dense = store.dense_index
    for row in questions:
        entry = {'ID': row['ID'], 'doc_name': row['doc_name'], 'question': row['questions'], 'official_answer': row['answers'],
                 'evidence_source': row['evidence_source'], 'retrieval': {}}
        for mode in ('bm25', 'hybrid'):
            store.dense_index = None if mode == 'bm25' else dense
            started = time.perf_counter()
            try:
                hits = store.search(row['questions'], top_k=4)
                entry['retrieval'][mode] = {**retrieval_scores(hits, row), 'hits': reduced_hits(hits),
                                          'wall_ms': round((time.perf_counter() - started) * 1000, 3)}
            except Exception as exc:
                entry['retrieval'][mode] = {'document_hit': False, 'any_evidence_page_hit': False, 'all_evidence_pages_hit': False,
                                           'gold_answer_lexically_present': False, 'evidence_text_lcs_recall': 0, 'error_type': type(exc).__name__}
        if client:
            client.reset_audit()
            started = time.perf_counter()
            try:
                result = query_generated(store, row['questions'])
                # Scoring-only: inspect exact model input, not public short
                # retrieval snippets or raw_page_text that was never sent.
                model_evidence = '\n\n'.join(
                    citation.get('generation_evidence', {}).get('text', '')
                    for citation in result.get('citations', [])
                    if citation['metadata'].get('document_id') == doc_id(row['doc_name'])
                    and citation['metadata'].get('page_no') in expected_pages(row))
                entry['generation'] = {'status': result['status'], 'answer_mode': result['answer_mode'],
                                       'answer': result['answer'], 'scores': answer_scores(result['answer'], row['answers']),
                                       'citations': result['citations'], 'generation_attempts': result.get('generation_attempts', []),
                                       'trace': result.get('trace', []),
                                       'scoring_only_model_evidence': {
                                           'evidence_text_lcs_recall': reference_lcs_recall(model_evidence, row['evidence_context']),
                                           'gold_answer_lexically_present': answer_scores(model_evidence, row['answers'])['normalized_gold_substring'],
                                       },
                                       'wall_ms': round((time.perf_counter() - started) * 1000, 3)}
            except Exception as exc:
                entry['generation'] = {'status': 'failed', 'error_type': type(exc).__name__, 'wall_ms': round((time.perf_counter()-started)*1000, 3)}
            entry['generation']['api_audits'] = safe_audits(client)
            entry['generation']['audit_dropped_calls'] = client.audit_dropped_count
        report['cases'].append(entry)
        print(json.dumps({'case': row['ID'], 'category': row['evidence_source'], 'hybrid_page_hit': entry['retrieval']['hybrid']['all_evidence_pages_hit'],
                          'model_mode': entry.get('generation', {}).get('answer_mode')}, ensure_ascii=False), flush=True)
        if client and any(a['http_status'] in (401, 403) for a in entry['generation']['api_audits']):
            report['status'] = 'stopped_model_auth_failure'
            break
    if report['status'] == 'running':
        report['status'] = 'measured_with_failures_retained'
    report['summary'] = summarise_cases(report['cases'])
    report['api_audits'] = [a for row in report['cases'] for a in row.get('generation', {}).get('api_audits', [])]
    if client:
        from evaluate_model import summarise_audits
        report['api_summary'] = summarise_audits(report['api_audits'], sum(row.get('generation', {}).get('audit_dropped_calls', 0) for row in report['cases']))
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'output': str(output), 'status': report['status'], 'summary': report['summary']}, ensure_ascii=False))
    return 0 if len(report['cases']) == len(questions) and all(d['ingestion_status'] == 'ok' for d in report['documents']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
