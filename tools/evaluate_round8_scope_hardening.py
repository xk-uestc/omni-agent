"""Pin meaningful local regressions for the single final result-scope patch."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def hashes(directory):
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob('*.py'))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().parent != (ROOT / 'docs').resolve() or args.output.exists():
        parser.error('New report directly in docs required')
    backend = ROOT / 'ict-track8/backend'
    tests = ROOT / 'ict-track8/tests'
    before, tests_before = hashes(backend), hashes(tests)
    temporary = ROOT / 'runtime/test-temp'
    temporary.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'ict-track8'),
           'TEMP': str(temporary), 'TMP': str(temporary), 'TMPDIR': str(temporary)}
    command = [sys.executable, '-m', 'pytest', '-q',
               'ict-track8/tests/test_round8_request_routing.py',
               'ict-track8/tests/test_round8_date_storage.py',
               'ict-track8/tests/test_complete_sql_results_round5.py',
               'ict-track8/tests/test_native_anchor_round5.py']
    started = time.monotonic()
    run = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                         text=True, encoding='utf-8', errors='replace')
    print(run.stdout, end='')
    if run.stderr:
        print(run.stderr, file=sys.stderr, end='')
    counts = {name: int(m.group(1)) if (m := re.search(r'(\d+) ' + name + r'\b', run.stdout)) else 0
              for name in ('passed', 'failed', 'skipped')}
    after, tests_after = hashes(backend), hashes(tests)
    stable = before == after and tests_before == tests_after
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'focused_scope_date_complete_result_and_anchor_regression_not_full_suite',
              'model_called': False, 'command': command[1:], 'exit_code': run.returncode,
              'duration_seconds': round(time.monotonic() - started, 3), **counts,
              'implementation_stable': stable,
              'implementation_file_sha256_start': before,
              'implementation_file_sha256_end': after,
              'test_file_sha256_start': tests_before, 'test_file_sha256_end': tests_after,
              'stdout': run.stdout, 'stderr': run.stderr,
              'ok': run.returncode == 0 and counts['failed'] == 0 and counts['passed'] >= 50 and stable}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
