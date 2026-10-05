"""Observe symbolic production rejection codes; never change answering gates.

Only a retained question goes to the ordinary pipeline, never a gold answer or
document hint. Diagnostics record short allowlisted error names, not payloads.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.dense_retrieval import LocalBgeEmbedder
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore
from backend.responses_client import StructuredResponses
from evaluate_ohr_bench import implementation_snapshot, safe_audits
from model_runtime import enable_local_model, local_model_headers
import os


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if (args.baseline.resolve().parent != (ROOT / 'docs').resolve()
            or args.output.resolve().parent != (ROOT / 'docs').resolve()
            or args.output.exists()):
        parser.error('Retained docs input and new docs output required')
    baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
    if (baseline.get('implementation_stable') is not True
            or baseline.get('gold_used_as_corpus_or_model_input') is not False):
        parser.error('Stable baseline with isolated reference answers required')
    matches = [c for c in baseline['cases'] if c['ID'] == args.case_id]
    if len(matches) != 1:
        parser.error('Exactly one retained case required')
    store_path = Path(baseline['store_path']).resolve()
    if not store_path.is_relative_to(Path('D:/ICT8-OfficialDatasets/ohr-bench/evaluations').resolve()):
        parser.error('Original corpus required')
    enable_local_model('gpt-6-luna')
    before = implementation_snapshot()
    client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
        model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
    store = KnowledgeStore(store_path, embedder=LocalBgeEmbedder(ROOT / 'models/bge-small-zh-v1.5'),
        generator=GroundedGenerator(client))
    codes, rejected_compositions = [], []
    watched = {'document_parts_answer.py', 'source_multi_span_answer.py', 'native_table_question.py'}
    def trace(frame, event, value):
        filename = Path(frame.f_code.co_filename).name
        if filename not in watched:
            return None
        if event == 'exception' and isinstance(value[1], ValueError):
            code = str(value[1])
            if re.fullmatch(r'(?:document_parts|component|unsupported_component|native|multi)_[a-z0-9_]{1,100}', code):
                entry = {'module': filename, 'function': frame.f_code.co_name, 'code': code}
                if code == 'document_parts_whole_question_rejected':
                    review = frame.f_locals.get('review', {})
                    entry['review_checks'] = {k: v for k, v in review.items()
                        if k in ('approved', 'every_original_part_answered',
                            'no_subject_period_or_condition_changed', 'all_sources_and_units_bound',
                            'no_conflict_or_unrequested_inference',
                            'every_computed_value_has_server_computation', 'no_missing_cross_part_dependency')
                        and type(v) is bool}
                    rejected_compositions.append(deepcopy(frame.f_locals.get('children', [])))
                if entry not in codes:
                    codes.append(entry)
                    print(json.dumps(entry), flush=True)
        return trace
    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        result = store.answer(matches[0]['question'], top_k=4)
    finally:
        sys.settrace(previous)
    after = implementation_snapshot()
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'observed_production_rejections_not_accuracy_evaluation',
        'gold_sent_to_model': False, 'case_id': args.case_id, 'question': matches[0]['question'],
        'model': 'gpt-6-luna', 'reasoning': 'medium', 'status': result['status'],
        'answer_mode': result['answer_mode'], 'answer': result.get('answer'),
        'component_answers': result.get('component_answers', []),
        'computation': result.get('computation'), 'semantic_review': result.get('semantic_review'),
        'citations': result.get('citations', []),
        'rejected_compositions_diagnostic_only': rejected_compositions,
        'symbolic_rejections': codes, 'trace': result.get('trace', []),
        'api_audits': safe_audits(client), 'implementation_stable': before == after,
        'implementation_file_sha256_start': before, 'implementation_file_sha256_end': after}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'status': report['status'], 'codes': codes, 'stable': before == after}), flush=True)


if __name__ == '__main__':
    main()
