"""Cross-schema program evaluation; public sample, synthetic questions, no gold input."""
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import time
from fetch_ohr_bench import ROOT, sha256

sys.path.insert(0, str(ROOT/'ict-track8'))
FOLDER = ROOT/'benchmarks/northwind_generalization'


def load_frozen():
    manifest_raw = (FOLDER/'MANIFEST.json').read_bytes(); manifest = json.loads(manifest_raw)
    asset_raw = (FOLDER/'ASSET_MANIFEST.json').read_bytes()
    if sha256(asset_raw) != manifest['asset_manifest_sha256']: raise ValueError('asset_manifest_changed')
    raw = (FOLDER/manifest['questions_path']).read_bytes()
    if sha256(raw) != manifest['questions_sha256']: raise ValueError('questions_changed')
    cases = json.loads(raw)
    if [c['id'] for c in cases] != [c['id'] for c in manifest['cases']]: raise ValueError('case_ids_changed')
    for case, frozen in zip(cases, manifest['cases']):
        if sha256(json.dumps(case, ensure_ascii=False, sort_keys=True).encode()) != frozen['row_sha256']:
            raise ValueError('frozen_case_changed')
    path = Path(manifest['data_root_default'])/manifest['database_path']
    if sha256(path.read_bytes()) != manifest['database_sha256']: raise ValueError('database_changed')
    return manifest, manifest_raw, cases, path


def implementation_snapshot():
    files = [*sorted((ROOT/'ict-track8/backend').rglob('*.py')), Path(__file__), ROOT/'tools/model_runtime.py']
    return {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()) for p in files}


def numeric(value):
    return type(value) in (int, float) and (type(value) is int or math.isfinite(value))


def cells_equal(a, b):
    if numeric(a) and numeric(b):
        return abs(Decimal(str(a))-Decimal(str(b))) <= Decimal('0.0000001')
    return type(a) is type(b) and a == b


def row_equal(a, b):
    if len(a) != len(b): return False
    # Output aliases/column ordering are not prescribed by these questions.
    # Preserve types, cardinality and repeated cells, never flatten all rows.
    available = list(b)
    for value in a:
        index = next((i for i, other in enumerate(available) if cells_equal(value, other)), None)
        if index is None: return False
        available.pop(index)
    return not available


def assess(case, result, expected):
    rows = result.get('rows', [])
    valid = isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
    rank_display_valid = True
    # The compiler optionally displays its dense rank. This is legitimate for
    # an explicit ranking request, although the reference SQL projects only
    # group/metric. Independently verify that display; never ignore arbitrary
    # extra output columns or trust a plan's claimed ordering as the answer.
    if (case.get('metric_order') and valid and rows
            and any('排名' in row for row in rows)):
        metric_rows = [[value for value in row if numeric(value)] for row in expected]
        expected_widths = {len(row) for row in expected}
        if (not expected or any(len(values) != 1 for values in metric_rows)
                or len(expected_widths) != 1 or result.get('plan', {}).get('analysis_mode') != 'rank'
                or any('排名' not in row or len(row) != next(iter(expected_widths))+1 for row in rows)):
            rank_display_valid = False
        else:
            levels = sorted({Decimal(str(values[0])) for values in metric_rows}, reverse=True)
            for row in rows:
                values = [v for key, v in row.items() if key != '排名' and numeric(v)]
                if (len(values) != 1 or Decimal(str(values[0])) not in levels
                        or type(row['排名']) is not int
                        or row['排名'] != levels.index(Decimal(str(values[0])))+1):
                    rank_display_valid = False
        rows = [{key: value for key, value in row.items() if key != '排名'} for row in rows]
    observed = [list(row.values()) for row in rows] if valid else []
    remaining = list(expected)
    for row in observed:
        index = next((i for i, other in enumerate(remaining) if row_equal(row, other)), None)
        if index is None: break
        remaining.pop(index)
    match = valid and len(observed) == len(expected) and not remaining
    order = True
    if case.get('metric_order'):
        metrics = [[v for v in row if numeric(v)] for row in observed]
        order = all(len(row) == 1 for row in metrics)
        if order:
            values = [row[0] for row in metrics]
            order = all(a >= b for a, b in zip(values, values[1:]))
    return {'status_ok': result.get('status') == 'ok', 'execution_result': match,
            'ranking_order': order and rank_display_valid}


