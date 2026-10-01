"""Compare saved system execution results, retaining fallback and every failure."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def compare(baseline_path, optimized_path):
    paths = [Path(baseline_path), Path(optimized_path)]
    raw = [path.read_bytes() for path in paths]
    before, after = [json.loads(data) for data in raw]
    for run in (before, after):
        if run.get('implementation_stable') is not True or run.get('database_unchanged') is not True:
            raise ValueError('source_or_database_not_stable')
        if run.get('gold_sent_to_model') is not False:
            raise ValueError('gold_protocol_not_verified')
        if run.get('model') != 'gpt-6-luna' or run.get('reasoning') != 'medium':
            raise ValueError('model_protocol_mismatch')
    for key in ('manifest_sha256', 'questions_sha256', 'database_sha256', 'total'):
        if before.get(key) is None or before[key] != after.get(key):
            raise ValueError('paired_input_mismatch')
    old, new = [{case['id']: case for case in run['cases']} for run in (before, after)]
    if len(old) != before['total'] or len(new) != after['total'] or set(old) != set(new):
        raise ValueError('paired_cases_not_complete_or_unique')
    changes = []
    for identifier, b in old.items():
        a = new[identifier]
        for key in ('question', 'category', 'reference_sql_scoring_only', 'expected_rows'):
            if b.get(key) != a.get(key):
                raise ValueError('paired_question_or_gold_mismatch')
        for row in (b, a):
            if type(row.get('pass')) is not bool:
                raise ValueError('saved_pass_not_boolean')
        changes.append({'id': identifier, 'category': b['category'], 'question': b['question'],
                        'before_pass': b['pass'], 'after_pass': a['pass'],
                        'before_status': b['result']['status'], 'after_status': a['result']['status'],
                        'before_checks': b['checks'], 'after_checks': a['checks']})

    def metrics(run):
        cases = run['cases']
        passed = sum(case['pass'] for case in cases)
        if passed != run['passed']:
            raise ValueError('saved_summary_not_consistent')
        validated = lambda case: case['result'].get('plan', {}).get('planner_source') == 'model_validated'
        audits = [a for case in cases for a in case.get('api_audits', [])]
        dropped = sum(case.get('audit_dropped_count', 0) for case in cases)
        return {'passed': passed, 'total': run['total'],
                'model_validated_passed': sum(case['pass'] and validated(case) for case in cases),
                'fallback_passed': sum(case['pass'] and not validated(case) for case in cases),
                'retained_api_calls': len(audits), 'dropped_api_calls': dropped,
                'completed_api_calls': sum(a.get('status') == 'completed' for a in audits),
                'reported_tokens': sum(a.get('total_tokens', 0) or 0 for a in audits)}
    return {'scope': 'paired_public_example_database_authored_questions_system_execution_not_official_nl2sql_benchmark',
            'baseline': {'path': str(paths[0]), 'sha256': hashlib.sha256(raw[0]).hexdigest(), **metrics(before)},
            'optimized': {'path': str(paths[1]), 'sha256': hashlib.sha256(raw[1]).hexdigest(), **metrics(after)},
            'better_cases': sum(not row['before_pass'] and row['after_pass'] for row in changes),
            'worse_cases': sum(row['before_pass'] and not row['after_pass'] for row in changes),
            'cases': changes,
            'limitations': ['One run per version cannot isolate model variation or prove statistical significance.',
                            'System execution may include rules fallback; strict model-validated success is counted separately.',
                            'Schema-explicit authored questions and one public database do not prove general cross-domain accuracy.',
                            'Saved outputs/checks are used verbatim; no answers, reference SQL or failures are edited.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('baseline', type=Path)
    parser.add_argument('optimized', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.baseline, args.optimized)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({key: result[key] for key in ('baseline', 'optimized', 'better_cases', 'worse_cases')}))
