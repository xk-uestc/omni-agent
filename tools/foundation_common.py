"""Local F1 evidence helpers. Never reads environment values or private files."""
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT / 'ict-track8/backend').rglob('*.py'))}


def environment():
    return {'python': sys.version, 'platform': platform.platform(),
            'sqlite': __import__('sqlite3').sqlite_version,
            'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()}


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')


def percentile(values, q):
    import math
    return sorted(values)[max(0, math.ceil(len(values) * q) - 1)] if values else None
