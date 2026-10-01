"""Pinned public Northwind community sample, not an official NL2SQL benchmark."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from fetch_ohr_bench import get_bounded, sha256, ROOT

REVISION = '4f56e7f5906dfd23b25244c5bfe8fb5da6402efd'
URL_ROOT = 'https://raw.githubusercontent.com/jpwhite3/northwind-SQLite3/'+REVISION
DATA = Path('D:/ICT8-OfficialDatasets/northwind')


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    assets = []
    for remote, name, bound in [('dist/northwind.db', 'northwind.db', 30_000_000),
                               ('LICENSE', 'LICENSE', 100_000), ('README.md', 'README.md', 200_000)]:
        path = DATA/name
        raw = get_bounded(URL_ROOT+'/'+remote, bound)
        if path.exists() and path.read_bytes() != raw:
            raise ValueError('existing_pinned_asset_changed')
        if not path.exists(): path.write_bytes(raw)
        assets.append({'path': name, 'url': URL_ROOT+'/'+remote, 'bytes': len(raw), 'sha256': sha256(raw)})
    with sqlite3.connect((DATA/'northwind.db').as_uri()+'?mode=ro', uri=True) as db:
        if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('sample_database_integrity_failed')
        schema = [{'name': name, 'sql': sql} for name, sql in db.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(),
        'dataset': 'jpwhite3/northwind-SQLite3', 'repository_url': 'https://github.com/jpwhite3/northwind-SQLite3',
        'revision': REVISION, 'license': 'MIT (repository LICENSE; retain attribution)',
        'provenance_class': 'community_port_and_extension_of_Northwind_sample_not_official_Microsoft_release',
        'benchmark_class': 'public_sample_database_with_synthetic_development_questions_not_public_NL2SQL_benchmark',
        'data_root_default': str(DATA), 'assets': assets, 'schema': schema,
        'private_project_data_used': False, 'api_called': False}
    target = ROOT/'benchmarks/northwind_generalization/ASSET_MANIFEST.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists(): raise FileExistsError('asset_manifest_already_frozen')
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'tables': [r['name'] for r in schema], 'database_bytes': assets[0]['bytes'],
                      'database_sha256': assets[0]['sha256']}))


if __name__ == '__main__': main()
