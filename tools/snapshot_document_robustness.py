"""Save a small reviewable receipt without embedding public PDFs or credentials."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ['ict-track8/backend/ocr.py', 'ict-track8/backend/orientation_quality.py',
         'ict-track8/backend/scanned_table_layout.py', 'ict-track8/backend/image_quality.py',
         'ict-track8/backend/chunk_cleaning.py', 'ict-track8/backend/session.py',
         'ict-track8/backend/omni_agent.py', 'ict-track8/backend/sql_history_scope.py',
         'ict-track8/backend/app.py']
REPORTS = ['document-robustness-finance-gold-20261004.json',
           'document-robustness-finance-final-gold-20261004.json',
           'document-table-cell-recovery-20261004.json', 'scanned-pdf-http-20261004.json']


def main():
    sources = {file: hashlib.sha256((ROOT / file).read_bytes()).hexdigest() for file in FILES}
    reports = []
    for file in REPORTS:
        path = ROOT / 'runtime' / file
        raw = path.read_bytes()
        parsed = json.loads(raw)
        if 'results' in parsed:
            summary = parsed['results']
        elif 'variants' in parsed:
            summary = []
            for variant in parsed['variants']:
                grids = variant['ocr'].get('metadata', {}).get('scanned_grids', {})
                summary.append({'variant': variant['variant'], 'ocr_status': variant['ocr']['status'],
                    'grids_status': grids.get('status'), 'cell_recovery': grids.get('cell_recovery'),
                    'tables': [{'rows': table['row_count'], 'columns': table['column_count'],
                        'unbound_region_count': len(table['unbound_region_indices']),
                        'cells_with_text': sum(bool(cell['text']) for row in table['cells'] for cell in row),
                        'values_verified': table['cell_values_verified']}
                        for table in grids.get('tables', [])]})
        else:
            summary = parsed
        reports.append({'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'summary': summary})
    target = ROOT / 'docs/DIALOGUE_DOCUMENT_ROBUSTNESS_20261004.json'
    target.write_text(json.dumps({'not_official_score': True, 'source_hashes_at_receipt': sources,
        'limitation': 'Historical reports bind their own run. Source hashes at receipt do not retroactively bind older inference.',
        'reports': reports}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(target)


if __name__ == '__main__':
    main()
