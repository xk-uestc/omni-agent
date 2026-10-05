"""Compare identical retained page-scale samples without reruns or rescoring."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    raw = path.read_bytes()
    report = json.loads(raw)
    if (report.get('implementation_stable') is not True
            or report.get('reference_sent_to_model') is not False
            or report.get('model') != 'gpt-6-luna' or report.get('reasoning') != 'medium'):
        raise ValueError('stable_authorized_reference_isolated_reports_required')
    return report, hashlib.sha256(raw).hexdigest()


def samples(record):
    return {s['request']: s for s in [record['cold_answer'],
        *(s for group in record['concurrency'] for s in group['samples'])]}


def supported(sample):
    return (sample['status'] == 'ok' and sample['literal_answer_match_scoring_only'] is True
        and sample['expected_page_cited_scoring_only'] is True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().parent != (ROOT/'docs').resolve():
        parser.error('New comparison in docs required')
    before, before_sha = read(args.before)
    after, after_sha = read(args.after)
    old = {r['pages']: r for r in before['records']}
    new = {r['pages']: r for r in after['records']}
    if set(old) != {10, 100, 500} or set(new) != set(old):
        raise ValueError('same_three_scales_required')
    pairs = []
    for pages, left in old.items():
        right = new[pages]
        for key in ('pdf_sha256', 'question', 'reference_scoring_only', 'expected_page_scoring_only', 'chunks'):
            if left[key] != right[key]:
                raise ValueError('original_sample_or_question_changed')
        ls, rs = samples(left), samples(right)
        if set(ls) != set(rs):
            raise ValueError('same_request_groups_required')
        for identifier, x in ls.items():
            y = rs[identifier]
            pairs.append({'pages': pages, 'request': identifier,
                'before_status': x['status'], 'after_status': y['status'],
                'before_literal_and_source_supported': supported(x),
                'after_literal_and_source_supported': supported(y),
                'before_wall_ms': x['wall_ms'], 'after_wall_ms': y['wall_ms'],
                'before_model_calls': len(x['api_audits']), 'after_model_calls': len(y['api_audits']),
                'before_input_tokens': sum(a.get('input_tokens') or 0 for a in x['api_audits']),
                'after_input_tokens': sum(a.get('input_tokens') or 0 for a in y['api_audits']),
                'before_operations': [a.get('operation') for a in x['api_audits']],
                'after_operations': [a.get('operation') for a in y['api_audits']]})
    calls_before = sum(p['before_model_calls'] for p in pairs)
    calls_after = sum(p['after_model_calls'] for p in pairs)
    summary = {'paired_requests': len(pairs),
        'before_literal_and_source_supported': sum(p['before_literal_and_source_supported'] for p in pairs),
        'after_literal_and_source_supported': sum(p['after_literal_and_source_supported'] for p in pairs),
        'before_model_calls': calls_before, 'after_model_calls': calls_after,
        'model_call_reduction_fraction': round(1-calls_after/calls_before, 6) if calls_before else None,
        'before_input_tokens': sum(p['before_input_tokens'] for p in pairs),
        'after_input_tokens': sum(p['after_input_tokens'] for p in pairs),
        'before_observed_wall_ms_sum': round(sum(p['before_wall_ms'] for p in pairs), 3),
        'after_observed_wall_ms_sum': round(sum(p['after_wall_ms'] for p in pairs), 3)}
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'same_original_synthetic_PDF_and_question_observations_not_controlled_speedup_or_accuracy',
        'before_report_sha256': before_sha, 'after_report_sha256': after_sha,
        'model_calls_by_this_comparison': 0, 'summary': summary, 'pairs': pairs,
        'limitations': ['Different provider/network/load/cache conditions; no controlled speedup proof.',
            'Literal answer and page diagnostics are not official whole-question correctness.',
            'Every retained request is compared; failures are not removed or replaced.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
