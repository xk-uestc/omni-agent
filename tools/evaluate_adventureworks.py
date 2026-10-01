"""Rules-only authored multi-table audit on pinned Microsoft CSV SQLite port."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from fetch_adventureworks import DEFAULT,digest


def verify_assets(directory):
    directory=Path(directory)
    manifest=json.loads((directory/'MANIFEST.json').read_text(encoding='utf-8'))
    for asset in [*manifest['assets'],manifest['database']]:
        path=(directory/asset['file']).resolve()
        if not path.is_relative_to(directory.resolve()) or digest(path)!=asset['sha256']:
            raise ValueError('Official asset integrity changed: '+asset['file'])
    return manifest


def projection(case,result):
    plan=result.get('plan',{})
    metrics=plan.get('metrics',[])
    if not metrics:
        metrics=[{'table':plan.get('metric_table') or plan.get('table'),'column':plan.get('metric_column'),
            'function':plan.get('metric_function'),'label':plan.get('metric_label')}]
    matches=[m for m in metrics if all(m.get(k)==case['metric'][k] for k in ('table','column','function'))]
    if len(matches)!=1:
        return None
    labels=[]
    for dimension in case['dimensions']:
        column=dimension['column']
        if column not in plan.get('dimensions',[]) or plan.get('dimension_tables',{}).get(column,plan.get('table'))!=dimension['table']:
            return None
        labels.append(plan.get('dimension_labels',{}).get(column,column))
    labels.append(matches[0]['label'])
    if not all(label in result.get('columns',[]) for label in labels):
        return None
    return [tuple(row[label] for label in labels) for row in result.get('rows',[])]


def same_rows(left,right):
    if left is None or len(left)!=len(right):
        return False
    ordered=lambda rows:sorted(rows,key=lambda row:repr(row[:-1]))
    for a,b in zip(ordered(left),ordered(right)):
        if len(a)!=len(b) or a[:-1]!=b[:-1]:
            return False
        if type(a[-1]) in (int,float) and type(b[-1]) in (int,float):
            if not math.isclose(a[-1],b[-1],abs_tol=.0001,rel_tol=1e-10):
                return False
        elif a[-1]!=b[-1]:
            return False
    return True


def compact_rows(rows):
    if rows is None:
        return None
    return {'count':len(rows),'sample_first_20':rows[:20],
        'ordered_rows_sha256':hashlib.sha256(json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode()).hexdigest(),
        'sampling_limit':'comparison used complete row collections; report stores bounded display only'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,default=DEFAULT)
    parser.add_argument('--output',default='docs/ADVENTUREWORKS_SQLITE_PORT_FIRST_RUN_20261001.json')
    args=parser.parse_args()
    output=(ROOT/args.output).resolve()
    if not output.is_relative_to(ROOT/'docs') or output.suffix!='.json' or output.exists():
        parser.error('New report must be JSON under docs and must not exist')
    manifest=verify_assets(args.directory)
    questions=ROOT/'benchmarks/adventureworks/questions.json'
    definitions=json.loads(questions.read_text(encoding='utf-8'))
    frozen=json.loads((ROOT/'benchmarks/adventureworks/MANIFEST.json').read_text(encoding='utf-8'))
    if digest(questions)!=frozen['questions_sha256']:
        raise ValueError('Frozen questions changed')
    database=(args.directory/manifest['database']['file']).resolve()
    from backend.nl2sql.engine import Nl2SqlEngine
    engine=Nl2SqlEngine(database,metric_catalog_path=args.directory/'no_domain_catalog.json')
    assert engine.metric_catalog is None
    records=[]
    started=time.perf_counter()
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as connection:
        for case in definitions['cases']:
            expected=connection.execute(case['gold_sql']).fetchall()
            query_started=time.perf_counter()
            try:
                result=engine.answer(case['question']).to_dict()
                error=None
            except Exception as exc:
                result={'status':'error','error_type':type(exc).__name__}
                error=type(exc).__name__
            observed=projection(case,result)
            checks={'status':result.get('status')=='ok','physical_projection':observed is not None,
                'complete_execution_result':same_rows(observed,expected),'actual_join':bool(result.get('plan',{}).get('join_path')) and ' JOIN ' in (result.get('sql') or '').upper(),
                'no_api_or_domain_catalog':result.get('plan',{}).get('planner_source')=='rules'}
            records.append({**case,'pass':all(checks.values()),'checks':checks,'expected':compact_rows(expected),
                'observed':compact_rows(observed),'result':result,'error_type':error,'latency_ms':round((time.perf_counter()-query_started)*1000,3)})
            print(json.dumps({'id':case['id'],'pass':records[-1]['pass'],'checks':checks,'status':result.get('status'),
                'clarification':result.get('clarification')},ensure_ascii=False),flush=True)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':definitions['scope'],'model':None,'model_api_calls':0,
        'few_shot_examples':0,'domain_alias_rules':0,'dataset_manifest_sha256':digest(args.directory/'MANIFEST.json'),
        'input_frozen':frozen,'source_commit':manifest['source_commit'],'dataset_statistics':manifest['statistics'],
        'database_sha256':manifest['database']['sha256'],'passed':sum(r['pass'] for r in records),'total':len(records),
        'wall_ms':round((time.perf_counter()-started)*1000,3),'cases':records,'limitations':manifest['limitations'],
        'native_postgresql_support':False,'official_benchmark_score':False,
        'audit_limits':['Questions authored and frozen before first execution; not official benchmark gold or blind holdout.',
            'Exact original Decimal totals retained in manifest; SQL result comparison tolerance 0.0001 and relative 1e-10 acknowledges SQLite NUMERIC floating representation.',
            'Unordered row multiset comparison does not score ranking presentation order; full collections checked despite bounded report samples.',
            'Default engine safety row limits remain; truncated large groups count as failed complete results.']}
    report['implementation_file_sha256'] = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
        'tools/evaluate_adventureworks.py', 'tools/fetch_adventureworks.py',
        'ict-track8/backend/nl2sql/engine.py', 'ict-track8/backend/nl2sql/schema.py',
        'ict-track8/backend/nl2sql/planner.py')}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'total':report['total']},ensure_ascii=False))
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
