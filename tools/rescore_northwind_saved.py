"""Re-score saved outputs with a corrected evaluator, never run model/backend."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from evaluate_northwind_generalization import assess, load_frozen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.report.read_bytes()
    report = json.loads(raw)
    manifest, manifest_raw, frozen, database = load_frozen()
    if (report['manifest_sha256'] != hashlib.sha256(manifest_raw).hexdigest()
            or report['questions_sha256'] != manifest['questions_sha256']
            or report['database_sha256'] != manifest['database_sha256']):
        raise ValueError('saved_report_frozen_case_mismatch')
    cases_by_id = {case['id']: case for case in frozen}
    if (len(report['cases']) != report['total'] or report['total'] != len(frozen)
            or len({case['id'] for case in report['cases']}) != len(frozen)
            or {case['id'] for case in report['cases']} != set(cases_by_id)):
        raise ValueError('saved_report_case_set_incomplete')
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True) as scoring:
        expected_by_id = {case['id']: [list(row) for row in scoring.execute(case['reference_sql']).fetchall()]
                          for case in frozen}
    changed = []
    for case in report['cases']:
        old_checks, old_pass = case['checks'], case['pass']
        scoring_case = cases_by_id[case['id']]
        if (case['question'] != scoring_case['question']
                or case['reference_sql_scoring_only'] != scoring_case['reference_sql']
                or case['expected_rows'] != expected_by_id[case['id']]):
            raise ValueError('saved_question_or_reference_sql_changed')
        case['checks'] = {**old_checks, **assess(scoring_case, case['result'], case['expected_rows'])}
        case['pass'] = all(case['checks'].values())
        if old_checks != case['checks']:
            changed.append({'id': case['id'], 'before_checks': old_checks, 'after_checks': case['checks'],
                            'before_pass': old_pass, 'after_pass': case['pass']})
    report['passed'] = sum(case['pass'] for case in report['cases'])
    report['rescoring'] = {'original_path': str(args.report),
        'original_sha256': hashlib.sha256(raw).hexdigest(), 'program_or_api_rerun': False,
        'evaluator_sha256': hashlib.sha256(Path(__file__).with_name('evaluate_northwind_generalization.py').read_bytes()).hexdigest(),
        'reason': 'Explicit dense-rank question may include verified optional rank display; no other extra columns ignored.',
        'changed_cases': changed, 'production_improvement': False}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'passed':report['passed'], 'total':report['total'], 'changed_cases':changed}, ensure_ascii=False))


if __name__ == '__main__':
    main()
