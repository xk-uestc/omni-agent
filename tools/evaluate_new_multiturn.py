"""Real five-turn new-schema audit; frozen authored inputs, never blind scores."""
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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from evaluate_model import MODEL, collect_audits, summarise_audits, execution_metrics, close_number, report_output


def frozen_inputs(directory):
    directory = Path(directory)
    manifest = json.loads((directory/'MANIFEST.json').read_text(encoding='utf-8'))
    verified = {}
    for entry in manifest['files']:
        filename = entry['file']
        path = (directory/filename).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError('冻结输入路径越界')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry['sha256']:
            raise ValueError('冻结输入发生变化: '+filename)
        verified[filename] = digest
    definitions = json.loads((directory/'questions.json').read_text(encoding='utf-8'))
    if definitions['few_shot_examples'] != 0 or definitions['domain_alias_rules'] != 0:
        raise ValueError('独立审计禁止增加领域别名或few-shot')
    return definitions, verified


def initialize_audit(directory, inputs, definitions):
    from backend.knowledge_store import KnowledgeStore
    from backend.dense_retrieval import LocalBgeEmbedder
    directory = Path(directory)
    database = directory/'new_schema.sqlite'
    with sqlite3.connect(database) as connection:
        connection.executescript((inputs/'schema.sql').read_text(encoding='utf-8'))
    knowledge = KnowledgeStore(directory/'knowledge', embedder=LocalBgeEmbedder(ROOT/'models/bge-small-zh-v1.5'))
    for document in definitions['documents']:
        knowledge.ingest((inputs/document['file']).read_bytes(), document_id=document['id'],
                         title=document['title'], modality='md', filename=document['file'])
    return database, knowledge


def expected_rows(database, case):
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True) as connection:
        return connection.execute(case['gold_sql']).fetchall()


def physical_value(sql_result, metric):
    """Find the actual verified physical aggregate, never a per-question label map."""
    plan = sql_result.get('plan', {})
    metrics = plan.get('metrics', [])
    if not metrics:
        metrics = [{'table':plan.get('metric_table') or plan.get('table'),
                    'column':plan.get('metric_column'), 'function':plan.get('metric_function'),
                    'label':plan.get('metric_label')}]
    matches = [m for m in metrics if
               all(m.get(key) == metric[key] for key in ('table', 'column', 'function'))]
    if len(matches) != 1 or len(sql_result.get('rows', [])) != 1:
        return None
    return sql_result['rows'][0].get(matches[0]['label'])


def has_edge(data, origin, destination):
    tools = {event['task_id']:event['tool'] for event in data.get('trace', []) if event.get('status')=='complete'}
    return any(tools.get(edge['from'])==origin and tools.get(edge['to'])==destination for edge in data.get('edges', []))


