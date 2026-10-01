"""Print only non-secret provider availability and supported model identifiers."""
import json
import os
import requests
import sys
from datetime import datetime, timezone
from pathlib import Path
from model_runtime import enable_local_model, local_model_headers


def main():
    enable_local_model()
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'model': 'gpt-6-luna',
              'base_url': os.environ['ICT8_OPENAI_BASE_URL'], 'checks': [], 'status': 'unavailable'}
    try:
        response = requests.get(os.environ['ICT8_OPENAI_BASE_URL'] + '/models',
            headers={**local_model_headers(), 'Authorization': 'Bearer ' + os.environ['ICT8_OPENAI_API_KEY']}, timeout=(10,30), allow_redirects=False)
        result = {'models_http_status': response.status_code}
        report['checks'].append({'endpoint':'/v1/models', 'http_status':response.status_code})
        if response.ok:
            payload = response.json()
            result['requested_model_available'] = any(item.get('id') == 'gpt-6-luna' for item in payload.get('data', []))
        print(json.dumps(result,ensure_ascii=False))
        if response.status_code == 401:
            root = os.environ['ICT8_OPENAI_BASE_URL'].removesuffix('/v1')
            root_response = requests.get(root + '/models', headers={**local_model_headers(), 'Authorization':'Bearer ' + os.environ['ICT8_OPENAI_API_KEY']}, timeout=(10,30), allow_redirects=False)
            report['checks'].append({'endpoint':'/models', 'http_status':root_response.status_code})
            print(json.dumps({'root_models_http_status': root_response.status_code}))
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ict-track8'))
        from backend.responses_client import StructuredResponses, GenerationError, object_schema
        client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'], model='gpt-6-luna', http_headers=local_model_headers())
        try:
            result = client.generate('返回ok=true', {}, object_schema({'ok': {'type':'boolean'}}), max_tokens=1000)
            report['checks'].append({'endpoint':'/v1/responses', 'http_status':200, 'structured_valid':result.get('ok') is True})
            report['status'] = 'ready' if result.get('ok') is True else 'invalid_output'
            print(json.dumps({'structured_probe':result, 'audit':client.audit},ensure_ascii=False))
        except GenerationError as exc:
            report['checks'].append({'endpoint':'/v1/responses', 'http_status':exc.status, 'structured_valid':False})
            report['status'] = 'authentication_failed' if exc.status == 401 else 'unavailable'
            print(json.dumps({'structured_probe':'failed', 'http_status':exc.status, 'audit':client.audit},ensure_ascii=False))
    except (requests.RequestException, ValueError):
        print(json.dumps({'models_http_status': None, 'error': 'unavailable'}))
    destination = Path(__file__).resolve().parents[1] / 'docs/MODEL_API_PROBE.json'
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['status'] == 'ready' else 1


if __name__ == '__main__':
    raise SystemExit(main())
