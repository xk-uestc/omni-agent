"""Acquire pinned upstream Sakila SQLite scripts; never generate business rows."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import urllib.request

COMMIT = 'e089a5b1ec9af0df7a9c6a5d47d49fa1736a4e84'
REPOSITORY = 'https://github.com/jOOQ/sakila'
DEFAULT = Path('D:/ICT8-OfficialDatasets/sakila-round5-cross-schema-20261002')
FILES = ('README.md', 'sqlite-sakila-db/sqlite-sakila-schema.sql',
         'sqlite-sakila-db/sqlite-sakila-insert-data.sql')
PINNED_BYTES_SHA = {
    'README.md': '103a57a69702c3493fa8778d28a94d95588e6873107adbe2edbbd07d20944bae',
    'sqlite-sakila-db/sqlite-sakila-schema.sql': 'b1db76b3a5192f98493901a9ac40fecb9dba918feb5604b7ef6b74230a385f12',
    'sqlite-sakila-db/sqlite-sakila-insert-data.sql': '4d69a56acbd3f60501e45737353707df0cf51fdb7fcab65cc9db3a72c8c85f34',
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def profile(connection):
    result = []
    for name, ddl in connection.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        columns = [list(row) for row in connection.execute('PRAGMA table_info(' + quote(name) + ')')]
        fks = [list(row) for row in connection.execute('PRAGMA foreign_key_list(' + quote(name) + ')')]
        rows = connection.execute('SELECT * FROM ' + quote(name) + ' ORDER BY rowid').fetchall()
        nulls = {column[1]: connection.execute('SELECT COUNT(*) FROM ' + quote(name)
                    + ' WHERE ' + quote(column[1]) + ' IS NULL').fetchone()[0] for column in columns}
        result.append({'table': name, 'ddl': ddl, 'columns': columns, 'foreign_keys': fks,
                       'row_count': len(rows), 'logical_rows_sha256': sha(canonical(rows)), 'null_counts': nulls})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=DEFAULT)
    parser.add_argument('--reuse-originals', action='store_true')
    parser.add_argument('--database-file', default='sakila.sqlite')
    args = parser.parse_args()
    target = args.directory.resolve()
    db = (target / args.database_file).resolve()
    if not db.is_relative_to(target) or db == target or db.suffix != '.sqlite':
        parser.error('New database must be a .sqlite file within source directory')
    if (target / 'SOURCE_MANIFEST.json').exists() or db.exists():
        parser.error('Source manifest/database exists; refusing overwrite')
    target.mkdir(parents=True, exist_ok=True)
    assets = []
    for relative in FILES:
        path = target / 'original' / relative
        if path.exists() and not args.reuse_originals:
            parser.error('Original source path exists; refusing overwrite')
        url = f'https://raw.githubusercontent.com/jOOQ/sakila/{COMMIT}/{relative}'
        if path.exists():
            raw = path.read_bytes()
        else:
            with urllib.request.urlopen(url, timeout=60) as response:
                raw = response.read()
        if not raw or sha(raw) != PINNED_BYTES_SHA[relative]:
            raise ValueError('Pinned upstream source SHA changed')
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(raw)
        assets.append({'file': path.relative_to(target).as_posix(), 'url': url,
                       'bytes': len(raw), 'sha256': sha(raw)})
        print(json.dumps({'source_file': relative, 'bytes': len(raw), 'sha256': sha(raw)}), flush=True)
    source = target / 'original' / 'sqlite-sakila-db'
    schema = (source / 'sqlite-sakila-schema.sql').read_text(encoding='utf-8-sig')
    inserts = (source / 'sqlite-sakila-insert-data.sql').read_text(encoding='utf-8-sig')
    if not all('License: BSD' in text and 'Copyright DB Software Laboratory' in text
               for text in (schema, inserts)):
        raise ValueError('Upstream license notices changed')
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as connection:
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute('PRAGMA recursive_triggers=OFF')
        connection.executescript(schema)
        triggers = connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
        for name, _ in triggers:
            connection.execute('DROP TRIGGER ' + quote(name))
        # The unchanged upstream INSERT script supplies every business row.
        # Its published NOW audit triggers would otherwise replace source
        # last_update with this computer's clock. Restore exact definitions.
        connection.executescript('BEGIN IMMEDIATE;\n' + inserts + '\nCOMMIT;')
        for _, sql in triggers:
            connection.execute(sql)
        connection.commit()
        connection.execute('PRAGMA foreign_keys=ON')
        violations = connection.execute('PRAGMA foreign_key_check').fetchall()
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        if violations or integrity != 'ok':
            raise ValueError('Source restore integrity/foreign keys failed')
        tables = profile(connection)
        objects = [list(row) for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
    # Independent second restore catches importer/trigger-order differences.
    with sqlite3.connect(':memory:') as verification:
        verification.executescript(schema)
        for name, _ in triggers:
            verification.execute('DROP TRIGGER ' + quote(name))
        verification.executescript('BEGIN;\n' + inserts + '\nCOMMIT;')
        for _, sql in triggers:
            verification.execute(sql)
        if profile(verification) != tables:
            raise ValueError('Independent restore differs')
    manifest = {
        'created_at': datetime.now(timezone.utc).isoformat(), 'source_repository': REPOSITORY,
        'source_commit': COMMIT, 'assets': assets,
        'license': {'upstream_identifier': 'BSD', 'variant': 'not specified in upstream notices',
                    'copyright': 'DB Software Laboratory',
                    'notice_source': 'headers of both unchanged SQLite SQL scripts and upstream README'},
        'data_origin': 'Canonical publicly released Sakila DVD-rental sample, originally MySQL example data; upstream sample data is fictional/generated. No business rows generated by this evaluator.',
        'scope': 'pinned_jooq_sakila_sqlite_distribution_authored_cross_schema_audit_not_official_question_score',
        'native_sqlite_schema_and_insert_scripts': True,
        'schema_sha256': sha(canonical(objects)), 'database': {'file': db.relative_to(target).as_posix(),
            'bytes': db.stat().st_size, 'sha256': sha(db.read_bytes())},
        'tables': tables, 'total_rows': sum(t['row_count'] for t in tables),
        'trigger_policy': {'captured_restored': len(triggers),
            'during_import': 'temporarily suspended in the new database to retain original insert-script last_update values',
            'final': 'all original trigger definitions restored; evaluation connections read only'},
        'import_transaction_policy': 'Unchanged source INSERT statements enclosed in one explicit transaction; original published files retained byte for byte.',
        'importer_sha256': sha(Path(__file__).read_bytes()),
        'validation': {'integrity_check': integrity, 'foreign_key_violations': len(violations),
                       'independent_second_restore_equal': True},
        'exposure': {'prior_project_use_search_matches_before_acquisition': 0,
            'search_terms': ['sakila', 'sqlite-sakila', 'jooq'],
            'search_scope': 'docs,benchmarks,tools,backend,tests before adding this acquisition tool',
            'meaning': 'Not previously used by this project; no claim of foundation-model unfamiliarity with public sample.'},
        'limitations': ['Public fictional example database, not operational customer records.',
            'Authored frozen evaluation questions, not a Sakila official benchmark or leaderboard.',
            'Audit-trigger suspension during initial restore is explicitly disclosed; no question uses last_update.',
            'SQLite NUMERIC/DECIMAL may store binary floating values; reference comparison must define monetary precision.',
            'No native SQL Server/PostgreSQL adaptation claimed.']}
    with (target / 'SOURCE_MANIFEST.json').open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'source_ready', 'tables': len(tables), 'rows': manifest['total_rows'],
                      'database_sha256': manifest['database']['sha256'], 'schema_sha256': manifest['schema_sha256'],
                      'foreign_key_violations': len(violations)}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
