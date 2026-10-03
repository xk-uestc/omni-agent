"""Three authored unfamiliar-domain five-turn sessions, actual model + SQLite.

Reference SQL belongs only to the scorer, never the model context. This is a
development probe, not a public benchmark or independent unseen test.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from model_runtime import enable_local_model, local_model_headers
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
from backend.omni_agent import OmniAgent
from backend.responses_client import StructuredResponses
from backend.session import ConversationStore


def hashes():
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}


def scenario(domain):
    table,column,metric = {'clinic':('patients','patient_id','患者数'),
                            'logistics':('shipments','shipping_cost','配送成本合计'),
                            'finance':('payments','amount','付款金额合计')}[domain]
    expression = 'COUNT(DISTINCT s.patient_id)' if domain=='clinic' else f'SUM(s.{column})'
    statements = [f'CREATE TABLE {table}(patient_id INTEGER PRIMARY KEY, city_id INTEGER REFERENCES cities(id),'
                  +(f'{column} REAL);' if domain!='clinic' else 'age REAL);'),
                  f'INSERT INTO {table} VALUES(1,1,10),(2,2,30),(3,3,90);']
    if domain=='finance':statements += ['CREATE TABLE loans(id INTEGER PRIMARY KEY, amount REAL);',
                                      'INSERT INTO loans VALUES(1,9999);']
    questions=['中国'+metric+'，按城市分组','那日本呢','换成按国家分组',
               '那排除北城呢','中国'+metric]
    reference=[f"SELECT c.name,{expression} FROM {table} s JOIN cities c ON s.city_id=c.id WHERE c.country='中国' GROUP BY c.name",
               f"SELECT c.name,{expression} FROM {table} s JOIN cities c ON s.city_id=c.id WHERE c.country='日本' GROUP BY c.name",
               f"SELECT c.country,{expression} FROM {table} s JOIN cities c ON s.city_id=c.id WHERE c.country='日本' GROUP BY c.country",
               None, f"SELECT {expression} FROM {table} s JOIN cities c ON s.city_id=c.id WHERE c.country='中国'"]
    return table,statements,questions,reference


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-model',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().parent!=(ROOT/'docs').resolve() or args.output.exists():
        parser.error('New report directly in docs required')
    import os
    config=enable_local_model() if args.with_model else None
    started=time.monotonic();before=hashes()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory=ROOT/'runtime'/('round7-sessions-'+stamp)
    directory.mkdir(parents=True)
    stop=threading.Event()
    def run(domain):
        table,statements,questions,reference=scenario(domain)
        work=directory/domain;work.mkdir()
        database=work/'source.sqlite'
        with sqlite3.connect(database) as db:
            db.executescript('PRAGMA foreign_keys=ON; CREATE TABLE cities(id INTEGER PRIMARY KEY, name TEXT, country TEXT);'
                            "INSERT INTO cities VALUES(1,'北城','中国'),(2,'南城','中国'),(3,'西城','日本');"+''.join(statements))
        digest=hashlib.sha256(database.read_bytes()).hexdigest()
        provider=ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],
                    model='gpt-6-luna',reasoning_effort='medium',http_headers=local_model_headers()) if config else None
        engine=Nl2SqlEngine(database,model_plan_provider=provider,metric_catalog_path=work/'no-catalog.json')
        client=StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],
                    model='gpt-6-luna',reasoning='medium',http_headers=local_model_headers()) if config else None
        if provider:provider.catalog=None;provider.reference_date=engine.reference_date
        sessions=ConversationStore(storage_path=work/'sessions.sqlite')
        agent=OmniAgent(engine,KnowledgeStore(work/'knowledge'),sessions,client)
        cases=[]
        for index,(question,sql) in enumerate(zip(questions,reference),1):
            if stop.is_set():break
            if hashes()!=before:raise RuntimeError('implementation_changed')
            if client:client.reset_audit();provider.reset_audit()
            wall=time.monotonic()
            result=agent.query(question,session_id=domain)
            inner=result.get('result',{})
            expected=[]
            if sql:
                with sqlite3.connect(database) as db:expected=[list(row) for row in db.execute(sql)]
            actual=[[row[col] for col in inner.get('columns',[])] for row in inner.get('rows',[])]
            bag=lambda rows:sorted(json.dumps(row,ensure_ascii=False) for row in rows)
            replay=[]
            if sql and inner.get('sql'):
                with sqlite3.connect(database) as db:
                    db.execute('PRAGMA query_only=ON')
                    replay=[list(row) for row in db.execute(inner['sql'],inner.get('parameters',[]))]
            semantically_correct=(result['status']=='clarification' if sql is None
                                  else result['status']=='ok' and bag(actual)==bag(expected)==bag(replay))
            audits=([{**entry,'component':name} for name,api in [('router',client),('sql',provider)]
                     for entry in api.audit_history] if client else [])
            model_ok=bool(audits) and all(entry.get('status')=='completed' and entry.get('model_verified') is True
                    and entry.get('model')=='gpt-6-luna' and entry.get('reasoning')=='medium' for entry in audits)
            passed=semantically_correct and (not config or sql is None or model_ok
                                            and result.get('planner_source')=='model_validated'
                                            and inner.get('plan',{}).get('planner_source')=='model_validated')
            cases.append({'turn':index,'question':question,'status':result['status'],
                          'semantic_pass':semantically_correct,'pass':passed,'actual_rows':actual,
                          'reference_rows_scoring_only':expected,'effective_question':result['effective_question'],
                          'context_resolution':result.get('context_resolution'),
                          'clarification_code':inner.get('clarification_code'), 'api_audits':audits,
                          'wall_ms':round((time.monotonic()-wall)*1000,3)})
            print(json.dumps({'domain':domain,'turn':index,'status':result['status'],'pass':passed}),flush=True)
            if any(entry.get('http_status') in {401,403} for entry in audits):stop.set();break
        reopened=ConversationStore(storage_path=work/'sessions.sqlite')
        return {'domain':domain,'cases':cases,'all_five_pass':len(cases)==5 and all(c['pass'] for c in cases),
                'persisted_turns_after_restart':len(reopened.context(domain)),
                'source_stable':digest==hashlib.sha256(database.read_bytes()).hexdigest()}
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(run,('clinic','logistics','finance')))
    after=hashes()
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'authored_cross_domain_development_probe_not_official_benchmark',
            'model_called':bool(config),'model':'gpt-6-luna' if config else None,'reasoning':'medium' if config else None,
            'planned_turns':15,'executed_turns':sum(len(s['cases']) for s in results),
            'passed_turns':sum(c['pass'] for s in results for c in s['cases']),
            'complete_five_turn_sessions':sum(s['all_five_pass'] for s in results),'sessions':results,
            'reference_used_as_model_input':False,'implementation_file_sha256':before,
            'implementation_file_sha256_end':after,'implementation_stable':before==after,
            'wall_seconds':round(time.monotonic()-started,3)}
    with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:report[k] for k in ('executed_turns','passed_turns','complete_five_turn_sessions','implementation_stable')}))
    return 0 if report['passed_turns']==15 and report['implementation_stable'] else 1


if __name__=='__main__':raise SystemExit(main())
