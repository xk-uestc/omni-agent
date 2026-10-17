"""Scorer-only M1-A contracts; never imported by production code.

Reuse the frozen context regression's SQL input-population checker, not agent
self-evaluation. Candidate eligibility here is an evaluation expectation only,
not a Memory Adapter or a retrieval implementation.
"""
from __future__ import annotations
import math
import sqlite3
from evaluate_context_semantics_20261009 import score as score_sql


def score(gold, response, database, documents):
    kind = gold['kind']
    inner = response.get('result') or {}
    if kind == 'sql':
        passed, detail = score_sql(gold, response, database)
        provenance = inner.get('provenance', {})
        version_ok = bool(provenance.get('source_revision')) and provenance.get('source_type') == 'structured_database'
        return passed and version_ok, {**detail, 'version_evidence_pass': version_ok}
    if kind == 'abstain':
        values = inner.get('results', {})
        executed_sql = bool(inner.get('sql')) or any(isinstance(v, dict) and v.get('sql') for v in values.values())
        rows = bool(inner.get('rows'))
        refused = response.get('status') in {'clarification', 'insufficient_evidence', 'blocked', 'safety_rejected'}
        return refused and not executed_sql and not rows, {'safe_abstention': refused, 'executed_sql': executed_sql,
            'answer_rows_present': rows, 'status': response.get('status'),
            'limitation': 'Checks output abstention only; recall/write isolation cannot be measured without adapter instrumentation.'}
    if kind != 'fusion':
        raise ValueError('unknown scorer contract')
    results = inner.get('results', {})
    terminal = [v for v in results.values() if isinstance(v, dict) and v.get('value') is not None and v.get('parameters')]
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        base = db.execute(gold['baseline_sql']).fetchone()[0]
    expected = base * (1 + gold['rate'])
    value_ok = len(terminal) == 1 and isinstance(terminal[0]['value'], (int, float)) and math.isclose(terminal[0]['value'], expected, rel_tol=1e-9, abs_tol=1e-7)
    pinned = inner.get('source_validation', {})
    document_ok = pinned.get('status') == 'verified' and all(pinned.get('documents', {}).get(d) == documents[d] for d in gold['documents'])
    sql_results = [v for v in results.values() if isinstance(v, dict) and v.get('sql')]
    sql_ok = False
    if len(sql_results) == 1:
        sql_ok, _ = score_sql({'gold_sql': gold['baseline_sql'], 'metric_fields': ['sales_amount'], 'dimension_fields': []},
                              {'status': sql_results[0].get('status'), 'result': sql_results[0]}, database)
    parameter_ok = False
    if len(terminal) == 1:
        p = terminal[0]['parameters'].get('目标增长率', {})
        cells = [v for v in results.values() if isinstance(v, dict) and v.get('sha256') == documents['targets'] and v.get('matched_conditions') == {'地区': gold.get('region', '华东'), '年份': gold.get('year', 2026)}]
        parameter_ok = (isinstance(p, dict) and len(cells) == 1 and p.get('locator') == cells[0].get('locator')
                        and p.get('source_uri') == cells[0].get('source_uri') and bool(p.get('locator'))
                        and math.isclose(float(p.get('value', -99)), gold['rate'], abs_tol=1e-9))
    graph_ok = {'document_formula', 'document_cell', 'sql', 'calculate'} <= {e.get('tool') for e in inner.get('trace', []) if e.get('status') == 'complete'}
    return response.get('status') == 'ok' and value_ok and document_ok and sql_ok and parameter_ok and graph_ok, {
        'status': response.get('status'), 'value_pass': value_ok, 'documents_pass': document_ok, 'baseline_sql_pass': sql_ok,
        'parameter_provenance_pass': parameter_ok, 'graph_pass': graph_ok, 'expected_scorer_only': expected,
        'actual_terminal_values': [v['value'] for v in terminal]}
