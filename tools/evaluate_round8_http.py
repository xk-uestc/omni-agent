"""Verify the live frontend HTTP contracts; never substitutes for browser QA."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request
import uuid
import socket

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8030')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.url != 'http://127.0.0.1:8030' or args.output.resolve().parent != (ROOT / 'docs').resolve() or args.output.exists():
        parser.error('Local project endpoint and new docs output required')
    def hashes():
        return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((ROOT / 'ict-track8/backend').rglob('*.py'))}
    before = hashes()
    # Only this fixed loopback target bypasses system/environment proxies.
    # The real upstream provider keeps its existing authenticated transport.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path, body=None):
        req = urllib.request.Request(args.url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json'} if body is not None else {})
        try:
            with opener.open(req, timeout=300) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            return 0, json.dumps({'transport_error': type(exc).__name__}).encode()
    checks = {}
    status, raw = request('/health'); health = json.loads(raw)
    checks['health_real_model'] = (status == 200 and health['ok']
        and health['generation'] == {'mode': 'responses', 'model': 'gpt-6-luna'})
    status, raw = request('/'); html = raw.decode()
    checks['frontend_available'] = status == 200 and 'app.js?v=20261004-' in html
    status, raw = request('/app.js'); script = raw.decode()
    checks['frontend_document_endpoint'] = (status == 200 and 'normalizeOmniResponse' in script
        and '/api/v1/omni/query' in script and 'sourcePageViewer' in script)
    status, raw = request('/api/v1/knowledge/documents'); documents = json.loads(raw)['documents']
    doc = next(d for d in documents if d['document_id'] == 'forecast-report')
    base = '/api/v1/knowledge/documents/' + doc['document_id']
    status, raw = request(base + '/original')
    checks['original_sha256'] = status == 200 and hashlib.sha256(raw).hexdigest() == doc['sha256']
    status, raw = request(base + '/visual-evidence', {'page_no': 1, 'expected_source_sha256': doc['sha256']})
    manifest = json.loads(raw)
    checks['manifest_pins'] = status == 200 and manifest['source_sha256'] == doc['sha256'] and manifest['page_no'] == 1
    status, raw = request(manifest['png_uri'])
    checks['png_sha256'] = status == 200 and hashlib.sha256(raw).hexdigest() == manifest['render_sha256']
    status, _ = request(base + '/visual-evidence', {'page_no': 1, 'expected_source_sha256': '0' * 64})
    checks['stale_source_rejected'] = status == 409
    responses = []
    for question, route in [('销售额的统计口径是什么', 'document'), ('查询sales_orders记录总数。', 'sql')]:
        status, raw = request('/api/v1/omni/query', {'question': question,
            'session_id': 'round8-http-' + uuid.uuid4().hex, 'reset_context': True})
        data = json.loads(raw); responses.append(data)
        result = data.get('result', {})
        checks[route + '_real_request'] = (status == 200 and data.get('route') == route
            and result.get('status') == 'ok' and data.get('planner_source') == 'model_validated')
        if route == 'document':
            checks['document_citations'] = bool(result.get('citations'))
        else:
            complete = result.get('provenance', {}).get('complete_result', {})
            checks['sql_readonly_executed'] = (bool(result.get('sql')) and result.get('result_state') == 'rows'
                and complete.get('status') == 'complete' and complete.get('cursor_eof_verified') is True
                and bool(result.get('provenance', {}).get('query_hash')))
    after = hashes()
    checks['backend_source_stable'] = before == after
    report = {'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': 'live_HTTP_frontend_API_contract_checks_not_browser_visual_or_accuracy_benchmark',
        'url': args.url, 'health': health, 'checks': checks, 'ok': all(checks.values()),
        'responses': responses, 'page_manifest': manifest,
        'implementation_file_sha256': before, 'implementation_file_sha256_end': after}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'ok': report['ok'], 'checks': checks}, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
