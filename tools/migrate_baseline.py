"""Create a verified independent baseline; never modify the source project."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path('D:/lanqun-site/ragv6-standalone')
TARGET = Path(__file__).resolve().parents[1]
PDF = Path('D:/快速交换/8 赛题八：网信产业应用赛道-多模态数据驱动的可解释精准问数问答智能体（中电云计算技术有限公司）.pdf')
SKIP = {'.git', '.claude', '__pycache__', '.pytest_cache', '.ruff_cache', 'node_modules', '.venv', 'venv', 'dist', 'runtime'}


def main():
    backup = TARGET / 'backups' / 'baseline-20261001.zip'
    if backup.exists():
        raise SystemExit('Baseline already exists; refusing to overwrite it.')
    selected = []
    for current, directories, files in os.walk(SOURCE / 'ict-track8'):
        directories[:] = [name for name in directories if name not in SKIP]
        for name in files:
            path = Path(current) / name
            if name.startswith('.env') or name in {'auth.json', 'config.toml'} or path.suffix.lower() in {'.pyc', '.log', '.zip', '.pem', '.key'}:
                continue
            selected.append(path)
    selected.extend(path for path in (SOURCE / 'docs').glob('*.md') if 'ICT_TRACK8' in path.name or 'NL2SQL' in path.name)
    selected.append(SOURCE / 'scripts/start-track8.ps1')
    required_bytes = sum(p.stat().st_size for p in selected)
    if shutil.disk_usage(TARGET).free < required_bytes * 3 + 100_000_000:
        raise SystemExit(f'Insufficient space for verified migration: {required_bytes} bytes')
    collisions = [str(path.relative_to(SOURCE)) for path in selected if (TARGET / path.relative_to(SOURCE)).exists()]
    if collisions:
        raise SystemExit('Destination collisions: ' + ', '.join(collisions))
    backup.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    with zipfile.ZipFile(backup, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(selected):
            relative = path.relative_to(SOURCE)
            destination = TARGET / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            data = destination.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest != hashlib.sha256(path.read_bytes()).hexdigest():
                raise RuntimeError(f'Copy mismatch: {relative}')
            archive.writestr(relative.as_posix(), data)
            entries.append({'path': relative.as_posix(), 'bytes': len(data), 'sha256': digest})
        manifest = {
            'created_at': datetime.now(timezone.utc).isoformat(),
            'source_head': subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip(),
            'includes_uncommitted_changes': True,
            'files': entries,
            'excluded_directories': sorted(SKIP),
        }
        archive.writestr('BASELINE_MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(backup) as archive:
        for item in entries:
            if hashlib.sha256(archive.read(item['path'])).hexdigest() != item['sha256']:
                raise RuntimeError('Archive mismatch: ' + item['path'])
    (TARGET / 'docs' / 'BASELINE_MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    specification = TARGET / 'specification'
    specification.mkdir(exist_ok=True)
    shutil.copy2(PDF, specification / 'official-track8.pdf')
    from pypdf import PdfReader
    reader = PdfReader(PDF)
    (specification / 'official-track8.txt').write_text('\n\n'.join(f'## 第 {i+1} 页\n{p.extract_text() or ""}' for i, p in enumerate(reader.pages)), encoding='utf-8')
    print(json.dumps({'root': str(TARGET), 'files': len(entries), 'source_bytes': required_bytes, 'verified_backup': str(backup), 'backup_bytes': backup.stat().st_size, 'specification_pages': len(reader.pages)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
