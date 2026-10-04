"""Record focused regressions of complete source answers and their integrations."""
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
        parser.error('New docs report required')
    names = ['test_round10_multi_source_spans.py', 'test_round10_multi_source_store.py',
        'test_source_span_answer.py', 'test_source_span_store.py', 'test_answer_completeness.py',
        'test_grounded_span_answer.py', 'test_grounded_span_store.py', 'test_grounded_span_quote_catalog.py',
        'test_evidence_recovery_round5.py', 'test_round9_visual_selection_contract.py',
        'test_round10_process_sessions.py']
    before = hashes(ROOT / 'ict-track8/backend')
    tests_before = hashes(ROOT / 'ict-track8/tests')
    command = [sys.executable, '-m', 'pytest', '-q', *['ict-track8/tests/' + name for name in names]]
    temporary = ROOT / 'runtime/test-temp'
    temporary.mkdir(exist_ok=True)
    started = time.monotonic()
    run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace',
        env={**os.environ, 'PYTHONPATH': str(ROOT / 'ict-track8'), 'TEMP': str(temporary),
             'TMP': str(temporary), 'TMPDIR': str(temporary)})
    print(run.stdout, end='')
    if run.stderr:
        print(run.stderr, file=sys.stderr, end='')
    counts = {key: int(m.group(1)) if (m := re.search(r'(\d+) ' + key + r'\b', run.stdout)) else 0
              for key in ('passed', 'failed', 'skipped')}
    after = hashes(ROOT / 'ict-track8/backend')
    tests_after = hashes(ROOT / 'ict-track8/tests')
    stable = before == after and tests_before == tests_after
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'scope': 'focused_source_answer_contracts_not_full_suite',
        'model_called': False, 'command': command[1:], 'exit_code': run.returncode,
        'duration_seconds': round(time.monotonic() - started, 3), **counts,
        'implementation_file_sha256_start': before, 'implementation_file_sha256_end': after,
        'test_file_sha256_start': tests_before, 'test_file_sha256_end': tests_after,
        'implementation_stable': stable, 'stdout': run.stdout, 'stderr': run.stderr,
        'ok': stable and run.returncode == 0 and counts['failed'] == 0 and counts['passed'] >= 100}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
