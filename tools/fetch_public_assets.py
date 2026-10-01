"""Fetch pinned public model assets and the officially recommended Chinook database."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
MODEL_REVISION = '7999e1d3359715c523056ef9478215996d62a620'
MODEL_FILES = ['config.json', 'model.safetensors', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt', 'README.md']


def download(url, path):
    if path.exists():
        return {'path': path.relative_to(ROOT).as_posix(), 'url': url, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
    path.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(ROOT).free < 220_000_000:
        raise RuntimeError('Insufficient E-drive space for model and delivery artifacts')
    temporary = path.with_suffix(path.suffix + '.download')
    with requests.get(url, timeout=(10, 45), stream=True) as response:
        response.raise_for_status()
        total = 0
        with temporary.open('wb') as stream:
            for chunk in response.iter_content(256*1024):
                total += len(chunk)
                if total > 180_000_000:
                    raise RuntimeError('Public asset exceeds download bound')
                stream.write(chunk)
    temporary.replace(path)
    result = {'path': path.relative_to(ROOT).as_posix(), 'url': url, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
    print(json.dumps({'downloaded': result['path'], 'bytes': result['bytes']}, ensure_ascii=False), flush=True)
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dense', action='store_true')
    parser.add_argument('--chinook', action='store_true')
    args = parser.parse_args()
    entries = []
    if args.dense:
        for name in MODEL_FILES:
            entries.append(download(f'https://huggingface.co/BAAI/bge-small-zh-v1.5/resolve/{MODEL_REVISION}/{name}', ROOT / 'models/bge-small-zh-v1.5' / name))
        (ROOT / 'models/bge-small-zh-v1.5/ASSET_MANIFEST.json').write_text(json.dumps({'model': 'BAAI/bge-small-zh-v1.5', 'revision': MODEL_REVISION, 'license': 'MIT', 'files': entries}, ensure_ascii=False, indent=2), encoding='utf-8')
    if args.chinook:
        item = download('https://raw.githubusercontent.com/lerocha/chinook-database/master/ChinookDatabase/DataSources/Chinook_Sqlite.sqlite', ROOT / 'benchmarks/chinook/Chinook.sqlite')
        entries.append(item)
        license_item = download('https://raw.githubusercontent.com/lerocha/chinook-database/master/LICENSE.md', ROOT / 'benchmarks/chinook/LICENSE.md')
        entries.append(license_item)
        with sqlite3.connect((ROOT / item['path']).resolve().as_uri() + '?mode=ro', uri=True) as connection:
            integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        if integrity != 'ok' or len(tables) < 10:
            raise RuntimeError('Invalid public database asset')
        (ROOT / 'benchmarks/chinook/ASSET_MANIFEST.json').write_text(json.dumps({'source': 'lerocha/chinook-database', 'integrity': integrity, 'tables': tables, 'files': [item, license_item]}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'assets': len(entries), 'bytes': sum(item['bytes'] for item in entries)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
