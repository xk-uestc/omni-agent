"""Paired real-model SQL routing latency; same questions, database and guards."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.omni_agent import OmniAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
from backend.nl2sql.seed import initialize_database
from backend.responses_client import StructuredResponses
from backend.session import ConversationStore
from model_runtime import enable_local_model, local_model_headers
from evaluate_ohr_bench import implementation_snapshot, safe_audits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().parent != ROOT/'docs':
        parser.error('New docs report required')
    enable_local_model()
    before = implementation_snapshot()
    folder = Path(tempfile.mkdtemp(prefix='sql-route-latency-', dir=ROOT/'runtime'))
    database = initialize_database(folder/'business.sqlite')
    knowledge = KnowledgeStore(folder/'knowledge')
    knowledge.ingest('产品保修政策：保修期十二个月。'.encode(), document_id='warranty',
        title='产品保修政策', modality='txt', filename='warranty.txt')
    cases = [
        ('2025年华东地区销售额', "SELECT SUM(sales_amount) FROM sales_orders WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' AND region='华东'"),
        ('2025年各地区销售额排名', "SELECT region,SUM(sales_amount),DENSE_RANK() OVER (ORDER BY SUM(sales_amount) DESC) FROM sales_orders WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' GROUP BY region ORDER BY SUM(sales_amount) DESC"),
        ('按月统计2025年华东地区销售额', "SELECT strftime('%Y-%m',order_date),SUM(sales_amount) FROM sales_orders WHERE order_date >= '2025-01-01' AND order_date < '2026-01-01' AND region='华东' GROUP BY strftime('%Y-%m',order_date) ORDER BY 1")]
    with sqlite3.connect(database) as connection:
        references = [connection.execute(sql).fetchall() for _, sql in cases]
    def run(item):
        index, enabled = item
        client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna', reasoning='medium', http_headers=local_model_headers())
        client.supports_verified_sql_routing = enabled
        provider = ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna', reasoning_effort='medium', http_headers=local_model_headers())
        engine = Nl2SqlEngine(database, model_plan_provider=provider)
        provider.catalog, provider.reference_date = engine.metric_catalog, engine.reference_date
        events = []
        started = time.perf_counter()
        agent = OmniAgent(engine, KnowledgeStore(knowledge.root), ConversationStore(), client)
        result = agent.query(cases[index][0], reset_context=True,
            trace_callback=lambda event: events.append({**event,'elapsed_ms':round((time.perf_counter()-started)*1000,3)}))
        duration = (time.perf_counter()-started)*1000
        sql_result = result['result']
        values = sorted(tuple(row.values()) for row in sql_result.get('rows',[]))
        expected = sorted(references[index])
        correct = (result['status']=='ok' and result['route']=='sql' and values==expected
            and sql_result['plan']['planner_source']=='model_validated')
        audits = safe_audits(client) + safe_audits(provider.client)
        record = {'question':cases[index][0], 'routing_enabled':enabled,'duration_ms':round(duration,3),
            'passed':correct,'model_requests':len(audits),'reference_values':expected,
            'reference_sql_scoring_only':cases[index][1], 'events':events,'result':result,'api_audits':audits}
        print(json.dumps({k:record[k] for k in ('question','routing_enabled','duration_ms','passed','model_requests')},ensure_ascii=False),flush=True)
        return record
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(run, [(i, flag) for i in range(len(cases)) for flag in (False,True)]))
    after = implementation_snapshot()
    report = {'created_at':datetime.now(timezone.utc).isoformat(),'scope':'paired_synthetic_SQL_routing_latency_not_official_accuracy',
        'model':'gpt-6-luna','reasoning':'medium','workers':2,'folder':str(folder),'records':records,
        'implementation_file_sha256':before,'implementation_file_sha256_end':after,'implementation_stable':before==after,
        'limitations':['One paired run per question; shared gateway load and model variation affect wall time.',
            'Fast route changes source selection only; SQL model planning and all engine checks remain.',
            'Exact result values compared against independent SQLite queries; no reference SQL supplied to production.']}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if before==after and all(r['passed'] for r in records) else 1


if __name__=='__main__':
    raise SystemExit(main())
