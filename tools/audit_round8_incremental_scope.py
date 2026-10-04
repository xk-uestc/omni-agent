"""Audit one result-scope hardening patch, retaining prior full-run evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--focused', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    for path in (args.baseline, args.focused, args.output):
        if path.resolve().parent != (ROOT / 'docs').resolve():
            parser.error('Reports must be directly in docs')
    if args.output.exists():
        parser.error('Never overwrite reports')
    baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
    focused = json.loads(args.focused.read_text(encoding='utf-8'))
    current = {p.relative_to(ROOT).as_posix(): digest(p)
               for p in sorted((ROOT / 'ict-track8/backend').rglob('*.py'))}
    if baseline.get('source_audit') != 'PASS':
        raise ValueError('baseline_source_audit_failed')
    changed = {path for path in current
               if current[path] != baseline['backend_file_sha256'].get(path)}
    if set(current) != set(baseline['backend_file_sha256']) or changed != {
            'ict-track8/backend/nl2sql/result_scope.py'}:
        raise ValueError('incremental_scope_not_exactly_the_allowed_backend_file')
    if (not focused.get('ok') or focused.get('model_called') is not False
            or focused.get('implementation_stable') is not True
            or focused.get('failed') != 0 or focused.get('passed', 0) < 50
            or focused['implementation_file_sha256_start'] != current
            or focused['implementation_file_sha256_end'] != current):
        raise ValueError('focused_regression_not_current_or_not_passed')
    report = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'single_result_scope_hardening_after_retained_full_validation',
        'source_audit': 'PASS', 'full_model_suite_rerun_after_patch': False,
        'full_local_suite_rerun_after_patch': False,
        'baseline_local_regression_passed': baseline.get('local_regression_passed', False),
        'baseline_regression': baseline.get('regression'),
        'baseline': {'path': str(args.baseline), 'sha256': digest(args.baseline)},
        'focused': {'path': str(args.focused), 'sha256': digest(args.focused),
                    'passed': focused['passed'], 'failed': focused['failed']},
        'changed_backend_files': sorted(changed),
        'current_backend_file_sha256': current,
        'current_backend_lf_normalized_sha256': {
            path: hashlib.sha256((ROOT / path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for path in current},
        'limitations': [
            'Prior API scores describe the retained pre-patch FINAL4 version.',
            'Focused tests cover the punctuation guard; they do not replace a full model or local suite rerun.',
            'A source provenance PASS never changes retained full regression failures into passes.',
        ],
    }
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'source_audit': 'PASS', 'focused_passed': focused['passed'],
                      'changed_backend_files': sorted(changed)}))


if __name__ == '__main__':
    main()
