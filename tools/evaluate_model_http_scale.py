"""Opt-in real-model HTTP row-scale measurement, not an accuracy benchmark.

Isolated databases and servers; local excluded credentials only. --preview never
loads credentials or starts a process. Document page/OCR scale is not measured.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
SCALES = (1000, 10000, 100000)
CONCURRENCY = (1, 2, 4)
QUESTION = '2025年华东地区销售额'
ORACLE_SQL = "SELECT SUM(sales_amount) FROM sales_orders WHERE region='华东' AND order_date>='2025-01-01' AND order_date<'2026-01-01'"
AUDIT_FIELDS = ('provider', 'model', 'reasoning', 'reasoning_effort', 'operation',
                'http_status', 'status', 'model_verified', 'response_model',
                'input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens',
                'reasoning_tokens', 'call_index', 'latency_ms')


def experiment_plan(groups=1):
    return {'row_scales': list(SCALES), 'warm_concurrency': list(CONCURRENCY),
            'groups_per_level': groups, 'requests_per_scale': 1 + groups * sum(CONCURRENCY),
            'total_requests': len(SCALES) * (1 + groups * sum(CONCURRENCY)),
            'model': 'gpt-6-luna', 'reasoning': 'medium',
            'endpoint': '/api/v1/omni/query', 'question': QUESTION,
            'scope': 'synthetic_structured_rows_actual_http_with_real_intent_and_sql_planning',
            'document_page_scale_measured': False,
            'sql_answer_generation': 'local_result_and_explanation_serialization_no_separate_llm_generation'}


def report_path(value):
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    candidate = candidate.resolve()
    if candidate.parent != (ROOT / 'docs').resolve() or candidate.suffix.lower() != '.json':
        raise ValueError('报告必须是项目 docs 目录内的 JSON')
    if candidate.exists():
        raise ValueError('禁止覆盖已有报告')
    return candidate


def make_database(path, count):
    """Fixed data generator; oracle executes independent literal SQL, no engine."""
    with sqlite3.connect(path) as connection:
        connection.executescript('CREATE TABLE sales_orders(order_id INTEGER PRIMARY KEY,region TEXT NOT NULL,order_date DATE NOT NULL,sales_amount INTEGER NOT NULL);')
        connection.executemany('INSERT INTO sales_orders VALUES(?,?,?,?)',
            ((i, ('华东', '华南', '华北')[i % 3],
              f'{2024 + i % 2}-03-{1 + i % 28:02d}', 100 + i % 997)
             for i in range(count)))
        connection.execute('CREATE INDEX sales_region_date ON sales_orders(region,order_date)')
        expected = connection.execute(ORACLE_SQL).fetchone()[0]
    return expected


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower, upper = math.floor(position), math.ceil(position)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def metrics(records, wall_ms):
    passed = sum(row['pass'] for row in records)
    audits = [audit for row in records for audit in row.get('api_audits', [])]
    usage = {}
    for field in ('input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens', 'reasoning_tokens'):
        known = [a[field] for a in audits if type(a.get(field)) is int and a[field] >= 0]
        usage[field] = {'reported_sum': sum(known), 'unknown_visible_calls': len(audits) - len(known),
                        'complete_transport_history': False}
    return {'wall_ms': round(wall_ms, 3), 'requests': len(records), 'passed': passed,
            'correct_rate': passed / len(records) if records else None,
            'http_errors': sum(row.get('http_status') != 200 for row in records),
            'actual_qps': round(len(records) * 1000 / wall_ms, 6) if wall_ms > 0 else None,
            'correct_qps': round(passed * 1000 / wall_ms, 6) if wall_ms > 0 else None,
            'p50_ms': percentile([row['wall_ms'] for row in records], .5),
            'p95_ms': percentile([row['wall_ms'] for row in records], .95),
            'visible_api_calls': len(audits), 'visible_usage_lower_bound': usage,
            'percentile_method': 'linear_interpolation_small_sample_not_production_tail_estimate'}


def safe_audits(value):
    """Only allowlisted transport metadata; repeated response copies deduplicated."""
    records, seen = [], set()
    def walk(node):
        if isinstance(node, dict):
            if node.get('provider') == 'responses' and 'operation' in node:
                safe = {key: node[key] for key in AUDIT_FIELDS if key in node}
                identity = json.dumps(safe, sort_keys=True)
                if identity not in seen:
                    seen.add(identity)
                    records.append(safe)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    return records


def evaluate_response(data, expected):
    result = data.get('result', {})
    checks = {'status': data.get('status') == 'ok' and result.get('status') == 'ok',
              'route': data.get('route') == 'sql',
              'real_intent': data.get('planner_source') == 'model_validated',
              'real_sql': result.get('plan', {}).get('planner_source') == 'model_validated',
              'oracle_value': result.get('rows') == [{'销售额': expected}]}
    audits = safe_audits(data)
    checks['visible_api_audits'] = len(audits) >= 2
    checks['allowed_completed_model'] = bool(audits) and all(
        row.get('model') == 'gpt-6-luna' and row.get('reasoning', row.get('reasoning_effort')) == 'medium'
        and row.get('model_verified') is True and row.get('status') == 'completed'
        and type(row.get('http_status')) is int and 200 <= row['http_status'] < 300
        for row in audits)
    return checks, audits


def request_one(base, expected, timeout, gate=None, submitted=None):
    if gate:
        gate.wait()
    started = time.perf_counter()
    record = {'http_status': None, 'checks': {}, 'pass': False, 'api_audits': [],
              'client_dispatch_delay_ms': round((started - submitted) * 1000, 3) if submitted else None}
    try:
        # Local traffic must never pass through the upstream proxy environment.
        with requests.Session() as client:
            client.trust_env = False
            response = client.post(base + '/api/v1/omni/query', json={'question': QUESTION},
                                   timeout=(10, timeout), allow_redirects=False)
        record['http_status'] = response.status_code
        if response.status_code == 200:
            data = response.json()
            record['checks'], record['api_audits'] = evaluate_response(data, expected)
            record['pass'] = all(record['checks'].values())
            record['status'], record['route'] = data.get('status'), data.get('route')
            result = data.get('result', {})
            record['actual_rows'] = result.get('rows')
            record['stage_observations'] = [
                {key: step[key] for key in ('stage', 'tool', 'status', 'latency_ms') if key in step}
                for step in data.get('trace', []) if isinstance(step, dict)]
        else:
            record['error_type'] = 'http_error'
    except (requests.RequestException, ValueError, TypeError) as exc:
        # Exception strings can include URLs/headers; retain the class only.
        record['error_type'] = type(exc).__name__
    record['wall_ms'] = round((time.perf_counter() - started) * 1000, 3)
    return record


def auth_failed(records):
    return any(a.get('http_status') in (401, 403) for r in records for a in r['api_audits'])


def group_requests(base, expected, workers, timeout):
    gate = threading.Event()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        submitted = time.perf_counter()
        futures = [pool.submit(request_one, base, expected, timeout, gate, submitted) for _ in range(workers)]
        started = time.perf_counter()
        gate.set()
        records = [future.result() for future in futures]
        wall_ms = (time.perf_counter() - started) * 1000
    return records, wall_ms


def resident_bytes(pid):
    try:
        import psutil
        return psutil.Process(pid).memory_info().rss, 'psutil_process_rss'
    except ImportError:
        if os.name != 'nt':
            return None, 'unavailable'
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('PeakWorkingSetSize', 'WorkingSetSize',
                'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000 | 0x0010, False, pid)
        if not handle:
            return None, 'windows_working_set_unavailable'
        try:
            info = Counters()
            info.cb = ctypes.sizeof(info)
            fn = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
            fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            if fn(handle, ctypes.byref(info), info.cb):
                return int(info.WorkingSetSize), 'windows_process_working_set'
            return None, 'windows_working_set_unavailable'
        finally:
            kernel.CloseHandle(handle)
    except Exception:
        return None, 'unavailable'


class MemorySampler:
    def __init__(self, pid):
        self.pid, self.samples, self.stop = pid, [], threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
    def run(self):
        while not self.stop.is_set():
            value, self.method = resident_bytes(self.pid)
            if value is not None:
                self.samples.append(value)
            self.stop.wait(.1)
    def __enter__(self):
        self.method = 'unavailable'
        self.thread.start()
        return self
    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=2)
    def report(self):
        return {'method': self.method, 'peak_sampled_bytes': max(self.samples) if self.samples else None,
                'samples': len(self.samples), 'interval_ms': 100,
                'scope': 'server_process_only_sampled_peak_not_exact_peak_or_children'}


def unused_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


@contextmanager
def isolated_server(work, environment, startup_timeout=90):
    port = unused_port()
    base = f'http://127.0.0.1:{port}'
    env = dict(environment)
    for key in ('ICT8_PLAN_URL', 'ICT8_PLAN_TOKEN', 'ICT8_API_TOKEN', 'ICT8_SCHEMA_ALIASES',
                'ICT8_MANUAL_RETRIEVER_URL', 'ICT8_MANUAL_RETRIEVER_TOKEN', 'ICT8_DEMO_MODE'):
        env.pop(key, None)
    env.update({'ICT8_ENV': 'development', 'ICT8_DB_PATH': str(work / 'sales.sqlite'),
                'ICT8_SESSION_DB': str(work / 'sessions.sqlite'), 'ICT8_KNOWLEDGE_ROOT': str(work / 'knowledge'),
                'ICT8_PLAN_RETRIES': '0', 'ICT8_OPENAI_REASONING': 'medium'})
    # --with-model loads only the existing project-local config. No config or
    # credentials are copied to this temporary working directory or argv/logs.
    process = subprocess.Popen([sys.executable, str(ROOT / 'tools/run_server.py'),
        '--with-model', '--model', 'gpt-6-luna', '--port', str(port)], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    started = time.perf_counter()
    try:
        ready = False
        with requests.Session() as client:
            client.trust_env = False
            while time.perf_counter() - started < startup_timeout:
                if process.poll() is not None:
                    raise RuntimeError('isolated_server_exited_before_health')
                try:
                    response = client.get(base + '/health', timeout=2, allow_redirects=False)
                    health = response.json()
                    if response.status_code == 200 and health.get('generation', {}).get('model') == 'gpt-6-luna':
                        ready = True
                        break
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(.2)
        if not ready:
            raise RuntimeError('isolated_server_health_timeout')
        yield base, process, round((time.perf_counter() - started) * 1000, 3)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def model_environment():
    from model_runtime import enable_local_model
    before = dict(os.environ)
    try:
        enabled = enable_local_model('gpt-6-luna')
        if enabled.get('reasoning') != 'medium':
            raise ValueError('仅允许 medium 思考配置；不会修改模型配置')
        return dict(os.environ)
    finally:
        for key in set(os.environ) - set(before):
            del os.environ[key]
        os.environ.update(before)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--groups', type=int, default=1, choices=(1, 2, 3))
    parser.add_argument('--timeout', type=int, default=180, choices=(180, 300))
    parser.add_argument('--work-parent', type=Path, default=Path('D:/ICT8-OfficialDatasets/http-scale'))
    parser.add_argument('--output', default='docs/MODEL_HTTP_ROW_SCALE_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    args = parser.parse_args()
    try:
        output = report_path(args.output)
        parent = args.work_parent.resolve()
        if os.name == 'nt' and parent.drive.lower() != 'd:':
            raise ValueError('临时数据父目录必须位于 D 盘')
        if not parent.is_dir() and not args.preview:
            parent.mkdir(parents=True, exist_ok=True)
    except ValueError as exc:
        parser.error(str(exc))
    plan = experiment_plan(args.groups)
    if args.preview:
        print(json.dumps(plan, ensure_ascii=False))
        return 0
    environment = model_environment()
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'plan': plan,
              'environment': {'python': platform.python_version(), 'platform': platform.platform(),
                              'cpu_logical': os.cpu_count(), 'sqlite': sqlite3.sqlite_version},
              'official_speed_threshold': None,
              'limits': ['No fixed latency/QPS threshold in official specification; this is measured evidence, not an invented pass SLO.',
                         'Synthetic single-table indexed aggregate, not independent complex-query accuracy.',
                         'Cold means first query after new process health; startup separately measured; OS/provider caches not cleared.',
                         'Warm means later requests in same process; each has no conversation session.',
                         'No document page/OCR scale or document answer generation measured.',
                         'HTTP-visible audits may omit transport retry history; token totals are visible lower bounds.',
                         'Server queue and SQL execution timings may be unavailable; do not infer them by subtracting overlapping stages.'],
              'scales': [], 'status': 'running'}
    sys.path.insert(0, str(ROOT / 'ict-track8'))
    from backend.knowledge_store import KnowledgeStore
    try:
        with tempfile.TemporaryDirectory(prefix='ict8-model-http-rows-', dir=parent) as directory:
            for size in SCALES:
                work = Path(directory) / str(size)
                work.mkdir()
                expected = make_database(work / 'sales.sqlite', size)
                KnowledgeStore(work / 'knowledge')  # Prevent demo corpus seeding.
                entry = {'rows': size, 'expected_value': expected, 'oracle_sql': ORACLE_SQL,
                         'database_sha256': hashlib.sha256((work / 'sales.sqlite').read_bytes()).hexdigest(), 'levels': []}
                report['scales'].append(entry)
                with isolated_server(work, environment) as (base, process, startup_ms):
                    entry['server_startup_ms'] = startup_ms
                    with MemorySampler(process.pid) as memory:
                        cold = request_one(base, expected, args.timeout)
                        entry['cold'] = cold
                        if not auth_failed([cold]):
                            for workers in CONCURRENCY:
                                records, total_ms = [], 0
                                for _ in range(args.groups):
                                    rows, wall_ms = group_requests(base, expected, workers, args.timeout)
                                    records.extend(rows)
                                    total_ms += wall_ms
                                    if auth_failed(rows):
                                        break
                                entry['levels'].append({'concurrency': workers, 'records': records, 'metrics': metrics(records, total_ms)})
                                if auth_failed(records):
                                    break
                    entry['server_memory'] = memory.report()
                attempted = [cold] + [r for level in entry['levels'] for r in level['records']]
                print(json.dumps({'rows': size, 'attempted': len(attempted), 'passed': sum(r['pass'] for r in attempted)}, ensure_ascii=False), flush=True)
                if auth_failed(attempted):
                    report['status'] = 'stopped_upstream_auth_failure'
                    break
        if report['status'] == 'running':
            report['status'] = 'measurement_completed'
    except Exception as exc:
        report['status'] = 'measurement_failed'
        report['error_type'] = type(exc).__name__
    records = [r for scale in report['scales'] for r in
               ([scale['cold']] if 'cold' in scale else []) + [r for level in scale['levels'] for r in level['records']]]
    report['attempted'], report['passed'] = len(records), sum(r['pass'] for r in records)
    report['all_requested_measurements_completed'] = len(records) == plan['total_requests'] and report['status'] == 'measurement_completed'
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'status': report['status'], 'attempted': report['attempted'], 'passed': report['passed'], 'output': str(output)}, ensure_ascii=False))
    return 0 if report['all_requested_measurements_completed'] and report['passed'] == report['attempted'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
