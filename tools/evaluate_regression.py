"""Run the complete local regression and save its actual summary, without API calls."""
import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def source_hashes():
    paths = sorted([*(ROOT / 'ict-track8/backend').rglob('*.py'),
                    *(ROOT / 'ict-track8/tests').rglob('*.py'), Path(__file__)])
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New report directly in docs; never overwrite history')
    args = parser.parse_args()
    output = args.output or ROOT / ('docs/LOCAL_REGRESSION_' +
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ') + '.json')
    if output.resolve().parent != (ROOT / 'docs').resolve() or output.suffix != '.json' or output.exists():
        parser.error('Output must be a new JSON directly in docs')
    before = source_hashes()
    started = time.monotonic()
    environment = {**os.environ, 'PYTHONPATH': str(ROOT / 'ict-track8')}
    result = subprocess.run([sys.executable, '-m', 'pytest', 'ict-track8/tests', '-q'],
                            cwd=ROOT, env=environment, capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    print(result.stdout, end='')
    if result.stderr:
        print(result.stderr, file=sys.stderr, end='')
    counts = {name: int(match.group(1)) if (match := re.search(r'(\d+) ' + name + r'\b', result.stdout)) else 0
              for name in ('passed', 'failed', 'skipped', 'warnings')}
    # pytest prints "1 warning", not "1 warnings".
    counts['warnings'] = sum(int(x) for x in re.findall(r'(\d+) warnings?\b', result.stdout))
    after = source_hashes()
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'complete_local_contract_regression_not_model_accuracy', 'model_called': False,
              'command': 'python -m pytest ict-track8/tests -q', 'exit_code': result.returncode,
              'duration_seconds': round(time.monotonic() - started, 3),
              'environment': {'python': platform.python_version(), 'platform': platform.platform()},
              **counts, 'subtests_passed': sum(int(x) for x in re.findall(r'(\d+) subtests? passed', result.stdout)),
              'implementation_file_sha256_start': before, 'implementation_file_sha256_end': after,
              'implementation_stable': before == after,
              'ok': result.returncode == 0 and counts['passed'] > 0 and counts['failed'] == 0 and before == after}
    with output.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return result.returncode if before == after else 2


if __name__ == '__main__':
    raise SystemExit(main())
