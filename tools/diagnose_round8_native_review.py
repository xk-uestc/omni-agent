"""Record native-table selection/review on source-only input, no scoring oracle."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
from evaluate_round7_complex import pdf_table
from model_runtime import enable_local_model, local_model_headers
from backend.responses_client import StructuredResponses
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore
from backend.native_table_question import route_native_table_question


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().parent != (ROOT / 'docs').resolve() or args.output.exists():
        parser.error('New docs output required')
    enable_local_model()
    client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
        model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
    calls = []
    generate = client.generate
    def observed(instructions, context, schema, **options):
        value = generate(instructions, context, schema, **options)
        calls.append({'operation': options['name'], 'context': context, 'output': value, 'audit': client.audit})
        return value
    client.generate = observed
    directory = ROOT / 'runtime' / ('native-review-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    store = KnowledgeStore(directory, generator=GroundedGenerator(client))
    store.ingest(pdf_table(), document_id='cost', title='Operating cost report', modality='pdf', filename='cost.pdf')
    question = 'For Network, subtract its 2031 cost from its 2032 cost.'
    result, trace = route_native_table_question(store, question, store.search(question, document_id='cost'), document_id='cost')
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'source_only_development_diagnosis_not_accuracy_score', 'reference_opened': False,
        'calls': calls, 'result': result, 'trace': trace}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'trace': trace['status'], 'calls': [{'operation': c['operation'], 'output': c['output']} for c in calls]}))


if __name__ == '__main__':
    main()
