"""Retained anonymous failure diagnosis, never a replacement accuracy score."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import StructuredResponses, object_schema
from backend.native_row_selection import CHECKS, route_native_row_selection, replay_selection
from evaluate_ohr_bench import implementation_snapshot, safe_audits
from model_runtime import enable_local_model, local_model_headers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--review-rationale', action='store_true',
                        help='Diagnostic prototype: require concrete rejection findings; retain every original review flag')
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().parent != (ROOT/'docs').resolve():
        parser.error('New diagnostic in docs required')
    raw = args.baseline.read_bytes()
    baseline = json.loads(raw)
    if baseline.get('gold_sent_to_model') is not False or baseline.get('implementation_stable') is not True:
        parser.error('Reference-isolated stable anonymous baseline required')
    case = next(c for c in baseline['cases'] if c['case_id'] == args.case_id)
    location = Path(baseline['run_directory']).resolve()/case['case_id']/'knowledge'
    if not location.is_relative_to((ROOT/'runtime').resolve()):
        parser.error('Retained local original source required')
    enable_local_model('gpt-6-luna')
    before = implementation_snapshot()
    client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
        model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
    observed = []
    generate = client.generate
    def capture(*positional, **kwargs):
        if args.review_rationale and kwargs['name'] == 'native_row_selection_independent_review':
            instructions, context, schema = positional
            finding = object_schema({'check': {'type': 'string', 'enum': list(CHECKS)},
                'reason': {'type': 'string', 'maxLength': 500},
                'source_row_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 48}})
            expanded = object_schema({**schema['properties'], 'failure_findings': {
                'type': 'array', 'items': finding, 'maxItems': len(CHECKS)}})
            value = generate(instructions + ' For each false review check, report the concrete '
                'unsupported fact, missing source, wrong predicate, omitted row, or unsupported proposed '
                'status/text in failure_findings. Use original source row IDs when relevant; never invent '
                'an entity or filter. Approval=false may reference the failed substantive checks. '
                'Verify the proposed disposition: a successful answer must answer the request, whereas '
                'a clarification must truthfully expose its unresolved scope without choosing a subset. '
                'A fixed-count mismatch is not itself evidence of a wrong literal predicate. '
                'Every original boolean still applies; never override a rejection. If all checks pass, '
                'failure_findings must be empty.', context, expanded, **kwargs)
            observed.append({'operation': kwargs['name'], 'model_result': value,
                             'diagnostic_review_rationale_prototype': True})
            if (not isinstance(value, dict) or set(value) != set(expanded['properties'])
                    or not isinstance(value['failure_findings'], list)):
                raise ValueError('native_review_diagnostic_structure_invalid')
            return {key: value[key] for key in schema['properties']}
        value = generate(*positional, **kwargs)
        observed.append({'operation': kwargs['name'], 'model_result': value})
        return value
    client.generate = capture
    store = KnowledgeStore(location, generator=GroundedGenerator(client))
    store.verify_source('anonymous', expected_sha256=case['source_sha256'])
    result, trace = route_native_row_selection(store, case['question'], store.search(case['question'], top_k=4))
    after = implementation_snapshot()
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'exposed_retained_failure_diagnosis_not_replacement_accuracy',
        'baseline_sha256': hashlib.sha256(raw).hexdigest(), 'case_id': case['case_id'],
        'question': case['question'], 'source_sha256': case['source_sha256'],
        'model': 'gpt-6-luna', 'reasoning': 'medium', 'reference_sent_to_model': False,
        'original_failure_preserved': True, 'observed': observed, 'result': result, 'trace': trace,
        'diagnostic_review_rationale_prototype': args.review_rationale,
        'source_replay_passed': bool(result and result.get('native_row_proof') and replay_selection(store, result)),
        'api_audits': safe_audits(client), 'implementation_stable': before == after,
        'implementation_file_sha256_start': before, 'implementation_file_sha256_end': after}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'status': result['status'] if result else None, 'reason': trace.get('reason'),
        'source_replay_passed': report['source_replay_passed'], 'implementation_stable': before == after}))


if __name__ == '__main__':
    main()
