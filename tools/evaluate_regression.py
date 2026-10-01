"""Run the complete local regression and save its actual summary, without API calls."""
import json
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    started = time.monotonic()
    result = subprocess.run([sys.executable, '-m', 'pytest', 'ict-track8/tests', '-q'],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(result.stdout, end='')
    if result.stderr:
        print(result.stderr, file=sys.stderr, end='')
    counts = {name: int(match.group(1)) if (match := re.search(r'(\d+) ' + name + r'\b', result.stdout)) else 0
              for name in ('passed', 'failed', 'skipped', 'warnings')}
    # pytest prints "1 warning", not "1 warnings".
    counts['warnings'] = sum(int(x) for x in re.findall(r'(\d+) warnings?\b', result.stdout))
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'complete_local_contract_regression_not_model_accuracy', 'model_called': False,
              'command': 'python -m pytest ict-track8/tests -q', 'exit_code': result.returncode,
              'duration_seconds': round(time.monotonic() - started, 3),
              'environment': {'python': platform.python_version(), 'platform': platform.platform()},
              **counts, 'ok': result.returncode == 0 and counts['passed'] > 0 and counts['failed'] == 0}
    (ROOT / 'docs/LOCAL_REGRESSION_REPORT.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
