"""Pin Round 12 local, live SQL, and HTTP evidence to one source version."""
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
    if args.output.resolve().parent != ROOT / 'docs' or args.output.exists():
        parser.error('New docs report required')
    local, local_pin = load(ROOT / 'docs/LOCAL_REGRESSION_ROUND12_FINAL2_20261004.json')
    manifest, manifest_pin = load(args.run_directory / 'RUN_MANIFEST.json')
    score, score_pin = load(ROOT / 'docs/SAKILA_ROUND12_ARTIFACT_DELIVERY_FINAL2_20261004.json')
    inline, inline_pin = load(ROOT / 'docs/SAKILA_ROUND12_INLINE_FINAL2_20261004.json')
    http, http_pin = load(ROOT / 'docs/ROUND12_HTTP_FINAL2_20261004.json')
    current = verify_source(local)
    if verify_source(manifest) != current:
        raise ValueError('source_version_mismatch')
    http_start, http_end = http.get('implementation_file_sha256'), http.get('implementation_file_sha256_end')
    if http_start != http_end or {k: v for k, v in http_end.items() if k in current} != current:
        raise ValueError('http_source_version_mismatch')
    if not local['ok'] or local['failed'] or not http['ok']:
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
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'source_audit': 'PASS',
        'scope': 'source_consistency_not_official_accuracy_or_full_goal_completion',
        'backend_file_sha256': current, 'report_pins': {'local': local_pin, 'manifest': manifest_pin,
            'artifact_delivery': score_pin, 'inline': inline_pin, 'http': http_pin},
        'local_regression': {key: local[key] for key in
            ('passed', 'failed', 'skipped', 'warnings', 'subtests_passed')},
        'artifact_delivery': {key: score[key] for key in
            ('passed', 'planned_total', 'inline_passed', 'session_turns_passed',
             'sessions_all_passed', 'artifact_cases')},
        'legacy_inline': {key: inline[key] for key in
            ('passed', 'planned_total', 'session_turns_passed', 'sessions_all_passed')},
        'http_checks_passed': sum(http['checks'].values()), 'http_check_total': len(http['checks']),
        'api_summary': api, 'full_goal_completed': False,
        'limitations': ['Exposed development set, not an official or blind accuracy score.',
            'Session results changed between live replays; record score and API variability together.',
            'No new OHR complex-document accuracy measurement in this SQL-focused round.']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'source_audit': report['source_audit'],
        'sql_passed': score['passed'], 'sql_total': score['planned_total'],
        'local_passed': local['passed']}))


if __name__ == '__main__':
    main()
