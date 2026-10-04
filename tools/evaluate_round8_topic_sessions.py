"""Actual five-turn topic/reset/recovery probes, independent scoring SQL."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from model_runtime import enable_local_model,local_model_headers
from backend.responses_client import StructuredResponses
from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.security import SqlSafetyError
from backend.knowledge_store import KnowledgeStore
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


class DiagnosticEngine(Nl2SqlEngine):
    """Preserve execution guards; report only fixed non-sensitive codes."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.execution_rejections = []

    def answer(self, *args, **kwargs):
        try:
            return super().answer(*args, **kwargs)
        except SqlSafetyError as exc:
            text = str(exc)
            self.execution_rejections.append({
                'type': type(exc).__name__,
                'code': text if re.fullmatch(r'[a-z][a-z0-9_]*(?::[A-Z_]{1,40})?', text)
                    else 'sql_rejection_detail_not_publishable',
            })
            raise


def hashes():
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}


def build_agent(work, database):
    provider=ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],model='gpt-6-luna',reasoning_effort='medium',http_headers=local_model_headers())
    client=StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],model='gpt-6-luna',reasoning='medium',http_headers=local_model_headers())
    engine=DiagnosticEngine(database,model_plan_provider=provider,metric_catalog_path=work/'none.json')
    provider.catalog=None;provider.reference_date=engine.reference_date
    agent=OmniAgent(engine,KnowledgeStore(work/'knowledge'),ConversationStore(storage_path=work/'sessions.sqlite'),client)
    return agent, engine, provider, client


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--recreate-before-turn',type=int,nargs='+',choices=(2,3,4,5),default=[],
        help='Rebuild all agent/engine/provider instances from persistent state before these turns; not an OS-process restart')
    args=parser.parse_args()
    if args.output.resolve().parent!=(ROOT/'docs').resolve() or args.output.exists():parser.error('New docs report required')
    enable_local_model();before=hashes()
    directory=ROOT/'runtime'/('round8-topic-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.mkdir(parents=True);sessions=[]
    for domain,table,metric in [('advertising','campaigns','spend'),('observatory','observations','exposure')]:
        work=directory/domain;work.mkdir();database=work/'source.sqlite'
        with sqlite3.connect(database) as db:
            db.executescript(f'''CREATE TABLE countries(id INTEGER PRIMARY KEY,name TEXT);
            CREATE TABLE {table}(id INTEGER PRIMARY KEY,country_id INTEGER REFERENCES countries(id),
                event_date TEXT,{metric} REAL);
            CREATE TABLE contracts(id INTEGER PRIMARY KEY,country_id INTEGER REFERENCES countries(id),
                contract_date TEXT,amount REAL);
            INSERT INTO countries VALUES(1,'中国'),(2,'日本');
            INSERT INTO {table} VALUES(1,1,'2025-02-03',12),(2,1,'2025-03-03',7),
                (3,2,'2025-02-03',31),(4,1,'2024-02-03',99);
            INSERT INTO contracts VALUES(1,1,'2024-02-03',80),(2,2,'2024-03-03',1000),
                (3,1,'2025-02-03',130);''')
        source_sha=hashlib.sha256(database.read_bytes()).hexdigest()
        scope=f'以{table}.event_date为准，统计2025年{table}.{metric}合计，只筛选countries.name为中国，关联{table}.country_id=countries.id。'
        topic='换个主题，以contracts.contract_date为准，统计2024年contracts.amount合计，只筛选countries.name为中国，关联contracts.country_id=countries.id。'
        recovery='以contracts.contract_date为准，统计2025年contracts.amount合计，只筛选countries.name为日本，关联contracts.country_id=countries.id。'
        cases=[(scope,f"SELECT SUM(x.{metric}) FROM {table} x JOIN countries c ON x.country_id=c.id WHERE c.name='中国' AND x.event_date>='2025-01-01' AND x.event_date<'2026-01-01'"),
            ('那日本呢',f"SELECT SUM(x.{metric}) FROM {table} x JOIN countries c ON x.country_id=c.id WHERE c.name='日本' AND x.event_date>='2025-01-01' AND x.event_date<'2026-01-01'"),
            (topic,"SELECT SUM(x.amount) FROM contracts x JOIN countries c ON x.country_id=c.id WHERE c.name='中国' AND x.contract_date>='2024-01-01' AND x.contract_date<'2025-01-01'"),
            ('那排除未说明的特殊合同呢',None),
            (recovery,"SELECT SUM(x.amount) FROM contracts x JOIN countries c ON x.country_id=c.id WHERE c.name='日本' AND x.contract_date>='2025-01-01' AND x.contract_date<'2026-01-01'")]
        agent,engine,provider,client=build_agent(work,database)
        observations=[];recreations=[]
        for turn,(question,reference) in enumerate(cases,1):
            if hashes()!=before:raise ValueError('source_changed')
            if turn in args.recreate_before_turn:
                saved=agent.conversations.context(domain)
                old_agent=agent
                agent,engine,provider,client=build_agent(work,database)
                restored_context=agent.conversations.context(domain)
                recreation={'before_turn':turn,'kind':'fresh_objects_from_persistent_storage_not_os_process_restart',
                    'context_turns':len(restored_context),'context_equal':saved==restored_context,
                    'new_agent_instance':agent is not old_agent}
                recreations.append(recreation)
                if not recreation['context_equal'] or len(restored_context)!=turn-1:
                    raise ValueError('recreated_session_context_mismatch')
            client.reset_audit();provider.reset_audit();engine.execution_rejections.clear()
            response=agent.query(question,session_id=domain);result=response.get('result',{})
            actual=[[r[col] for col in result.get('columns',[])] for r in result.get('rows',[])]
            with sqlite3.connect(database) as db:
                expected=[list(r) for r in db.execute(reference)] if reference else []
                replay=[list(r) for r in db.execute(result['sql'],result.get('parameters',[]))] if result.get('sql') else []
            audits=[{**a,'component':name} for name,api in [('router',client),('sql',provider)] for a in api.audit_history]
            passed=(response['status']=='clarification' if reference is None else
                response['status']=='ok' and actual==expected==replay and bool(audits)
                and all(a.get('status')=='completed' and a.get('model_verified') is True for a in audits))
            observations.append({'turn':turn,'question':question,'pass':passed,'response':response,
                'reference_rows_scoring_only':expected,'api_audits':audits,
                'execution_rejections':list(engine.execution_rejections)})
            print(json.dumps({'domain':domain,'turn':turn,'pass':passed,'status':response['status']}),flush=True)
            if any(a.get('http_status') in (401,403) for a in audits):break
        restored=ConversationStore(storage_path=work/'sessions.sqlite')
        sessions.append({'domain':domain,'cases':observations,
            'recreations':recreations,
            'persisted_turns_after_restart':len(restored.context(domain)),
            'source_stable':source_sha==hashlib.sha256(database.read_bytes()).hexdigest()})
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'authored_topic_switch_and_failure_recovery_not_official_accuracy',
        'model':'gpt-6-luna','reasoning':'medium','reference_used_as_model_input':False,
        'planned_turns':10,'executed_turns':sum(len(s['cases']) for s in sessions),
        'recreate_before_turn':sorted(set(args.recreate_before_turn)),
        'passed_turns':sum(c['pass'] for s in sessions for c in s['cases']),
        'complete_five_turn_sessions':sum(len(s['cases'])==5 and all(c['pass'] for c in s['cases']) for s in sessions),
        'implementation_file_sha256':before,'implementation_file_sha256_end':hashes(),
        'implementation_stable':before==hashes(),'sessions':sessions}
    with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:report[k] for k in ('passed_turns','executed_turns','complete_five_turn_sessions','implementation_stable')}))


if __name__=='__main__':main()