def evaluate_result(case, result, database):
    data = result.get('result', {})
    route = 'sql' if case['kind']=='sql' else 'fusion'
    checks = {'model_router':result.get('planner_source')=='model_validated',
              'status':result.get('status')=='ok', 'route':result.get('route')==route,
              'history':result.get('context_turns')==case['context_turns']}
    sql_results = [data] if result.get('route')=='sql' else [v for v in data.get('results', {}).values()
                   if isinstance(v, dict) and 'plan' in v and 'provenance' in v]
    checks['real_sql_planner'] = bool(sql_results) and all(v.get('plan', {}).get('planner_source')=='model_validated' for v in sql_results)
    gold = expected_rows(database, case)
    if case['kind']=='sql':
        checks['physical_metric_value'] = close_number(physical_value(data, case['metric']), gold[0][0])
        forbidden = set(case.get('forbidden_filter_columns', []))
        filters = [*data.get('plan', {}).get('filters', []), *(f for m in data.get('plan', {}).get('metrics', []) for f in m.get('filters', []))]
        checks['no_previous_topic_filter'] = not any(f.get('column') in forbidden for f in filters)
    elif case['kind']=='sql_to_document':
        top = gold[0]
        checks['actual_champion'] = any(len(v.get('rows', []))==1 and
            v['rows'][0].get('Department')==top[0] and any(close_number(value, top[1]) for value in v['rows'][0].values()) for v in sql_results)
        hits = [hit for v in data.get('results', {}).values() if isinstance(v, dict) for hit in v.get('hits', [])]
        checks['source_and_champion'] = any(hit.get('metadata', {}).get('document_id') in case['sources'] and
            top[0] in hit.get('snippet', '') and all(part in hit.get('snippet', '') for part in case['contains']) for hit in hits)
        checks['sql_to_search_dependency'] = has_edge(data, 'sql', 'search')
    elif case['kind']=='formula':
        calculated = [v for v in data.get('results', {}).values() if isinstance(v, dict) and 'formula_source' in v]
        final = calculated[-1] if calculated else {}
        checks['calculated_value'] = close_number(final.get('value'), gold[0][0])
        checks['formula_source'] = final.get('formula_source')==f"/api/v1/knowledge/documents/{case['expected_formula_document']}/original"
        parameters = final.get('parameters', {})
        checks['parameter_provenance'] = set(parameters)==set(case['expected_parameters']) and all(
            isinstance(value, dict) and str(value.get('source_uri', '')).startswith('sql://') and value.get('locator') for value in parameters.values())
        with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True) as connection:
            expected_sum, expected_count = connection.execute("SELECT SUM(RefundedAmount),COUNT(RequestId) FROM WarrantyRequests WHERE RequestDate>='2025-01-01' AND RequestDate<'2026-01-01'").fetchone()
        checks['physical_sql_inputs'] = any(close_number(physical_value(v, {'table':'WarrantyRequests','column':'RefundedAmount','function':'SUM'}), expected_sum) for v in sql_results) and any(
            close_number(physical_value(v, {'table':'WarrantyRequests','column':'RequestId','function':'COUNT'}), expected_count) for v in sql_results)
        checks['actual_dependencies'] = has_edge(data, 'sql', 'calculate') and has_edge(data, 'document_formula', 'calculate')
        checks['no_previous_topic_filter'] = not any(f.get('column')=='Department' for v in sql_results for f in [
            *v.get('plan', {}).get('filters', []), *(f for m in v.get('plan', {}).get('metrics', []) for f in m.get('filters', []))])
    return checks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--output', default='docs/NEW_MULTITURN_FIRST_RUN_20261001.json')
    args = parser.parse_args()
    inputs = ROOT/'benchmarks/new_multiturn'
    try:
        definitions, digests = frozen_inputs(inputs)
        if not args.preview:
            output = report_output(args.output)
    except ValueError as exc:
        parser.error(str(exc))
    cases = definitions['cases']
    if args.preview:
        print(json.dumps({'mode':'preview_no_credentials_no_api', 'model':MODEL, 'reasoning':'medium',
            'frozen_inputs':digests, **definitions}, ensure_ascii=False, indent=2))
        return 0
    from model_runtime import enable_local_model, local_model_headers
    from backend.responses_client import StructuredResponses, GenerationError, object_schema
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
    from backend.omni_agent import OmniAgent
    from backend.session import ConversationStore
    started = time.perf_counter()
    enable_local_model(MODEL)
    client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
        model=MODEL, reasoning='medium', http_headers=local_model_headers())
    report = {'created_at':datetime.now(timezone.utc).isoformat(), 'scope':definitions['scope'],
              'model':MODEL, 'reasoning':'medium', 'python':platform.python_version(),
              'few_shot_examples':0, 'domain_alias_rules':0, 'frozen_inputs':digests,
              'status':'preflight_failed', 'planned_cases':len(cases), 'passed':0, 'total':0, 'cases':[]}
    report['implementation_file_sha256'] = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
        'tools/evaluate_new_multiturn.py', 'ict-track8/backend/omni_agent.py',
        'ict-track8/backend/nl2sql/engine.py', 'ict-track8/backend/nl2sql/schema.py',
        'ict-track8/backend/nl2sql/planner.py', 'ict-track8/backend/nl2sql/model_contract.py',
        'ict-track8/backend/nl2sql/responses_provider.py', 'ict-track8/backend/dependency_agent.py')}
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    preflight_started = time.perf_counter()
    try:
        if client.generate('只返回ok=true。', {}, object_schema({'ok':{'type':'boolean'}}), name='model_preflight', max_tokens=1000).get('ok') is not True:
            raise GenerationError('预检结构不符')
    except GenerationError:
        report.update(preflight=client.audit_history, api_summary=summarise_audits(client.audit_history),
            not_run=[row['id'] for row in cases], status='preflight_failed')
        save()
        return 1
    preflight_wall_ms = (time.perf_counter()-preflight_started)*1000
    report['preflight'] = client.audit_history
    initialization_started = time.perf_counter()
    (ROOT/'runtime').mkdir(exist_ok=True)
    run_directory = Path(tempfile.mkdtemp(prefix='new-multiturn-', dir=ROOT/'runtime'))
    database, knowledge = initialize_audit(run_directory, inputs, definitions)
    provider = ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
        model=MODEL, reasoning_effort='medium', http_headers=local_model_headers())
    engine = Nl2SqlEngine(database, model_plan_provider=provider, metric_catalog_path=run_directory/'absent_catalog.json')
    assert engine.metric_catalog is None
    provider.catalog, provider.reference_date = None, engine.reference_date
    agent = OmniAgent(engine, knowledge, ConversationStore(), client)
    initialization_wall_ms = (time.perf_counter()-initialization_started)*1000
    report['database_sha256'] = hashlib.sha256(database.read_bytes()).hexdigest()
    report['retrieval_health'] = knowledge.retrieval_health()
    report['status'] = 'running'
    records = []
    execution_started = time.perf_counter()
    for row in cases:
        for api_client in (client, provider):
            api_client.reset_audit()
        query_started = time.perf_counter()
        try:
            result = agent.query(row['question'], session_id=row['session_id'])
            checks, error = evaluate_result(row, result, database), None
        except Exception as exc:
            result, checks, error = None, {'completed':False}, type(exc).__name__
        audits, dropped = collect_audits({'omni':client, 'nl2sql':provider})
        completed = [audit for audit in audits if audit.get('status')=='completed']
        checks['verified_api_calls'] = bool(completed) and all(a.get('model_verified') is True and a.get('model')==MODEL for a in completed) and dropped==0
        checks['requested_only_allowed_model'] = bool(audits) and all(a.get('model')==MODEL for a in audits)
        record = {**row, 'expected_rows':expected_rows(database,row), 'checks':checks,
            'pass':all(checks.values()), 'result':result, 'error_type':error,
            'latency_ms':round((time.perf_counter()-query_started)*1000,3),
            'api_audits':audits, 'api_summary':summarise_audits(audits,dropped)}
        records.append(record)
        report.update(cases=records, total=len(records), passed=sum(r['pass'] for r in records))
        save()
        print(json.dumps({'id':row['id'], 'pass':record['pass'], 'checks':checks}, ensure_ascii=False), flush=True)
        if any(a.get('http_status') in {401,403} for a in audits):
            report['status'] = 'authentication_or_access_rejected'
            break
    execution_wall_ms = (time.perf_counter()-execution_started)*1000
    if report['status']=='running':
        report['status'] = 'passed' if all(r['pass'] for r in records) else 'some_cases_failed'
    audits = [*report['preflight'], *(a for row in records for a in row['api_audits'])]
    report.update(api_summary=summarise_audits(audits, sum(r['api_summary']['dropped_calls'] for r in records)),
        not_run=[r['id'] for r in cases if r['id'] not in {record['id'] for record in records}],
        execution_metrics=execution_metrics(records, execution_wall_ms),
        timings={'preflight_wall_ms':round(preflight_wall_ms,3), 'initialization_wall_ms':round(initialization_wall_ms,3),
                 'queries_wall_ms':round(execution_wall_ms,3), 'configuration_to_report_wall_ms':round((time.perf_counter()-started)*1000,3)})
    save()
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    raise SystemExit(main())
