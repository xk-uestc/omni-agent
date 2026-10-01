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


def assess(case, result, expected, audits, *, model=False, dropped=0):
    columns = case['columns']
    field_ok = all(column in result.get('columns', []) for column in columns)
    observed = [tuple(row[column] for column in columns) for row in result.get('rows', [])] if field_ok else []
    checks = {'status': result.get('status') == 'ok', 'projection_labels': field_ok,
              'execution_result': field_ok and normalized(observed) == normalized(expected)}
    if model:
        checks.update({'actual_model_plan': result.get('plan', {}).get('planner_source') == 'model_validated',
            'completed_verified_api': bool(audits) and any(a.get('status') == 'completed'
                and a.get('model_verified') is True and a.get('model') == 'gpt-6-luna' for a in audits),
            'only_allowed_model': bool(audits) and all(a.get('model') == 'gpt-6-luna' for a in audits),
            'complete_call_history': dropped == 0})
    return checks


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',action='store_true')
    parser.add_argument('--output', help='New JSON report under docs; existing evidence is never overwritten')
    parser.add_argument('--preview', action='store_true')
    args=parser.parse_args()
    output = (ROOT / (args.output or ('docs/CHINOOK_MODEL_REPORT.json' if args.model else 'docs/CHINOOK_RULES_REPORT.json'))).resolve()
    if not output.is_relative_to((ROOT/'docs').resolve()) or output.suffix.lower() != '.json':
        parser.error('报告必须是docs中的JSON文件')
    if args.output and output.exists():
        parser.error('不能覆盖历史报告')
    path=ROOT/'benchmarks/chinook/Chinook.sqlite'
    questions_path=ROOT/'benchmarks/chinook/questions.json'
    cases=json.loads(questions_path.read_text(encoding='utf-8'))['cases']
    frozen={'database_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'questions_sha256':hashlib.sha256(questions_path.read_bytes()).hexdigest(),
            'case_ids':[row['id'] for row in cases]}
    if args.preview:
        print(json.dumps({'scope':'official_recommended_database_with_manual_development_questions',
            'model':'gpt-6-luna' if args.model else None, 'reasoning':'medium' if args.model else None,
            'input_frozen':frozen, 'total':len(cases), 'model_called':False}, ensure_ascii=False))
        return 0
    from backend.nl2sql.engine import Nl2SqlEngine
    provider=None
    if args.model:
        import os
        from model_runtime import enable_local_model
        from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
        enable_local_model()
        from model_runtime import local_model_headers
        provider=ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],model='gpt-6-luna',reasoning_effort='medium',max_retries=0,http_headers=local_model_headers())
    engine=Nl2SqlEngine(path,model_plan_provider=provider,metric_catalog_path=ROOT/'benchmarks/chinook/no_catalog.json')
    records=[]
    stopped=None
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as connection:
        for case in cases:
            if provider:
                provider.reset_audit()
            expected=connection.execute(case['gold_sql']).fetchall()
            started=time.perf_counter()
            try:
                result=engine.answer(case['question']).to_dict()
            except (ValueError,RuntimeError,sqlite3.Error) as exc:
                result={'status':'error','error_type':type(exc).__name__}
            audits=list(provider.audit_history) if provider else []
            dropped=provider.audit_dropped_count if provider else 0
            checks=assess(case,result,expected,audits,model=args.model,dropped=dropped)
            passed=all(checks.values())
            records.append({**case,'pass':passed,'checks':checks,'latency_ms':round((time.perf_counter()-started)*1000,3),'expected':expected,'result':result,
                'api_audits':audits,'audit_dropped_count':dropped})
            print(json.dumps({'id':case['id'],'pass':passed,'status':result['status'],'planner':result.get('plan',{}).get('planner_source')},ensure_ascii=False),flush=True)
            if any(a.get('http_status') in {401,403} for a in audits):
                stopped='authentication_or_access_rejected'
                break
    from evaluate_model import summarise_audits
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'public_chinook_manual_queries_not_standard_test_set',
            'db_sha256':frozen['database_sha256'],'input_frozen_before_api':frozen,'model':'gpt-6-luna' if args.model else None,
            'reasoning':'medium' if args.model else None, 'stopped':stopped,
            'passed':sum(c['pass'] for c in records),'total':len(records),'planned_total':len(cases),'cases':records,
            'limits':['Official-recommended original database; questions are authored development queries, not official gold questions.',
                'Projection labels are checked strictly; a label mismatch is separately reported and does not prove numeric SQL error.',
                'Unordered row multiset comparison does not measure ranking-order fidelity or a public benchmark leaderboard.'],
            'api_summary':summarise_audits([a for c in records for a in c['api_audits']],sum(c['audit_dropped_count'] for c in records))}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'total':report['total']},ensure_ascii=False))
    return 0 if report['passed']==report['planned_total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
