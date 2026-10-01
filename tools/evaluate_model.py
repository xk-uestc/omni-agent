"""Opt-in real API checks with public/synthetic data and no credential artifacts."""
import argparse
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='gpt-6-luna')
    parser.add_argument('--full', action='store_true')
    args = parser.parse_args()
    from model_runtime import enable_local_model
    enable_local_model(args.model)
    from backend.responses_client import StructuredResponses
    from backend.grounded_generation import GroundedGenerator
    from backend.knowledge_store import KnowledgeStore
    from backend.dense_retrieval import LocalBgeEmbedder
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.omni_agent import OmniAgent
    from backend.session import ConversationStore
    client = StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'], model=args.model)
    store = KnowledgeStore(ROOT/'runtime/knowledge', embedder=LocalBgeEmbedder(ROOT/'models/bge-small-zh-v1.5'), generator=GroundedGenerator(client))
    engine = Nl2SqlEngine(ROOT/'ict-track8/data/demo_sales.sqlite')
    agent = OmniAgent(engine, store, ConversationStore(), client)
    cases = [
        ('generated_rag', '标准硬件产品保修期有多久？', 'document', '12个月'),
        ('formula_sql', '按指标计算文档中的客单价公式计算2025年华东地区客单价，销售額和订单数从数据库取值。', 'fusion', 29584/3),
        ('forecast_three_sources', '根据经营预测PDF的公式、区域目标Excel中华东2026年的增长率，以及数据库中2025年华东销售额，算出2026年目标销售额。', 'fusion', 29584*1.12),
        ('version_policy', '按照退货政策历史版本，2024-12-31和2025-01-01的无理由退货期限分别是多少，比较是否变化。', 'fusion', 'different'),
    ]
    records = []
    for label, question, route, expected in cases if args.full else cases[:1]:
        started = time.perf_counter()
        result = agent.query(question)
        data = result['result']
        passed = result['planner_source'] == 'model_validated' and result['route'] == route and result['status'] == 'ok'
        if route == 'document':
            passed = passed and data['answer_mode'] == 'model_grounded' and expected in data['answer']
        else:
            final = list(data.get('results', {}).values())[-1] if data.get('results') else {}
            observed = final.get('value') if isinstance(expected, (int,float)) else final.get('status')
            passed = passed and (abs(observed-expected)<1e-6 if isinstance(observed,(int,float)) and isinstance(expected,(int,float)) else observed==expected)
        record = {'id': label, 'question': question, 'pass': passed, 'latency_ms': round((time.perf_counter()-started)*1000,3), 'result': result, 'api_audit': dict(client.audit)}
        records.append(record)
        print(json.dumps({'id': label, 'pass': passed, 'route': result['route'], 'planner': result['planner_source'], 'status': result['status']},ensure_ascii=False),flush=True)
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'scope':'real_api_synthetic_corpus_not_public_accuracy', 'model':args.model, 'reasoning':'medium', 'python':platform.python_version(), 'cases':records, 'passed':sum(row['pass'] for row in records), 'total':len(records)}
    (ROOT/'docs/REAL_MODEL_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(row['pass'] for row in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
