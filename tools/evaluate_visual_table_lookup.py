"""One real pinned native-grid table lookup, not a public benchmark."""
import hashlib
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))


def main():
    import fitz
    from model_runtime import enable_local_model, local_model_headers
    from backend.responses_client import StructuredResponses, GenerationError
    from backend.grounded_generation import GroundedGenerator
    from backend.knowledge_store import KnowledgeStore
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = Path('D:/ICT8-OfficialDatasets/diag/visual-table-lookup') / stamp
    directory.mkdir(parents=True, exist_ok=False)
    expected = 1000 + secrets.randbelow(8000)
    rows = [['Region', '2025 Units', '2026 Plan'], ['West', str(expected), str(expected + 100)], ['East', '9200', '9500']]
    with fitz.open() as pdf:
        page = pdf.new_page(width=430, height=280)
        for x in (50, 150, 250, 350):
            page.draw_line((x, 50), (x, 155))
        for y in (50, 85, 120, 155):
            page.draw_line((50, y), (350, y))
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                page.insert_text((55 + c * 100, 72 + r * 35), value, fontsize=9)
        raw = pdf.tobytes()
    with (directory / 'source.pdf').open('xb') as stream:
        stream.write(raw)
    configured = enable_local_model('gpt-6-luna')
    if configured['reasoning'] != 'medium':
        raise ValueError('Only authorized model reasoning allowed')
    client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
                                 model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
    store = KnowledgeStore(directory / 'knowledge', generator=GroundedGenerator(client))
    record = store.ingest(raw, document_id='grid', title='Random grid', modality='pdf', filename='source.pdf')
    report = {'scope': 'one_synthetic_native_grid_model_selection_with_server_value_not_official_accuracy',
              'model': 'gpt-6-luna', 'reasoning': 'medium', 'question': 'What is West 2025 Units?',
              'expected': {'row_header': 'West', 'column_header_path': ['2025 Units'], 'raw_value': str(expected)},
              'source_sha256': hashlib.sha256(raw).hexdigest(), 'data_directory': str(directory),
              'requests_planned': 1, 'limits': ['Native text and ruling-grid provenance is not independent visible OCR verification.',
                                               'One synthetic lookup does not establish public PDF/chart QA accuracy.']}
    try:
        result = store.visual_table_answer('grid', question=report['question'], page_no=1,
                                          expected_source_sha256=record['sha256'])
        report['result'] = result
        fact = result.get('fact', {})
        key = fact.get('fact_key', {})
        report['pass'] = (result['status'] == 'ok' and fact.get('raw_value') == str(expected)
                          and key.get('row_header') == 'West' and key.get('column_header_path') == ['2025 Units']
                          and len(client.audit_history) == 1 and client.audit.get('status') == 'completed'
                          and client.audit.get('model_verified') is True)
    except (GenerationError, ValueError) as exc:
        report.update(error_type=type(exc).__name__, **{'pass': False})
    report['api_audits'] = client.audit_history
    output = ROOT / 'docs' / f'VISUAL_TABLE_LOOKUP_{stamp}.json'
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'report': str(output), 'pass': report['pass'], 'calls': len(client.audit_history)}))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
