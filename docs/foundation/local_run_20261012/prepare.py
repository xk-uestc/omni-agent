"""Provision isolated local deployment data; do not edit backend or tracked DB.

Run after installing the saved native/BM25/local-OCR environment. No model
credentials are read. --fetch-native-pdf downloads one pinned public original.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
RESOURCES = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native-pdf', type=Path)
    p.add_argument('--fetch-native-pdf', action='store_true')
    a = p.parse_args()
    run = ROOT / 'runtime/local_run_20261012'; run.mkdir(parents=True, exist_ok=True)
    for name in ('asgi.py', 'launch.sh', 'budget_transport.py', 'verify_http.py', 'ingest_samples.py'):
        shutil.copyfile(RESOURCES / name, run / name)
    (run / 'launch.sh').chmod(0o755)
    sys.path.insert(0, str(ROOT / 'ict-track8'))
    from backend.nl2sql.seed import initialize_database, initialize_rich_demo_data
    db = run / 'demo.sqlite'
    if not db.exists():
        initialize_database(db); initialize_rich_demo_data(db)
    with sqlite3.connect(db) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS 材料采购(记录ID INTEGER PRIMARY KEY, 材料原价 REAL)')
        connection.execute('INSERT OR IGNORE INTO 材料采购 VALUES(1,500)')
        connection.execute('CREATE TABLE IF NOT EXISTS 保险参数(记录ID INTEGER PRIMARY KEY, 材料运输保险费率 REAL)')
        connection.execute('INSERT OR IGNORE INTO 保险参数 VALUES(1,0.02)')
    cat = json.loads((ROOT / 'ict-track8/data/demo_metric_catalog.json').read_text())
    cat['version'] += '-local-run-transparent-formula-mapping'
    cat['metrics'] += [
        {'id': 'local_material_price', 'table': '材料采购', 'column': '材料原价', 'function': 'SUM',
         'label': '材料原价', 'query_alias': '材料原价', 'aliases': ['材料原价'], 'unit': 'currency', 'currency': 'CNY'},
        {'id': 'local_insurance_rate', 'table': '保险参数', 'column': '材料运输保险费率', 'function': 'SUM',
         'label': '材料运输保险费率', 'query_alias': '材料运输保险费率', 'aliases': ['材料运输保险费率'], 'unit': 'ratio'}]
    (run / 'metric_catalog.json').write_text(json.dumps(cat, ensure_ascii=False, indent=2))
    manifest = json.loads((ROOT / 'benchmarks/real_document_20261011/odb-manifest.json').read_text())
    source = next(d for d in manifest['documents'] if d['split'] == 'dev' and '61520788' in d['image_name'])
    raw = None
    if a.native_pdf:
        raw = a.native_pdf.read_bytes()
    elif (run / 'native-textbook.pdf').exists():
        raw = (run / 'native-textbook.pdf').read_bytes()
    elif a.fetch_native_pdf:
        with urllib.request.urlopen(source['url'], timeout=60) as response:
            raw = response.read(20 * 1024 * 1024 + 1)
    if raw is not None:
        if len(raw) > 20 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != source['pdf_sha256']:
            raise ValueError('native textbook PDF does not match frozen original')
        (run / 'native-textbook.pdf').write_bytes(raw)
    else:
        print('Native table/formula acceptance needs --native-pdf PATH or --fetch-native-pdf.')
    print('Runtime prepared:', run)
    print('Formula database numbers are artificial integration data: 500 CNY and 0.02.')


if __name__ == '__main__':
    main()
