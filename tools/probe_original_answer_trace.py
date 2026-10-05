"""Observe unchanged production model decisions on retained exposed questions."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.dense_retrieval import LocalBgeEmbedder
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore
from backend.responses_client import StructuredResponses
from evaluate_ohr_bench import implementation_snapshot, safe_audits
from model_runtime import enable_local_model, local_model_headers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--case-id', action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if (args.baseline.resolve().parent != (ROOT/'docs').resolve()
            or args.output.resolve().parent != (ROOT/'docs').resolve() or args.output.exists()
            or not 1 <= len(args.case_id) <= 8 or len(set(args.case_id)) != len(args.case_id)):
        parser.error('Retained baseline, 1 to 8 unique exposed IDs and new docs report required')
    raw = args.baseline.read_bytes()
    baseline = json.loads(raw)
    if (baseline.get('gold_used_as_corpus_or_model_input') is not False
            or baseline.get('implementation_stable') is not True):
        parser.error('Stable reference-isolated baseline required')
    corpus = Path(baseline['store_path']).resolve()
    if not corpus.is_relative_to(Path('D:/ICT8-OfficialDatasets/ohr-bench/evaluations').resolve()):
        parser.error('Retained original evaluation corpus required')
    indexed = {case['ID']: case for case in baseline['cases']}
    if any(identifier not in indexed for identifier in args.case_id):
        parser.error('Unknown exposed case')
    enable_local_model('gpt-6-luna')
    before = implementation_snapshot()
    embedder = LocalBgeEmbedder(ROOT/'models/bge-small-zh-v1.5')
    records = []
    for identifier in args.case_id:
        question = indexed[identifier]['question']
        client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
        calls = []
        generate = client.generate

        def observed(instructions, context, schema, **options):
            record = {'operation': options.get('name'), 'instructions': instructions,
                'context': deepcopy(context), 'schema': deepcopy(schema)}
            calls.append(record)
            try:
                result = generate(instructions, context, schema, **options)
                record['output'] = deepcopy(result)
                return result
            except Exception as error:
                record['error_type'] = type(error).__name__
                raise

        client.generate = observed
        store = KnowledgeStore(corpus, embedder=embedder, generator=GroundedGenerator(client))
        for document in baseline['documents']:
            store.verify_source(document['document_id'], expected_sha256=document['pdf_sha256'])
        try:
            # No gold, document hint, page hint or prior model answer reaches production.
            result = store.answer(question, top_k=4)
        except Exception as error:
            result = {'status': 'failed', 'error_type': type(error).__name__}
        audits = safe_audits(client)
        records.append({'ID': identifier, 'question': question, 'calls': calls, 'result': result, 'api_audits': audits})
        print(json.dumps({'ID': identifier, 'status': result['status'], 'operations': [c['operation'] for c in calls]}), flush=True)
        if any(a.get('http_status') in (401, 403) for a in audits):
            break
    after = implementation_snapshot()
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'exposed_question_first_decision_observation_not_accuracy_score',
        'baseline_sha256': hashlib.sha256(raw).hexdigest(), 'gold_used_as_corpus_or_model_input': False,
        'production_instructions_modified': False, 'model': 'gpt-6-luna', 'reasoning': 'medium',
        'implementation_file_sha256_start': before, 'implementation_file_sha256_end': after,
        'implementation_stable': before == after, 'records': records}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return 0 if before == after and len(records) == len(args.case_id) else 1


if __name__ == '__main__':
    raise SystemExit(main())
