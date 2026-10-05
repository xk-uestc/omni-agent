"""Replay exposed OHR questions through the production entry, retaining failures."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.answer_contract import question_contract
from backend.dense_retrieval import LocalBgeEmbedder
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore
from backend.responses_client import StructuredResponses
from evaluate_ohr_bench import answer_output_fields, implementation_snapshot, safe_audits
from model_runtime import enable_local_model, local_model_headers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--all-cases', action='store_true',
                        help='Replay EVERY retained baseline case, not only compound/enumeration questions')
    args = parser.parse_args()
    if (args.output.resolve().parent != (ROOT / 'docs').resolve() or args.output.exists()
            or args.baseline.resolve().parent != (ROOT / 'docs').resolve()):
        parser.error('Retained docs baseline and new output required')
    raw = args.baseline.read_bytes()
    baseline = json.loads(raw)
    if baseline.get('gold_used_as_corpus_or_model_input') is not False or not baseline.get('implementation_stable'):
        parser.error('Baseline protocol mismatch')
    selected = baseline['cases'] if args.all_cases else [c for c in baseline['cases'] if (
        question_contract(c['question'])['multiple_requested_fields']
        or question_contract(c['question'])['exhaustive_selection_required'])]
    if not selected:
        parser.error('No complex questions found')
    enable_local_model('gpt-6-luna')
    before = implementation_snapshot()
    store_path = Path(baseline['store_path']).resolve()
    if not store_path.is_relative_to(Path('D:/ICT8-OfficialDatasets/ohr-bench/evaluations').resolve()):
        parser.error('Original evaluation corpus required')
    embedder = LocalBgeEmbedder(ROOT / 'models/bge-small-zh-v1.5')
    stop = threading.Event()
    def execute(case):
        if stop.is_set():
            return {'ID': case['ID'], 'question': case['question'], 'status': 'not_run', 'api_audits': []}
        client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
        store = KnowledgeStore(store_path, embedder=embedder, generator=GroundedGenerator(client))
        for d in baseline['documents']:
            store.verify_source(d['document_id'], expected_sha256=d['pdf_sha256'])
        try:
            # Only the user's current question reaches answer(): no gold, correct
            # document ID, category, future turn or official evidence hint.
            result = store.answer(case['question'], top_k=4)
            generation = {'status': result['status'], 'answer_mode': result['answer_mode'],
                **answer_output_fields(result, case['official_answer']), 'trace': result.get('trace', []),
                'citations': result.get('citations', []),
                'answer_span_result': result.get('answer_span_result'),
                'generation_attempts': result.get('generation_attempts', [])}
        except Exception as exc:
            generation = {'status': 'failed', 'error_type': type(exc).__name__}
        audits = safe_audits(client)
        if any(a.get('http_status') in (401, 403) for a in audits):
            stop.set()
        print(json.dumps({'ID': case['ID'], 'status': generation['status'],
            'mode': generation.get('answer_mode')}, ensure_ascii=False), flush=True)
        return {'ID': case['ID'], 'question': case['question'], 'doc_name': case['doc_name'],
            'official_answer_scoring_only': case['official_answer'], 'generation': generation,
            'before_generation': {k: case['generation'].get(k) for k in ('status', 'answer_mode', 'answer', 'scores')},
            'api_audits': audits}
    with ThreadPoolExecutor(max_workers=2) as pool:
        cases = list(pool.map(execute, selected))
    after = implementation_snapshot()
    scored = [c for c in cases if 'scores' in c.get('generation', {})]
    summary = {'planned': len(selected), 'executed': sum('generation' in c for c in cases),
        'failed_or_unscored': len(cases) - len(scored),
        'before_exact_match': sum(c['generation']['scores']['normalized_exact_match'] for c in selected),
        'after_exact_match': sum(c['generation']['scores']['normalized_exact_match'] for c in scored),
        'before_f1': sum(c['generation']['scores']['english_token_f1'] for c in selected) / len(selected),
        'after_f1_including_failures': sum(c['generation']['scores']['english_token_f1'] for c in scored) / len(selected)}
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': ('all_retained_exposed_OHR_development_cases_not_full_benchmark' if args.all_cases else
                  'question_shape_selected_exposed_OHR_development_subset_not_full_benchmark'),
        'baseline': {'path': str(args.baseline), 'sha256': hashlib.sha256(raw).hexdigest()},
        'selection_rule': ('every_baseline_case_no_filter_no_gold_input' if args.all_cases else
                           'production_question_contract_compound_or_exhaustive_no_gold_filter'),
        'gold_used_as_corpus_or_model_input': False, 'model': 'gpt-6-luna', 'reasoning': 'medium',
        'store_path': str(store_path), 'implementation_file_sha256': before,
        'implementation_file_sha256_end': after, 'implementation_stable': before == after,
        'cases': cases, 'summary': summary,
        'limitations': ['Known questions and documents; no first-test or blind-accuracy claim.',
            'Closed-set review applies only to supplied bounded evidence, not uninspected entire PDFs.',
            'Semantic judgments remain fallible; score failures and source refusals are retained.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(summary))
    return 0 if before == after and summary['executed'] == summary['planned'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