def allowed(audit):
    return (audit.get('model') == 'gpt-6-luna' and audit.get('reasoning') == 'medium'
            and audit.get('model_verified') is True and audit.get('status') == 'completed'
            and type(audit.get('http_status')) is int and 200 <= audit['http_status'] < 300
            and isinstance(audit.get('response_model'), str)
            and __import__('re').fullmatch(r'gpt-6-luna(?:-\d{4}-\d{2}-\d{2})?', audit['response_model']) is not None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true', help='Verify frozen assets only; no program/model execution')
    parser.add_argument('--model', action='store_true')
    parser.add_argument('--output', default='docs/NORTHWIND_GENERALIZATION_FIRST_RUN.json')
    args = parser.parse_args()
    manifest, manifest_raw, cases, path = load_frozen()
    if args.preview:
        print(json.dumps({'verified_cases': len(cases), 'schema_tables': 13,
            'questions_sha256': manifest['questions_sha256'], 'database_sha256': manifest['database_sha256'],
            'scope': manifest['scope'], 'api_called': False, 'program_executed': False}))
        return 0
    output = (ROOT/args.output).resolve()
    if not output.is_relative_to((ROOT/'docs').resolve()) or output.suffix != '.json' or output.exists():
        parser.error('Use a new JSON report in docs; never overwrite results')
    from backend.nl2sql.engine import Nl2SqlEngine
    provider = None
    if args.model:
        from model_runtime import enable_local_model, local_model_headers
        from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
        configuration = enable_local_model()
        if configuration['model'] != 'gpt-6-luna' or configuration['reasoning'] != 'medium':
            raise ValueError('only_gpt_6_luna_medium_allowed')
        provider = ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna', reasoning_effort='medium', max_retries=0, http_headers=local_model_headers())
    engine = Nl2SqlEngine(path, model_plan_provider=provider, reference_date=date(2026,10,2),
                         metric_catalog_path=FOLDER/'absent_catalog.json')
    if (FOLDER/'absent_catalog.json').exists(): raise ValueError('unexpected_business_catalog')
    start = implementation_snapshot(); records, stopped = [], None
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as scoring:
        for case in cases:
            if stopped:
                records.append({'id': case['id'], 'category': case['category'], 'pass': False,
                                'status': 'not_attempted', 'reason': stopped})
                continue
            if provider: provider.reset_audit()
            began = time.perf_counter()
            try:
                # ONLY question goes to the engine; no reference SQL/results,
                # custom schema aliases, gold plans or answers enter provider.
                result = engine.answer(case['question']).to_dict()
            except Exception as exc:
                result = {'status': 'error', 'error_type': type(exc).__name__}
            expected = scoring.execute(case['reference_sql']).fetchall()
            audits = list(provider.audit_history) if provider else []
            dropped = provider.audit_dropped_count if provider else 0
            checks = assess(case, result, expected)
            checks['only_allowed_verified_calls'] = all(allowed(a) for a in audits)
            checks['complete_api_history'] = dropped == 0
            records.append({'id': case['id'], 'category': case['category'], 'question': case['question'],
                'reference_sql_scoring_only': case['reference_sql'], 'expected_rows': expected,
                'result': result, 'checks': checks, 'pass': all(checks.values()),
                'latency_ms': round((time.perf_counter()-began)*1000,3), 'api_audits': audits,
                'audit_dropped_count': dropped})
            print(json.dumps({'id': case['id'], 'pass': records[-1]['pass'], 'status': result['status']}), flush=True)
            if any(a.get('http_status') in (401,403) for a in audits): stopped = 'authentication_or_access_rejected'
            if implementation_snapshot() != start: stopped = 'implementation_changed_during_evaluation'
    end = implementation_snapshot()
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'scope': manifest['scope'],
        'manifest_sha256': sha256(manifest_raw), 'questions_sha256': manifest['questions_sha256'],
        'database_sha256': manifest['database_sha256'], 'database_unchanged': sha256(path.read_bytes()) == manifest['database_sha256'],
        'implementation_file_sha256': start, 'implementation_file_sha256_end': end, 'implementation_stable': start == end,
        'model': 'gpt-6-luna' if args.model else None, 'reasoning': 'medium' if args.model else None,
        'gold_sent_to_model': False, 'passed': sum(r['pass'] for r in records), 'total': len(cases),
        'attempted': sum(r.get('status') != 'not_attempted' for r in records), 'stopped': stopped,
        'cases': records, 'limitations': manifest['limitations'], 'scoring': manifest['scoring']}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'total': report['total'], 'implementation_stable': start == end}))
    return 0 if report['passed'] == len(cases) and start == end and report['database_unchanged'] else 1


if __name__ == '__main__': raise SystemExit(main())
