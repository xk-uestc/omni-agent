"""Freeze synthetic cross-schema QA before system testing, refs are scoring-only."""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from fetch_ohr_bench import ROOT, sha256


def main():
    folder = ROOT/'benchmarks/northwind_generalization'
    output = folder/'MANIFEST.json'
    if output.exists(): raise FileExistsError('questions_already_frozen')
    asset_bytes = (folder/'ASSET_MANIFEST.json').read_bytes(); assets = json.loads(asset_bytes)
    raw = (folder/'questions.json').read_bytes(); cases = json.loads(raw)
    if len(cases) != 16 or len({c['id'] for c in cases}) != 16:
        raise ValueError('question_contract_invalid')
    dbpath = Path(assets['data_root_default'])/'northwind.db'
    digest = sha256(dbpath.read_bytes())
    if digest != assets['assets'][0]['sha256']: raise ValueError('sample_database_sha_changed')
    # Frozen questions are authored by schema/operator coverage, before any
    # system result. Reference SQL syntax validation cannot discard hard cases.
    with sqlite3.connect(dbpath.as_uri()+'?mode=ro', uri=True) as db:
        for case in cases:
            if not case['reference_sql'].lstrip().upper().startswith(('SELECT','WITH')):
                raise ValueError('reference_must_be_readonly')
            db.execute('EXPLAIN QUERY PLAN '+case['reference_sql']).fetchall()
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'new_schema_public_sample_with_synthetic_development_queries_not_official_benchmark',
        'selection_frozen_before_system_execution': True, 'model_called': False,
        'questions_path': 'questions.json', 'questions_sha256': sha256(raw),
        'asset_manifest_sha256': sha256(asset_bytes), 'database_sha256': digest,
        'data_root_default': assets['data_root_default'], 'database_path': 'northwind.db',
        'cases': [{'id': c['id'], 'category': c['category'],
                   'row_sha256': sha256(json.dumps(c, ensure_ascii=False, sort_keys=True).encode())} for c in cases],
        'scoring': {'reference_sql_usage': 'scoring_connection_only_never_provider_or_aliases_or_catalog',
                    'rows': 'multiset of typed cell values per row; aliases and column ordering ignored; duplicate rows retained',
                    'numeric_absolute_tolerance': '0.0000001',
                    'ranking': 'result set plus explicit descending numeric order for ranking question',
                    'failure_policy': 'all sixteen cases retained, refusal/exception scored unsuccessful'},
        'limitations': ['New domain/schema, not unseen official NL2SQL benchmark queries.',
            'Synthetic schema-explicit Chinese questions include literal table/column names; not representative unassisted user accuracy.',
            'Northwind is a community port/extension of a sample database, not an official Microsoft publication.',
            'One run/version cannot establish statistical significance.'],
        'private_project_data_used': False}
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'questions': len(cases), 'categories': len({c['category'] for c in cases}),
                      'questions_sha256': manifest['questions_sha256'], 'system_or_api_called': False}))


if __name__ == '__main__': main()
