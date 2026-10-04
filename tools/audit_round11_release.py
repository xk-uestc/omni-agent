"""Pin round-eleven reports to current source without changing any score."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from audit_round6_release import api_summary, verify_source

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    raw = path.read_bytes()
    return json.loads(raw), {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().parent != (ROOT / 'docs').resolve() or args.output.exists():
        parser.error('New docs report required')
    local, local_pin = load(ROOT / 'docs/LOCAL_REGRESSION_ROUND11_FINAL2_20261004.json')
    manifest, manifest_pin = load(args.run_directory / 'RUN_MANIFEST.json')
    score, score_pin = load(ROOT / 'docs/SAKILA_ROUND11_ARTIFACT_DELIVERY_FINAL2_20261004.json')
    http, http_pin = load(ROOT / 'docs/ROUND11_HTTP_FINAL_20261004.json')
    current = verify_source(local)
    if verify_source(manifest) != current:
        raise ValueError('source_version_mismatch')
    http_start, http_end = http.get('implementation_file_sha256'), http.get('implementation_file_sha256_end')
    if http_start != http_end or {k: v for k, v in http_end.items() if k in current} != current:
        raise ValueError('http_source_version_mismatch')
    if local['failed'] or not local['ok'] or not http['ok']:
        raise ValueError('regression_or_http_failed')
    observed = args.run_directory / 'observed.jsonl'
    observed_sha = hashlib.sha256(observed.read_bytes()).hexdigest()
    rows = [json.loads(line) for line in observed.read_text(encoding='utf-8').splitlines()]
    if (len(rows) != 56 or len({r['case_id'] for r in rows}) != 56
            or manifest['reference_opened'] is not False or not manifest['source_stable']
            or score['planned_total'] != 56 or score['executed_total'] != 56
            or not score['stable'] or score['observed_sha256'] != observed_sha
            or manifest['observed_sha256'] != observed_sha
            or score['run_manifest_sha256'] != manifest_pin['sha256']):
        raise ValueError('run_denominator_or_provenance_invalid')
    api = api_summary([a for r in rows for a in r['api_audits']],
                      dropped=sum(r['api_audit_dropped'] for r in rows))
    diagnostics = [{'case_id': r['case_id'], 'error_type': r['error_type'],
                    'safety_diagnostics': r.get('safety_diagnostics')}
                   for r in rows if r['error_type']]
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'source_audit': 'PASS',
        'scope': 'source_consistency_not_goal_completion_or_accuracy_certificate',
        'backend_file_sha256': current, 'report_pins': {'local': local_pin,
            'manifest': manifest_pin, 'score': score_pin, 'http': http_pin},
        'local_passed': local['passed'], 'sql_passed': score['passed'], 'sql_total': 56,
        'session_turns_passed': score['session_turns_passed'],
        'sessions_all_passed': score['sessions_all_passed'], 'api_summary': api,
        'runtime_failures': diagnostics, 'full_goal_completed': False,
        'limitations': ['Exposed development replay, not official or independent blind accuracy.',
            'No new OHR document accuracy measurement in this SQL-focused round.',
            'Candidate interrupted runs and old failures are retained separately.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: report[k] for k in ('source_audit', 'local_passed', 'sql_passed', 'sql_total')}))


if __name__ == '__main__':
    main()
