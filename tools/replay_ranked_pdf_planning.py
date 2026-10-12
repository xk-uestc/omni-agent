"""Paired real-original integration: manual replay vs real model vs HTTP/SSE.

Database rows are an explicitly artificial integration mapping. No Gold, graph,
winner or answer is passed to the real model. A shared ledger caps this round.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root', type=Path, default=ROOT)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=['manual', 'model', 'http', 'sse'], default='manual')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--ledger', type=Path)
    parser.add_argument('--question', action='append')
    args = parser.parse_args()
    sys.path.insert(0, str(args.code_root / 'ict-track8'))
    from backend.knowledge_store import KnowledgeStore
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.dependency_agent import DependencyAgent
    from backend.omni_agent import OmniAgent
    from backend.session import ConversationStore
    saved = json.loads((ROOT / 'docs/foundation/runs/work_upgrade_20261012/sql-to-public-pdf-grammar-1.json').read_text())
    manifest = json.loads((ROOT / 'benchmarks/real_document_20261011/odb-manifest.json').read_text())
    source = next(d for d in manifest['documents'] if d['split'] == 'dev' and '61520788' in d['image_name'])
    raw = (args.data_root / source['pdf_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == source['pdf_sha256']
    questions = args.question or [saved['question']]
    rows = [(1, '高级熟练工', 80), (2, '熟练工', 50), (3, '半熟练工', 20), (4, '普工', 10), (5, '高级熟练工', 30)]
    catalog = {'version': 'transparent_labor_mapping_v1', 'metrics': [
        {'id': 'hours', 'table': '用工统计', 'column': '工时', 'function': 'SUM', 'label': '总工时',
         'query_alias': '工时', 'aliases': ['工时'], 'unit': '小时'}]}
    model = None
    if args.mode != 'manual':
        from m1c_model_client import BudgetedPlanner
        if args.config is None or args.ledger is None:
            parser.error('model modes require this round config and shared ledger')
        model = BudgetedPlanner(args.config, args.ledger, max_calls=20)
        model.supports_visual_source_review = False
    report = {'classification': 'manual_graph' if model is None else 'real_text_model_autonomous_planning',
              'deployment': 'local_research_client_and_production_code_not_remote_deployment',
              'code_root': str(args.code_root), 'source': source,
              'database_classification': 'artificial_business_mapping_to_public_original',
              'database_rows': rows, 'metric_catalog': catalog, 'mode': args.mode, 'runs': []}
    with tempfile.TemporaryDirectory(prefix='omni-ranked-pdf-') as tmp:
        root = Path(tmp); db = root / 'labor.sqlite'
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE 用工统计(记录ID INTEGER PRIMARY KEY, 工种 TEXT, 工时 REAL)')
            connection.executemany('INSERT INTO 用工统计 VALUES(?,?,?)', rows)
        cat = root / 'catalog.json'; cat.write_text(json.dumps(catalog, ensure_ascii=False))
        engine = Nl2SqlEngine(db, metric_catalog_path=cat)
        knowledge = KnowledgeStore(root / 'knowledge')
        knowledge.ingest(raw, document_id='labor', title='定额教材', modality='pdf', filename=Path(source['pdf_path']).name)
        conversations = ConversationStore()
        agent = OmniAgent(engine, knowledge, conversations, model)
        for question in questions:
            started = time.perf_counter(); events = []
            if args.mode == 'manual':
                result = DependencyAgent(engine, knowledge).run(saved['tasks'], original_question=question)
            elif args.mode == 'model':
                result = agent.query(question, reset_context=True, trace_callback=events.append)
            else:
                os.environ['ICT8_DB_PATH'] = str(db)
                os.environ['ICT8_KNOWLEDGE_DIR'] = str(root / 'http-knowledge')
                from backend import app as api
                from fastapi.testclient import TestClient
                api.engine = engine; api.knowledge_store = knowledge; api.conversation_store = conversations
                api.generation_client = model; api.memory_adapter = None; api.experience_selector = None
                client = TestClient(api.app)
                suffix = '/stream' if args.mode == 'sse' else ''
                response = client.post('/api/v1/omni/query' + suffix, json={'question': question, 'reset_context': True})
                assert response.status_code == 200, response.status_code
                if args.mode == 'http':
                    result = response.json()
                else:
                    result = None
                    for packet in response.text.split('\n\n'):
                        lines = packet.splitlines()
                        event = next((l.removeprefix('event: ') for l in lines if l.startswith('event: ')), '')
                        data = next((l.removeprefix('data: ') for l in lines if l.startswith('data: ')), '')
                        if not data:
                            continue
                        payload = json.loads(data); events.append({'event': event, 'data': payload})
                        if event == 'done':
                            result = payload
                    assert result is not None, 'SSE missing final snapshot'
                # Replay every presented original link through the real API.
                for citation in result.get('result', {}).get('citations', []):
                    original = client.get(citation['source_uri'])
                    assert original.status_code == 200 and hashlib.sha256(original.content).hexdigest() == source['pdf_sha256']
            elapsed = time.perf_counter() - started
            nested = result.get('result', result)
            cell = next((v for v in nested.get('results', {}).values() if v.get('validation') == 'native_table_literal_cell_replayed'), {})
            passed = result['status'] == 'ok' and cell.get('value') == 10.26
            if model is not None:
                passed = passed and result.get('planner_source') == 'model_validated' and bool(nested.get('answer'))
            report['runs'].append({'question': question, 'seconds': elapsed, 'complete_task_passed': passed,
                                   'result': result, 'events': events})
            print(json.dumps({'mode': args.mode, 'status': result['status'], 'passed': passed,
                              'planner': result.get('planner_source'), 'seconds': round(elapsed, 3)}, ensure_ascii=False), flush=True)
            report['model_calls'] = model.calls if model else []
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
