"""Strict projection-labelled semantic evaluation on the real public Chinook DB."""
import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
sys.path.insert(0,str(ROOT/'ict-track8/eval'))


def normalized(rows):
    return sorted([tuple(round(v,6) if type(v) in (int,float) else v for v in row) for row in rows],key=repr)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',action='store_true')
    args=parser.parse_args()
    from backend.nl2sql.engine import Nl2SqlEngine
    path=ROOT/'benchmarks/chinook/Chinook.sqlite'
    provider=None
    if args.model:
        import os
        from model_runtime import enable_local_model
        from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
        enable_local_model()
        from model_runtime import local_model_headers
        provider=ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],model='gpt-6-luna',max_retries=0,http_headers=local_model_headers())
    engine=Nl2SqlEngine(path,model_plan_provider=provider,metric_catalog_path=ROOT/'benchmarks/chinook/no_catalog.json')
    records=[]
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as connection:
        for case in json.loads((ROOT/'benchmarks/chinook/questions.json').read_text(encoding='utf-8'))['cases']:
            expected=connection.execute(case['gold_sql']).fetchall()
            started=time.perf_counter()
            try:
                result=engine.answer(case['question']).to_dict()
                columns=case['columns']
                field_ok=all(column in result['columns'] for column in columns)
                observed=[tuple(row[column] for column in columns) for row in result['rows']] if field_ok else []
                passed=result['status']=='ok' and field_ok and normalized(observed)==normalized(expected)
            except (ValueError,RuntimeError,sqlite3.Error) as exc:
                result={'status':'error','error_type':type(exc).__name__}
                passed=False
            records.append({**case,'pass':passed,'latency_ms':round((time.perf_counter()-started)*1000,3),'expected':expected,'result':result})
            print(json.dumps({'id':case['id'],'pass':passed,'status':result['status'],'planner':result.get('plan',{}).get('planner_source')},ensure_ascii=False),flush=True)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'public_chinook_manual_queries_not_standard_test_set',
            'db_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'model':'gpt-6-luna' if args.model else None,
            'passed':sum(c['pass'] for c in records),'total':len(records),'cases':records}
    output='CHINOOK_MODEL_REPORT.json' if args.model else 'CHINOOK_RULES_REPORT.json'
    (ROOT/'docs'/output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'total':report['total']},ensure_ascii=False))
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
