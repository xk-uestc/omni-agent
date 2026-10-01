"""One real HTTP main-QA visual route probe; no duplicate or automatic retry."""
from datetime import datetime, timezone
import json
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]


def main():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    question = 'What is West 2025 Units?'
    manifest = json.loads((ROOT / 'samples/manifest.json').read_text(encoding='utf-8'))
    sample = next(item for item in manifest['files'] if item['document_id'] == 'visual-grid-acceptance-20261001')
    report = {'scope': 'one_synthetic_main_http_visual_route_not_official_accuracy',
              'question': question, 'expected_literal': '6635',
              'explicit_document_selector_sent': False, 'http_requests_attempted': 1}
    try:
        response = requests.post('http://127.0.0.1:8030/api/v1/knowledge/query',
                                 json={'question': question, 'top_k': 4}, timeout=(5, 240))
        report['http_status'] = response.status_code
        if response.status_code == 200:
            result = response.json()
            report['result'] = result
            fact = result.get('fact', {})
            report['passed'] = (result.get('status') == 'ok' and result.get('answer_mode') == 'visual_grid_routed'
                                and fact.get('raw_value') == '6635'
                                and fact.get('source_sha256') == sample['sha256']
                                and result.get('visible_cell_verification', {}).get('status') == 'corroborated'
                                and result.get('question') == question
                                and bool(result.get('citations')))
        else:
            report['passed'] = False
    except (requests.RequestException, ValueError) as exc:
        report['passed'] = False
        report['error_type'] = type(exc).__name__
    path = ROOT / 'docs' / f'VISUAL_ROUTE_HTTP_{stamp}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(path), 'passed': report['passed'], 'http_status': report.get('http_status')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
