"""Opt-in real gpt-6-luna checks; rules and stubs never count as model success."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sqlite3
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
MODEL = 'gpt-6-luna'
TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens', 'reasoning_tokens')


def implementation_hashes(root=ROOT):
    """Pin executable source, never credentials or runtime-generated files."""
    files = sorted((root/'ict-track8/backend').rglob('*.py'))
    files += [root/'tools/evaluate_model.py', root/'tools/model_runtime.py']
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files if path.is_file()}


def case(identifier, question, kind, **kwargs):
    return {'id': identifier, 'question': question, 'kind': kind, **kwargs}


def suite():
    definitions = json.loads((ROOT/'samples/qa_gold.json').read_text(encoding='utf-8'))
    cases = [case('generated_rag', '标准硬件产品保修期有多久？', 'document', sources=['warranty-policy'], contains=['12个月'])]
    cases.extend(case(row['id'], row['question'], 'document', sources=row.get('sources', [row['source']] if 'source' in row else []),
                      contains=row['contains'], expected_status=row.get('expected_status', 'ok')) for row in definitions)
    for region in ('华东', '华南', '华北'):
        for year in (2024, 2025):
            for metric in ('销售额', '订单数'):
                cases.append(case(f'sql-{region}-{year}-{metric}', f'{year}年{region}地区{metric}', 'sql', region=region, year=year, metric=metric))
    cases.extend([
        case('formula_sql', '按指标计算文档中的客单价公式计算2025年华东地区客单价，销售額和订单数从数据库取值。', 'formula', region='华东', year=2025),
        case('forecast_three_sources', '根据经营预测PDF公式、区域目标Excel中华东2026年的增长率，以及数据库2025年华东销售额，算2026年目标销售额。', 'forecast', region='华东', year=2025, growth=.12),
        case('document_to_sql', '从区域目标Excel中定位2026年目标增长率为12%的地区，作为过滤条件查询数据库中该地区2025年的销售额。', 'document_to_sql', region='华东', year=2025, metric='销售额'),
        case('sql_to_document', '先从数据库查2025年销售额排名第一的地区，再根据该地区检索冠军团队的方法，保留来源。', 'sql_to_document', sources=['champion-method'], contains=['华东', '重点客户分层']),
        case('multiple_documents', '从售后流程文档提取紧急工单首次响应小时数，与2025版响应标准Excel比较，文档要求是否小于等于标准？', 'threshold'),
        case('version_policy', '按照退货政策历史版本，2024-12-31和2025-01-01的无理由退货期限分别是多少，比较是否变化。', 'policy'),
    ])
    dialogue = [
        case('cross-turn-1', '按指标公式文档计算2025年华东地区客单价，销售额和订单数从数据库取。', 'formula', region='华东', year=2025),
        case('cross-turn-2', '那华南呢，仍按同一公式？', 'formula', region='华南', year=2025),
        case('cross-turn-3', '根据经营预测PDF公式、区域目标Excel中华东2026增长率和数据库2025年华东销售额，算2026年目标销售额。', 'forecast', region='华东', year=2025, growth=.12),
        case('cross-turn-4', '那华南2026年的目标呢，基准还是2025年，增长率取该地区Excel？', 'forecast', region='华南', year=2025, growth=.10),
        case('cross-turn-5', '换个主题：从售后流程文档提取紧急工单首次响应小时数，与2025版响应标准Excel比较，文档要求是否小于等于标准？', 'threshold'),
        case('cross-topic-switch', '标准硬件产品保修期是多少个月？', 'document', sources=['warranty-policy'], contains=['12个月']),
        case('cross-explicit-reset', '2025年华东地区销售额', 'sql', region='华东', year=2025, metric='销售额', reset_context=True),
    ]
    for i, row in enumerate(dialogue):
        cases.append({**row, 'session_id': 'model-cross-five', 'context_turns': 0 if row.get('reset_context') else i})
    sql_dialogue = [('2025年华东地区销售额', '华东', 2025, '销售额'), ('那华南呢', '华南', 2025, '销售额'),
                    ('那2024年呢', '华南', 2024, '销售额'), ('那订单数呢', '华南', 2024, '订单数'), ('那华北呢', '华北', 2024, '订单数')]
    for i, (question, region, year, metric) in enumerate(sql_dialogue):
        cases.append(case(f'sql-turn-{i+1}', question, 'sql', region=region, year=year, metric=metric, session_id='model-sql-five', context_turns=i))
    cases.extend([
        case('clarify-missing-metric', '2025年华东地区', 'clarification', session_id='model-clarification', context_turns=0),
        case('clarify-refill', '我查销售额，仍是2025年华东地区', 'sql', region='华东', year=2025, metric='销售额', session_id='model-clarification', context_turns=1),
    ])
    return cases


def summarise_audits(audits, dropped=0):
    totals = {}
    for field in TOKEN_FIELDS:
        known = [a[field] for a in audits if type(a.get(field)) is int and a[field] >= 0]
        totals[field] = {'reported_sum': sum(known), 'unknown_calls': len(audits)-len(known), 'complete': len(known) == len(audits) and dropped == 0}
    return {'calls': len(audits)+dropped, 'retained_calls': len(audits), 'dropped_calls': dropped,
            'completed_calls': sum(a.get('status') == 'completed' for a in audits), 'failed_calls': sum(a.get('status') != 'completed' for a in audits),
            'tokens': totals, 'api_latency_ms': round(sum(a.get('latency_ms', 0) for a in audits), 3),
            'usd_cost': None, 'pricing_status': 'third_party_price_not_supplied_no_estimate'}


def execution_metrics(records, wall_ms):
    """Observed bounded mixed-workload throughput; API latency sums are not wall time."""
    latencies = sorted(row['latency_ms'] for row in records)
    def percentile(fraction):
        if not latencies:
            return None
        position = (len(latencies)-1)*fraction
        lower, upper = math.floor(position), math.ceil(position)
        return round(latencies[lower]+(latencies[upper]-latencies[lower])*(position-lower), 3)
    seconds = wall_ms / 1000
    audits = [audit for row in records for audit in row['api_audits']]
    corrections = [audit for audit in audits if audit.get('operation', '').endswith(('_correction', '_repair'))]
    generation_attempts = [row['result']['result'].get('generation_attempts', [])
                           for row in records if isinstance(row.get('result'), dict)
                           and isinstance(row['result'].get('result'), dict)]
    return {'scope': 'in_process_agent_mixed_development_workload_not_http_service_or_production_sla',
            'wall_ms': round(wall_ms, 3), 'attempted_queries': len(records),
            'passed_queries': sum(row['pass'] for row in records),
            'attempted_queries_per_second': round(len(records)/seconds, 6) if seconds > 0 else None,
            'passed_queries_per_second': round(sum(row['pass'] for row in records)/seconds, 6) if seconds > 0 else None,
            'query_latency_p50_ms': percentile(.5), 'query_latency_p95_ms': percentile(.95),
            'query_latency_max_ms': max(latencies) if latencies else None,
            'repair_calls': len(corrections), 'repair_operations': dict(Counter(a['operation'] for a in corrections)),
            'grounded_first_attempt_rejections': sum(bool(a) and a[0].get('validation_status') == 'rejected' for a in generation_attempts),
            'grounded_correction_validated_cases': sum(len(a) == 2 and a[1].get('validation_status') == 'validated' for a in generation_attempts),
            'transport_retry_count': None,
            'transport_retry_limit': 'API audits include retries; repeated SQL operations can be distinct DAG tools and are not classified as retries',
            'warm_cold_limit': 'shared process/store; lazy loading and cache states vary; not independent cold-start trials',
            'preflight_and_initialization_excluded': True}


def select_cases(cases, requested_ids):
    """Expand selected conversational cases with every preceding session turn.

    Keep the original suite order, never rewrite context_turns or silently drop
    earlier turns that contributed to the observed failure.
    """
    requested = set(requested_ids)
    known = {row['id'] for row in cases}
    unknown = requested - known
    if unknown:
        raise ValueError('未知测试ID: ' + ', '.join(sorted(unknown)))
    selected = set(requested)
    latest_session_position = {}
    for position, row in enumerate(cases):
        if row['id'] in requested and row.get('session_id'):
            latest_session_position[row['session_id']] = position
    for position, row in enumerate(cases):
        if row.get('session_id') in latest_session_position and position <= latest_session_position[row['session_id']]:
            selected.add(row['id'])
    return [row for row in cases if row['id'] in selected]


def report_output(value=None):
    path = (ROOT / (value or 'docs/REAL_MODEL_REPORT.json')).resolve()
    if path.suffix.lower() != '.json' or not path.is_relative_to((ROOT/'docs').resolve()):
        raise ValueError('验收报告必须是项目docs目录内的JSON文件')
    if value is not None and path.exists():
        raise ValueError('指定报告已存在，不能覆盖历史证据')
    return path


def collect_audits(clients):
    return ([{**audit, 'component': name} for name, client in clients.items() for audit in client.audit_history],
            sum(client.audit_dropped_count for client in clients.values()))


def oracle(database, row):
    expression = {'销售额': 'SUM(sales_amount)', '订单数': 'COUNT(*)'}[row.get('metric', '销售额')]
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro', uri=True) as connection:
        return connection.execute(f'SELECT {expression} FROM sales_orders WHERE region=? AND order_date>=? AND order_date<?',
                                  (row['region'], f"{row['year']}-01-01", f"{row['year']+1}-01-01")).fetchone()[0]


def close_number(observed, expected):
    return type(observed) in (int, float) and math.isfinite(observed) and math.isclose(observed, expected, abs_tol=1e-6, rel_tol=1e-9)


def evaluate_result(row, result, database):
    checks = {'model_router': result.get('planner_source') == 'model_validated',
              'status': result.get('status') == row.get('expected_status', 'clarification' if row['kind'] == 'clarification' else 'ok')}
    data, kind = result.get('result', {}), row['kind']
    route = 'document' if kind == 'document' else 'sql' if kind == 'sql' else 'fusion'
    checks['route'] = result.get('route') == route if kind != 'clarification' else result.get('route') in {'sql', 'clarify'}
    if 'context_turns' in row:
        checks['history'] = result.get('context_turns') == row['context_turns']
    sql_results = [data] if result.get('route') == 'sql' else [v for v in data.get('results', {}).values() if isinstance(v, dict) and 'provenance' in v and 'plan' in v]
    if sql_results and kind != 'clarification':
        checks['real_sql_planner'] = all(v.get('plan', {}).get('planner_source') == 'model_validated' for v in sql_results)
    if kind == 'clarification':
        checks['explicit_clarification'] = bool(data.get('clarification')) and not data.get('sql') and not data.get('rows')
    elif kind == 'document':
        if row.get('expected_status') == 'insufficient_evidence':
            checks['abstention'] = not data.get('claims')
        else:
            checks['real_generation'] = data.get('answer_mode') == 'model_grounded'
            citations = {h['citation_id']: h for h in data.get('citations', [])}
            quotes = [s['quote'] for claim in data.get('claims', []) for s in claim.get('support', [])
                      if citations.get(s['citation_id'], {}).get('metadata', {}).get('document_id') in row['sources']]
            checks['required_source_and_facts'] = bool(quotes) and all(value in '\n'.join(quotes) for value in row['contains'])
    elif kind == 'sql':
        checks['value'] = bool(data.get('rows')) and close_number(data['rows'][0].get(row['metric']), oracle(database, row))
    else:
        results = data.get('results', {})
        final = list(results.values())[-1] if results else {}
        tools = {event.get('tool') for event in data.get('trace', []) if event.get('status') == 'complete'}
        if kind in {'formula', 'forecast'}:
            expected = oracle(database, row)
            expected = expected / oracle(database, {**row, 'metric': '订单数'}) if kind == 'formula' else expected*(1+row['growth'])
            checks['value'] = close_number(final.get('value'), expected)
            checks['actual_dependencies'] = {'document_formula', 'sql', 'calculate'} <= tools and bool(data.get('edges'))
            if kind == 'forecast':
                checks['excel_parameter'] = 'document_cell' in tools
                checks['forecast_years'] = final.get('temporal_validation', {}).get('status') == 'verified'
        elif kind == 'document_to_sql':
            checks['value'] = bool(final.get('rows')) and close_number(final['rows'][0].get(row['metric']), oracle(database, row))
            checks['actual_dependencies'] = {'document_cell', 'sql'} <= tools and bool(data.get('edges'))
        elif kind == 'sql_to_document':
            checks['retrieved_source'] = any(h.get('metadata', {}).get('document_id') in row['sources'] and all(v in h.get('snippet', '') for v in row['contains']) for h in final.get('hits', []))
            checks['actual_dependencies'] = {'sql', 'search'} <= tools and bool(data.get('edges'))
        elif kind == 'threshold':
            checks['actual_comparison'] = final.get('operator') == 'le' and final.get('matched') is True
            checks['two_values'] = final.get('left', {}).get('value') == final.get('right', {}).get('value') == 2
            checks['actual_dependencies'] = {'search', 'search_fact', 'document_cell', 'compare'} <= tools and bool(data.get('edges'))
        elif kind == 'policy':
            checks['version_comparison'] = final.get('status') == 'different' and final.get('left', {}).get('value') == 5 and final.get('right', {}).get('value') == 7
            checks['actual_dependencies'] = {'policy_select', 'compare'} <= tools and bool(data.get('edges'))
    return checks


def execute_cases(agent, clients, cases, database, *, on_record=None, stop_event=None):
    records, stopped = [], None
    for row in cases:
        if stop_event is not None and stop_event.is_set():
            stopped = 'authentication_or_access_rejected'
            break
        for client in clients.values():
            client.reset_audit()
        started = time.perf_counter()
        try:
            result = agent.query(row['question'], session_id=row.get('session_id'), reset_context=row.get('reset_context', False))
            checks, error = evaluate_result(row, result, database), None
        except Exception as exc:
            result, checks, error = None, {'completed': False}, type(exc).__name__
        audits, dropped = collect_audits(clients)
        completed = [a for a in audits if a.get('status') == 'completed']
        checks['verified_api_calls'] = bool(completed) and all(a.get('model_verified') is True and a.get('model') == MODEL for a in completed) and dropped == 0
        checks['requested_only_allowed_model'] = bool(audits) and all(a.get('model') == MODEL for a in audits)
        record = {**row, 'pass': all(checks.values()), 'checks': checks, 'error_type': error,
                  'latency_ms': round((time.perf_counter()-started)*1000, 3), 'result': result,
                  'api_audits': audits, 'api_summary': summarise_audits(audits, dropped)}
        records.append(record)
        print(json.dumps({'id': row['id'], 'pass': record['pass'], 'checks': checks}, ensure_ascii=False), flush=True)
        if on_record:
            on_record(record)
        if any(a.get('http_status') in {401, 403} for a in audits):
            stopped = 'authentication_or_access_rejected'
            if stop_event is not None:
                stop_event.set()
            break
    return records, stopped


def execute_grouped(agent, clients, cases, database, *, workers=1, on_record=None):
    """Parallelize independent conversations, preserving every session's order."""
    if workers == 1:
        return execute_cases(agent, clients, cases, database, on_record=on_record)
    groups = {}
    for row in cases:
        key = ('session', row['session_id']) if row.get('session_id') else ('case', row['id'])
        groups.setdefault(key, []).append(row)
    stop_event, records, stopped = threading.Event(), [], None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(execute_cases, agent, clients, group, database,
                               on_record=on_record, stop_event=stop_event) for group in groups.values()]
        for future in as_completed(futures):
            completed, reason = future.result()
            records.extend(completed)
            stopped = stopped or reason
    order = {row['id']: index for index, row in enumerate(cases)}
    records.sort(key=lambda record: order[record['id']])
    return records, stopped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=[MODEL], default=MODEL)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--case-ids', nargs='+', help='定向题ID；逗号或空格分隔，自动包含该会话的所有前置轮')
    parser.add_argument('--output', help='新验收报告路径；必须位于项目docs，不覆盖既有历史报告')
    parser.add_argument('--workers', type=int, choices=(1, 2, 3), default=1,
                        help='独立会话并行数；同一多轮会话始终顺序执行')
    parser.add_argument('--preview', action='store_true', help='不读取密钥、不联网，列出验收题及判据')
    args = parser.parse_args()
    all_cases = suite()
    requested_ids = [identifier.strip() for value in (args.case_ids or [])
                     for identifier in value.split(',') if identifier.strip()]
    try:
        if args.case_ids and not requested_ids:
            raise ValueError('定向题ID不能为空')
        cases = select_cases(all_cases, requested_ids) if requested_ids else all_cases if args.full else all_cases[:1]
        output = report_output(args.output)
    except ValueError as exc:
        parser.error(str(exc))
    if args.preview:
        print(json.dumps({'mode': 'preview_no_credentials_no_api', 'model': MODEL, 'planned_cases': len(cases),
                          'requested_case_ids': requested_ids, 'output': str(output),
                          'case_groups': dict(Counter(row['kind'] for row in cases)), 'cases': cases}, ensure_ascii=False, indent=2))
        return 0
    from model_runtime import enable_local_model, local_model_headers
    from backend.responses_client import StructuredResponses, GenerationError, object_schema
    overall_started = time.perf_counter()
    enable_local_model(args.model)
    client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'], model=MODEL, reasoning=os.environ['ICT8_OPENAI_REASONING'], http_headers=local_model_headers())
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'scope': 'real_api_synthetic_development_cases_not_public_or_blind_accuracy',
              'model': MODEL, 'reasoning': client.reasoning, 'python': platform.python_version(),
              'workers': args.workers,
              'planned_cases': len(cases), 'case_groups': dict(Counter(row['kind'] for row in cases)),
              'cases': [], 'passed': 0, 'total': 0, 'status': 'preflight_failed'}
    report['cases_sha256'] = hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    report['implementation_sha256_start'] = implementation_hashes()
    if requested_ids:
        report['scope'] = 'real_api_targeted_development_cases_with_required_history_not_full_suite'
        report['requested_case_ids'] = requested_ids
        report['history_prerequisite_case_ids'] = [row['id'] for row in cases if row['id'] not in requested_ids]
    output.parent.mkdir(parents=True, exist_ok=True)
    preflight_started = time.perf_counter()
    try:
        preflight = client.generate('只返回ok=true。', {}, object_schema({'ok': {'type': 'boolean'}}), name='model_preflight', max_tokens=1000)
        if preflight.get('ok') is not True:
            raise GenerationError('预检未返回有效结构化结果')
    except GenerationError:
        report['preflight'] = client.audit_history
        report['api_summary'] = summarise_audits(client.audit_history)
        report['status'] = 'authentication_failed' if client.audit.get('http_status') == 401 else 'preflight_failed'
        report['not_run'] = [row['id'] for row in cases]
        report['timings'] = {'preflight_wall_ms': round((time.perf_counter()-preflight_started)*1000, 3),
                             'configuration_to_report_wall_ms': round((time.perf_counter()-overall_started)*1000, 3)}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'status': report['status'], 'http_status': client.audit.get('http_status'), 'planned_cases': len(cases), 'executed_cases': 0}, ensure_ascii=False))
        return 1
    report['preflight'] = client.audit_history
    preflight_wall_ms = (time.perf_counter()-preflight_started)*1000
    initialization_started = time.perf_counter()
    from backend.grounded_generation import GroundedGenerator
    from backend.knowledge_store import KnowledgeStore
    from backend.dense_retrieval import LocalBgeEmbedder
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
    from backend.omni_agent import OmniAgent
    from backend.session import ConversationStore
    database = ROOT/'ict-track8/data/demo_sales.sqlite'
    provider = ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'], model=MODEL, reasoning_effort=client.reasoning, http_headers=local_model_headers())
    engine = Nl2SqlEngine(database, model_plan_provider=provider)
    provider.catalog, provider.reference_date = engine.metric_catalog, engine.reference_date
    store = KnowledgeStore(ROOT/'runtime/knowledge', embedder=LocalBgeEmbedder(ROOT/'models/bge-small-zh-v1.5'), generator=GroundedGenerator(client))
    agent = OmniAgent(engine, store, ConversationStore(), client)
    initialization_wall_ms = (time.perf_counter()-initialization_started)*1000
    report['database_sha256'] = hashlib.sha256(database.read_bytes()).hexdigest()
    progress_lock, progress_records = threading.Lock(), []
    order = {row['id']: index for index, row in enumerate(cases)}
    progress_path = ROOT/'runtime/model-evaluation-progress.json'
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    def save_progress(record):
        with progress_lock:
            progress_records.append(record)
            ordered = sorted(progress_records, key=lambda item: order[item['id']])
            progress_path.write_text(json.dumps({**report, 'status': 'running',
                'cases': ordered, 'total': len(ordered), 'passed': sum(item['pass'] for item in ordered)},
                ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    execution_started = time.perf_counter()
    records, stopped = execute_grouped(agent, {'omni_and_generation': client, 'nl2sql': provider}, cases, database,
                                      workers=args.workers, on_record=save_progress)
    execution_wall_ms = (time.perf_counter()-execution_started)*1000
    audits = [*report['preflight'], *(a for row in records for a in row['api_audits'])]
    dropped = sum(row['api_summary']['dropped_calls'] for row in records)
    report.update(cases=records, passed=sum(r['pass'] for r in records), total=len(records),
                  status=stopped or ('passed' if all(r['pass'] for r in records) else 'some_cases_failed'),
                  api_summary=summarise_audits(audits, dropped), not_run=[row['id'] for row in cases if row['id'] not in {record['id'] for record in records}])
    report['execution_metrics'] = execution_metrics(records, execution_wall_ms)
    report['implementation_sha256_end'] = implementation_hashes()
    report['implementation_stable'] = report['implementation_sha256_start'] == report['implementation_sha256_end']
    if not report['implementation_stable']:
        report['status'] = 'implementation_changed_during_run_not_final_acceptance'
    report['timings'] = {'preflight_wall_ms': round(preflight_wall_ms, 3),
                         'initialization_wall_ms': round(initialization_wall_ms, 3),
                         'queries_wall_ms': round(execution_wall_ms, 3),
                         'configuration_to_report_wall_ms': round((time.perf_counter()-overall_started)*1000, 3),
                         'process_startup_excluded': True}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
