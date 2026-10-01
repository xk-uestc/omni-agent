"""Save honest, reproducible local acceptance evidence; no contest score inference."""
from __future__ import annotations

import json
import argparse
import platform
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dense', action='store_true')
    args = parser.parse_args()
    from backend.knowledge_store import KnowledgeStore
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.dependency_agent import DependencyAgent
    embedder = None
    if args.dense:
        from backend.dense_retrieval import LocalBgeEmbedder
        embedder = LocalBgeEmbedder(ROOT / 'models/bge-small-zh-v1.5')
    store = KnowledgeStore(ROOT / 'runtime/knowledge', embedder=embedder)
    engine = Nl2SqlEngine(ROOT / 'ict-track8/data/demo_sales.sqlite')
    qa = []
    for case in json.loads((ROOT / 'samples/qa_gold.json').read_text(encoding='utf-8')):
        started = time.perf_counter()
        result = store.answer(case['question'])
        sources = [hit['metadata']['document_id'] for hit in result['citations']]
        source_ok = not (case.get('source') or case.get('sources')) or bool(set(sources) & set(case.get('sources', [case.get('source')])))
        # The expected answer must occur in the citation from the required source, not a different document.
        required_citations = [hit for hit in result['citations'] if hit['metadata']['document_id'] in case.get('sources', [case.get('source')])]
        text = '\n'.join(hit['snippet'] for hit in required_citations)
        passed = source_ok and all(value in text for value in case['contains']) and result['status'] == case.get('expected_status', 'ok')
        faithful = all(hit['snippet'] in next(record.content for record in store.records() if record.document_id == hit['document_id']) for hit in result['citations'])
        qa.append({'id': case['id'], 'question': case['question'], 'pass': passed, 'faithful_quote': faithful, 'sources': sources, 'answer': result['answer'], 'latency_ms': round((time.perf_counter()-started)*1000, 3)})
    sql = []
    with sqlite3.connect(engine.database_path) as connection:
        for region in ['华东', '华南', '华北']:
            for year in [2024, 2025]:
                for label, expression in [('销售额', 'SUM(sales_amount)'), ('订单数', 'COUNT(*)')]:
                    question = f'{year}年{region}地区{label}'
                    expected = connection.execute(f'SELECT {expression} FROM sales_orders WHERE region=? AND order_date>=? AND order_date<?', (region, f'{year}-01-01', f'{year+1}-01-01')).fetchone()[0]
                    result = engine.answer(question)
                    observed = result.rows[0].get(label) if result.status == 'ok' and result.rows else None
                    sql.append({'question': question, 'expected': expected, 'observed': observed, 'pass': observed == expected, 'planner_source': result.plan['planner_source']})
    ref = lambda node, path=[]: {'ref': node, 'path': path}
    task = lambda name, tool, args: {'id': name, 'tool': tool, 'args': args}
    plans = [
        ('document_formula_sql', [task('definition','document_formula',{'document_id':'metric-definitions','label':'客单价'}),task('values','sql',{'question':'2025年华东地区销售额和订单数'}),task('result','calculate',{'formula':ref('definition'),'parameters':{'销售额':ref('values',['rows',0,'销售额']),'订单数':ref('values',['rows',0,'订单数'])}})], 29584/3),
        ('three_source_forecast', [task('definition','document_formula',{'document_id':'forecast-report','label':'目标销售额'}),task('base','sql',{'question':'2025年华东地区销售额'}),task('growth','document_cell',{'document_id':'region-targets','where':{'地区':'华东','年份':2026},'column':'目标增长率'}),task('result','calculate',{'formula':ref('definition'),'parameters':{'基准销售额':ref('base',['rows',0,'销售额']),'目标增长率':ref('growth')}})],29584*1.12),
        ('document_to_sql',[task('region','document_cell',{'document_id':'region-targets','where':{'地区':'华东','年份':2026},'column':'地区'}),task('result','sql',{'question':['2025年',ref('region',['value']),'地区销售额']})],None),
        ('sql_to_document',[task('ranking','sql',{'question':'2025年各地区销售额排名'}),task('result','search',{'query':[ref('ranking',['rows',0,'region']),'冠军经验']})],None),
        ('multiple_documents',[task('policy','search',{'query':'紧急工单首次响应时间'}),task('fact','search_fact',{'evidence':ref('policy'),'scope':'紧急工单','label':'首次响应','unit':'小时'}),task('standard','document_cell',{'document_id':'service-thresholds','where':{'工单优先级':'紧急','适用版本':'2025'},'column':'首次响应小时'}),task('result','compare',{'left':ref('fact'),'right':ref('standard'),'operator':'le'})],None),
    ]
    fusion = []
    agent = DependencyAgent(engine, store)
    for name, tasks, expected in plans:
        result = agent.run(tasks)
        passed = result['status'] == 'ok'
        if expected is not None:
            passed = passed and abs(result['results']['result']['value'] - expected) < 1e-6
        if name == 'document_to_sql':
            passed = passed and result['results']['result']['rows'][0]['销售额'] == 29584
        if name == 'sql_to_document':
            passed = passed and any(hit['metadata']['document_id'] == 'champion-method' and '华东' in hit['snippet'] for hit in result['results']['result']['hits'])
        if name == 'multiple_documents':
            final = result['results'].get('result', {})
            passed = passed and final.get('matched') is True and final.get('operator') == 'le' and final['left']['value'] == final['right']['value'] == 2
        fusion.append({'name': name, 'pass': passed, 'result': result})
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'environment': {'python': platform.python_version(), 'platform': platform.platform()},
              'scope': 'local_synthetic_acceptance_not_public_benchmark', 'planner': 'rules', 'generation': 'attributed_extracts', 'retrieval': store.retrieval_health(),
              'summary': {'qa_pass': sum(item['pass'] for item in qa), 'qa_total': len(qa), 'quote_faithful': all(item['faithful_quote'] for item in qa), 'sql_pass': sum(item['pass'] for item in sql), 'sql_total': len(sql), 'fusion_pass': sum(item['pass'] for item in fusion), 'fusion_total': len(fusion)},
              'qa': qa, 'sql': sql, 'fusion': fusion}
    output = 'HYBRID_ACCEPTANCE_REPORT.json' if args.dense else 'INDEPENDENT_ACCEPTANCE_REPORT.json'
    (ROOT / 'docs' / output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False))
    return 0 if all(item['pass'] for item in [*qa, *sql, *fusion]) else 1


if __name__ == '__main__':
    raise SystemExit(main())
