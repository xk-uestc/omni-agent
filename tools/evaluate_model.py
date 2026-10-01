"""Opt-in real gpt-6-luna checks; rules and stubs never count as model success."""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))
MODEL = 'gpt-6-luna'
TOKEN_FIELDS = ('input_tokens', 'output_tokens', 'total_tokens', 'cached_input_tokens', 'reasoning_tokens')


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


def execute_cases(agent, clients, cases, database):
    records, stopped = [], None
    for row in cases:
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
        if any(a.get('http_status') in {401, 403} for a in audits):
            stopped = 'authentication_or_access_rejected'
            break
    return records, stopped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', choices=[MODEL], default=MODEL)
    parser.add_argument('--full', action='store_true')
    parser.add_argument('--preview', action='store_true', help='不读取密钥、不联网，列出验收题及判据')
    args = parser.parse_args()
    cases = suite() if args.full else suite()[:1]
    if args.preview:
        print(json.dumps({'mode': 'preview_no_credentials_no_api', 'model': MODEL, 'planned_cases': len(cases),
                          'case_groups': dict(Counter(row['kind'] for row in cases)), 'cases': cases}, ensure_ascii=False, indent=2))
        return 0
    from model_runtime import enable_local_model
    from backend.responses_client import StructuredResponses, GenerationError, object_schema
    enable_local_model(args.model)
    client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'], model=MODEL, reasoning=os.environ['ICT8_OPENAI_REASONING'])
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'scope': 'real_api_synthetic_development_cases_not_public_or_blind_accuracy',
              'model': MODEL, 'reasoning': client.reasoning, 'python': platform.python_version(),
              'planned_cases': len(cases), 'case_groups': dict(Counter(row['kind'] for row in cases)),
              'cases': [], 'passed': 0, 'total': 0, 'status': 'preflight_failed'}
    try:
        preflight = client.generate('只返回ok=true。', {}, object_schema({'ok': {'type': 'boolean'}}), name='model_preflight', max_tokens=1000)
        if preflight.get('ok') is not True:
            raise GenerationError('预检未返回有效结构化结果')
    except GenerationError:
        report['preflight'] = client.audit_history
        report['api_summary'] = summarise_audits(client.audit_history)
        report['status'] = 'authentication_failed' if client.audit.get('http_status') == 401 else 'preflight_failed'
        report['not_run'] = [row['id'] for row in cases]
        (ROOT/'docs/REAL_MODEL_REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'status': report['status'], 'http_status': client.audit.get('http_status'), 'planned_cases': len(cases), 'executed_cases': 0}, ensure_ascii=False))
        return 1
    report['preflight'] = client.audit_history
    from backend.grounded_generation import GroundedGenerator
    from backend.knowledge_store import KnowledgeStore
    from backend.dense_retrieval import LocalBgeEmbedder
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
    from backend.omni_agent import OmniAgent
    from backend.session import ConversationStore
    database = ROOT/'ict-track8/data/demo_sales.sqlite'
    provider = ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'], model=MODEL, reasoning_effort=client.reasoning)
    engine = Nl2SqlEngine(database, model_plan_provider=provider)
    provider.catalog, provider.reference_date = engine.metric_catalog, engine.reference_date
    store = KnowledgeStore(ROOT/'runtime/knowledge', embedder=LocalBgeEmbedder(ROOT/'models/bge-small-zh-v1.5'), generator=GroundedGenerator(client))
    agent = OmniAgent(engine, store, ConversationStore(), client)
    records, stopped = execute_cases(agent, {'omni_and_generation': client, 'nl2sql': provider}, cases, database)
    audits = [*report['preflight'], *(a for row in records for a in row['api_audits'])]
    dropped = sum(row['api_summary']['dropped_calls'] for row in records)
    report.update(cases=records, passed=sum(r['pass'] for r in records), total=len(records),
                  status=stopped or ('passed' if all(r['pass'] for r in records) else 'some_cases_failed'),
                  api_summary=summarise_audits(audits, dropped), not_run=[row['id'] for row in cases[len(records):]])
    (ROOT/'docs/REAL_MODEL_REPORT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
