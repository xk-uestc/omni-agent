"""Frozen, collaborator-authored first subscription-domain audit.

No external model, credential, few-shot example, or domain-specific alias is used.
This is not an official benchmark or a strictly independent blinded evaluation.
After exposure the questions are development material, not unseen holdout data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / 'benchmarks/heldout_finance'
sys.path.insert(0, str(ROOT / 'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine


def normalized(rows, ordered=False):
    values = [tuple(round(v, 6) if type(v) in (int, float) else v for v in row) for row in rows]
    return values if ordered else sorted(values, key=repr)


def projection_matches(case, result):
    """Check source-field semantics, not cosmetic aggregate column labels.

    Output order is dimensions then one metric. Extra/missing output fields fail.
    Dimension labels must name their actual dimension; aggregate display labels
    may differ because their table/column/function are verified from the plan.
    """
    plan = result.get('plan', {})
    dimensions = case['dimensions']
    metric = case['metric']
    expected_dims = [item['column'] for item in dimensions]
    observed_dims = plan.get('dimensions', [])
    tables = plan.get('dimension_tables', {})
    columns = result.get('columns', [])
    return (
        len(columns) == len(dimensions) + 1
        and observed_dims == expected_dims
        and columns[:len(dimensions)] == expected_dims
        and all(tables.get(item['column'], plan.get('table')) == item['table'] for item in dimensions)
        and plan.get('metric_table', plan.get('table')) == metric['table']
        and plan.get('metric_column') == metric['column']
        and str(plan.get('metric_function', '')).lower() == metric['aggregation']
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/HELDOUT_FINANCE_FIRST_RUN.json')
    parser.add_argument('--replace-report', action='store_true', help='Replace a development report; the first-run report is always protected.')
    args = parser.parse_args()
    if args.output.exists() and (not args.replace_report or args.output.name == 'HELDOUT_FINANCE_FIRST_RUN.json'):
        raise FileExistsError('Refusing to replace an existing audit report; choose a new --output.')
    manifest = json.loads((INPUT / 'MANIFEST.json').read_text(encoding='utf-8'))
    for item in manifest['files']:
        raw = (INPUT / item['path']).read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Frozen audit input hash mismatch: ' + item['path'])
    cases = json.loads((INPUT / 'questions.json').read_text(encoding='utf-8'))['cases']
    records = []
    # A fresh database lives on the system temporary drive, not the project disk.
    with tempfile.TemporaryDirectory(prefix='ict8-subscription-audit-') as directory:
        workspace = Path(directory)
        db = workspace / 'subscriptions.sqlite'
        with closing(sqlite3.connect(db)) as writer:
            writer.executescript((INPUT / 'schema.sql').read_text(encoding='utf-8'))
            writer.commit()
        initial_sha = hashlib.sha256(db.read_bytes()).hexdigest()
        old_aliases = os.environ.pop('ICT8_VALUE_ALIASES', None)
        try:
            engine = Nl2SqlEngine(db, model_plan_provider=None,
                model_fallback=False, metric_catalog_path=workspace / 'no-catalog.json')
            with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True)) as gold:
                gold.execute('PRAGMA query_only=ON')
                for case in cases:
                    cursor = gold.execute(case['gold_sql'])
                    expected = cursor.fetchall()
                    tied_order = case['ordered'] and len({row[-1] for row in expected}) != len(expected)
                    validity = 'ambiguous_tie_policy' if tied_order else 'valid'
                    started = time.perf_counter()
                    try:
                        result = engine.answer(case['question']).to_dict()
                        fields = projection_matches(case, result)
                        columns = result.get('columns', [])
                        observed = [tuple(row[name] for name in columns) for row in result.get('rows', [])]
                        values = normalized(observed, case['ordered'] and not tied_order) == normalized(expected, case['ordered'] and not tied_order)
                        status_ok = result.get('status') == 'ok'
                    except (ValueError, RuntimeError, sqlite3.Error, KeyError) as exc:
                        result = {'status': 'error', 'error_type': type(exc).__name__}
                        fields = values = status_ok = False
                        observed = []
                    record = {**case, 'status_ok': status_ok, 'field_match': fields,
                        'value_match': values, 'pass': status_ok and fields and values,
                        'validity': validity,
                        'expected': expected, 'observed': observed,
                        'gold_columns': [entry[0] for entry in cursor.description],
                        'latency_ms': round((time.perf_counter() - started) * 1000, 3), 'result': result}
                    records.append(record)
                    print(json.dumps({key: record[key] for key in ('id', 'pass', 'field_match', 'value_match')}, ensure_ascii=False), flush=True)
        finally:
            if old_aliases is not None:
                os.environ['ICT8_VALUE_ALIASES'] = old_aliases
        unchanged = initial_sha == hashlib.sha256(db.read_bytes()).hexdigest()
    report = {
        'created_at': datetime.now(timezone.utc).isoformat(),
        'scope': ('collaborating_agent_authored_first_domain_audit_not_official_or_strict_blind_test'
                  if args.output.name == 'HELDOUT_FINANCE_FIRST_RUN.json' else 'post_fix_development_regression_not_blind_test'),
        'post_exposure_scope': 'development_cases_if_used_for_subsequent_tuning',
        'model': None, 'real_model_called': False, 'few_shot_examples': 0,
        'domain_specific_alias_files': 0, 'metric_catalog': None,
        'python': platform.python_version(), 'input_manifest': manifest,
        'database_unchanged_after_queries': unchanged,
        'field_match_contract': 'source table/column/aggregation and dimensions; cosmetic aggregate labels are not scored',
        'value_match_contract': 'all output fields, numeric rounding to 6 decimals; TopN order is checked',
        'passed': sum(item['pass'] for item in records),
        'valid_cases': sum(item['validity'] == 'valid' for item in records),
        'valid_passed': sum(item['pass'] and item['validity'] == 'valid' for item in records),
        'oracle_limits': ['An ordered TopN gold with tied amounts is excluded from the reliable accuracy denominator; its unordered contents are recorded diagnostically.'],
        'field_matched': sum(item['field_match'] for item in records),
        'value_matched': sum(item['value_match'] for item in records),
        'total': len(records), 'cases': records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w' if args.replace_report else 'x', encoding='utf-8') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write('\n')
    print(json.dumps({key: report[key] for key in ('passed', 'field_matched', 'value_matched', 'total', 'database_unchanged_after_queries')}, ensure_ascii=False), flush=True)
    return 0 if unchanged and report['passed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
