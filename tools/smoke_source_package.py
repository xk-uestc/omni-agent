"""Isolated extracted ZIP smoke acceptance, using preinstalled Python dependencies."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path, PurePosixPath
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def isolated_environment(environment):
    # Preserve platform/runtime lookup only. No parent provider credentials,
    # endpoint, proxies, PYTHONPATH or model switches enter the child.
    allowed = {'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'COMSPEC',
               'PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA', 'USERPROFILE',
               'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'USERNAME'}
    result = {key: value for key, value in environment.items() if key.upper() in allowed}
    result.update(PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                  ICT8_MODEL_ENABLED='false')
    return result


def safe_extract(package, target):
    target = Path(target).resolve()
    with zipfile.ZipFile(package) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError('duplicate_zip_member')
        for member in archive.infolist():
            name = member.filename
            path = PurePosixPath(name)
            if (not name or '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts
                    or stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK
                    or any(part in {'runtime', '.git', '.venv'} for part in path.parts)
                    or path.name in {'model_config.json', 'auth.json', '.env'}):
                raise ValueError('unsafe_or_private_zip_member')
            if not (target / name).resolve().is_relative_to(target):
                raise ValueError('zip_path_escape')
        archive.extractall(target)


def verify(package):
    location = ROOT / 'ict-track8/scripts/verify_package.py'
    spec = importlib.util.spec_from_file_location('source_zip_verify', location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.verify_package(Path(package))
    if not result['ok']:
        raise ValueError('package_verification_failed')
    return result


def request(base, path, body=None, *, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers={'Content-Type': 'application/json'} if data else {})
    # Localhost must never traverse inherited network proxies.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=90) as response:
        value = response.read()
    return value if raw else json.loads(value)


def smoke(package, output, *, temporary_root=Path('D:/ICT8-Backups'), timeout=180):
    package, output, temporary_root = Path(package).resolve(), Path(output).resolve(), Path(temporary_root).resolve()
    if output.exists():
        raise FileExistsError('report_must_not_overwrite_history')
    if temporary_root.drive.upper() != 'D:':
        raise ValueError('temporary_extraction_must_be_on_D_drive')
    temporary_root.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix='package-smoke-', dir=temporary_root))
    report = {'package': str(package), 'package_sha256': hashlib.sha256(package.read_bytes()).hexdigest(),
              'extracted_workspace': str(workspace), 'python': sys.executable,
              'environment_scope': 'preinstalled_python_dependencies_not_clean_OS',
              'model_enabled': False, 'api_calls': 0, 'checks': {}, 'status': 'running'}
    server = None
    try:
        report['verification'] = verify(package)
        safe_extract(package, workspace)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        report['base_url'] = base
        with (workspace / 'smoke-server.stdout.log').open('wb') as stdout, (workspace / 'smoke-server.stderr.log').open('wb') as stderr:
            server = subprocess.Popen([sys.executable, '-B', str(workspace / 'tools/run_server.py'), '--port', str(port)],
                cwd=workspace, env=isolated_environment(os.environ), stdout=stdout, stderr=stderr)
            deadline = time.monotonic() + timeout
            while True:
                if server.poll() is not None:
                    raise RuntimeError('isolated_server_exited_before_health')
                try:
                    health = request(base, '/health')
                    break
                except Exception:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('isolated_server_health_timeout')
                    time.sleep(.5)
            checks = report['checks']
            checks['health'] = health.get('ok') is True
            checks['no_model'] = health.get('generation', {}).get('model') is None and health.get('generation', {}).get('mode') == 'attributed_extracts'
            checks['dense'] = health.get('retrieval', {}).get('mode') == 'bm25_dense_rrf'
            checks['frontend'] = b'<html' in request(base, '/', raw=True).lower()
            expected = json.loads((workspace / 'samples/manifest.json').read_text(encoding='utf-8'))['files']
            observed = request(base, '/api/v1/knowledge/documents')['documents']
            checks['exact_sample_ids'] = {item['document_id'] for item in expected} == {item['document_id'] for item in observed}
            checks['exact_sample_metadata'] = all(any(doc['document_id'] == item['document_id'] and doc.get('sha256') == item['sha256']
                and doc.get('modality') == item['modality'] for doc in observed) for item in expected)
            checks['original_hashes'] = all(hashlib.sha256(request(base, f"/api/v1/knowledge/documents/{item['document_id']}/original", raw=True)).hexdigest() == item['sha256'] for item in expected)
            sql = request(base, '/api/v1/nl2sql/query', {'question': '2025年华东销售额'})
            checks['real_sql_29584'] = sql.get('status') == 'ok' and sql.get('rows') == [{'销售额': 29584.0}]
            def optional_check(name, action):
                try:
                    checks[name] = bool(action())
                except Exception as exc:
                    checks[name] = False
                    report.setdefault('check_errors', {})[name] = {'error_type': type(exc).__name__, 'code': str(exc)[:120]}
            def rag_check():
                rag = request(base, '/api/v1/knowledge/query', {'question': '顾客签收商品后几日内可以申请无理由退货？', 'document_id': 'return-policy'})
                report.setdefault('observed_results', {})['docx_rag'] = {'status': rag.get('status'), 'answer': rag.get('answer'), 'citations': len(rag.get('citations', []))}
                return rag.get('status') == 'ok' and bool(rag.get('citations')) and '7' in rag.get('answer', '')
            optional_check('ordinary_rag', rag_check)
            def pdf_rag_check():
                rag = request(base, '/api/v1/knowledge/query', {'question': '标准硬件产品保修期是多少个月？'})
                report.setdefault('observed_results', {})['pdf_rag'] = {'status': rag.get('status'), 'answer': rag.get('answer'), 'citations': len(rag.get('citations', []))}
                return rag.get('status') == 'ok' and bool(rag.get('citations')) and '12' in rag.get('answer', '')
            optional_check('ordinary_pdf_rag', pdf_rag_check)
            tasks = [{'id': 'formula', 'tool': 'document_formula', 'args': {'document_id': 'metric-definitions', 'label': '客单价'}},
                     {'id': 'sales', 'tool': 'sql', 'args': {'question': '2025年华东销售额和订单数'}},
                     {'id': 'calculate', 'tool': 'calculate', 'args': {'formula': {'ref': 'formula', 'path': []}, 'parameters': {
                         '销售额': {'ref': 'sales', 'path': ['rows', 0, '销售额']}, '订单数': {'ref': 'sales', 'path': ['rows', 0, '订单数']}}}}]
            def fusion_check():
                fusion = request(base, '/api/v1/fusion/execute', {'tasks': tasks})
                report.setdefault('observed_results', {})['fusion'] = fusion
                count = request(base, '/api/v1/nl2sql/query', {'question': '2025年华东订单数'})
                expected_count = count.get('rows', [{}])[0].get('订单数')
                return fusion.get('status') == 'ok' and expected_count and fusion.get('results', {}).get('calculate', {}).get('value') == 29584.0 / expected_count
            optional_check('actual_fusion', fusion_check)
            pdf = next(item for item in expected if item['modality'] == 'pdf')
            detail = request(base, f"/api/v1/knowledge/documents/{pdf['document_id']}")
            checks['pdf_page_provenance'] = any(chunk.get('page_no') or chunk.get('metadata', {}).get('page_no')
                                                   or str(chunk.get('source_locator', '')).startswith('page:') for chunk in detail.get('chunks', []))
            report['health'] = health
            report['sample_count'] = len(expected)
            report['status'] = 'passed' if all(checks.values()) else 'failed_checks'
    except Exception as exc:
        report.update(status='failed', error_type=type(exc).__name__, error_code=str(exc)[:120])
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=15)
        report['owned_server_stopped'] = server is None or server.poll() is not None
        stderr_path = workspace / 'smoke-server.stderr.log'
        if stderr_path.exists():
            # Keep stack locations and exception type only; never copy raw
            # exception messages, source lines or arbitrary log payloads.
            stderr_text = stderr_path.read_text(encoding='utf-8', errors='replace')
            report['server_exception_types'] = re.findall(r'^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):', stderr_text, re.M)
            report['server_exception_stack'] = [{'file': match[0], 'line': int(match[1]), 'function': match[2]}
                for match in re.findall(r'^\s*File "([^"\r\n]+)", line (\d+), in ([A-Za-z0-9_<>.]+)', stderr_text, re.M)][-100:]
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('package', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--temporary-root', type=Path, default=Path('D:/ICT8-Backups'))
    parser.add_argument('--timeout', type=int, default=180)
    args = parser.parse_args()
    report = smoke(args.package, args.output, temporary_root=args.temporary_root, timeout=args.timeout)
    print(json.dumps({'status': report['status'], 'output': str(args.output), 'checks': report['checks']}, ensure_ascii=False))
    raise SystemExit(0 if report['status'] == 'passed' else 1)
