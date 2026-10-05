"""Read-only live API cap checks, independently replaying the unchanged SQL."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().parent != ROOT / 'docs':
        parser.error('Use a new report directly in docs')
    files = ['ict-track8/backend/nl2sql/engine.py', 'ict-track8/backend/nl2sql/result_scope.py']
    def hashes():
        return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    before = hashes()
    database_sha = hashlib.sha256(args.database.read_bytes()).hexdigest()
    checks = []
    for question in ('销售额是多少', '2019年销售额'):
        request = Request('http://127.0.0.1:8030/api/v1/nl2sql/query',
            data=json.dumps({'question':question, 'max_rows':1}).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(request, timeout=300) as response:
            result = json.load(response)
        with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro', uri=True) as db:
            cursor = db.execute(result['sql'], result['parameters'])
            columns = [item[0] for item in cursor.description]
            replay = cursor.fetchall()
        cells = [tuple(row[name] for name in columns) for row in result['rows']]
        passed = (result['status']=='ok' and cells==replay and len(cells)<=1
            and result['provenance']['result_completeness']=='within_return_limit'
            and result['provenance'].get('output_cardinality',{}).get('maximum_output_rows')==1
            and not any('达到返回上限' in item for item in result.get('notices',[])))
        checks.append({'question':question, 'passed':passed, 'result':result,
                       'independent_sqlite_cells':[list(row) for row in replay]})
    stable = before==hashes() and database_sha==hashlib.sha256(args.database.read_bytes()).hexdigest()
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'two_live_http_scalar_caps_not_general_sql_accuracy',
        'source_sha256':before, 'source_stable':stable, 'database_sha256':database_sha,
        'checks':checks, 'passed':sum(item['passed'] for item in checks), 'total':len(checks)}
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    print(json.dumps({'passed':report['passed'], 'total':report['total'], 'stable':stable}))
    return 0 if stable and all(item['passed'] for item in checks) else 1


if __name__ == '__main__':
    raise SystemExit(main())
