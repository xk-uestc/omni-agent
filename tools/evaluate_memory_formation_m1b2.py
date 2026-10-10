"""Frozen formation ABC: C never calls MemoryStore.put; approval runs in a child CLI."""
import argparse
from dataclasses import asdict,replace
from datetime import date,datetime,timezone
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import time
from evaluate_memory_m1b1 import ROOT,digest,dump,source_hashes,timed_query,stats
from score_memory_formation_m1b2 import score,formation_score
from evaluate_context_semantics_20261009 import percentile
from backend.memory.core import MemoryCore,MemoryStore,MemoryRecord,TrustedScope,RecallContext
from backend.memory.adapter import MemoryAdapter
from backend.memory.formation import MemoryFormation
from backend.memory.extraction import digest as payload_digest
from backend.memory.admin import load
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_rich_demo_data
from backend.knowledge_store import KnowledgeStore
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore

BENCH=ROOT/'benchmarks/memory_formation_m1b2_20261010'


def cli(config,*args):
    command=[sys.executable,'-m','backend.memory.admin','--config',str(config),*args]
    env={**os.environ,'PYTHONPATH':str(ROOT/'ict-track8'),'ICT8_DISABLE_DENSE':'1'}
    start=time.perf_counter();p=subprocess.run(command,cwd=ROOT,env=env,text=True,capture_output=True,timeout=30)
    try:result=json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):result=None
    return {'command':command,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr,'result':result,'wall_ms':round((time.perf_counter()-start)*1000,3)}


def static_memory(adapter,source):
    c=source['contract'];e={'source_id':source['document_id'],'document_id':source['document_id']}
    return MemoryRecord(memory_id='static-'+source['document_id'],memory_type='business_semantics',term=c['term'],content=c['definition'],
        binding=c.get('binding'),scope=adapter.core.scope,provenance={'authority':'offline_fixture_confirmation_not_real_enterprise',
        'confirmation_id':'static-'+source['document_id'],'verification_id':'static-source-contract','evidence_id':source['document_id']},
        source_version=adapter.source_version((source['document_id'],)),valid_from=c['valid_from'],valid_to=c.get('valid_to'),
        verification_state='confirmed',created_at=c['valid_from'],updated_at=c['valid_from'])


def worker(config,question,arm):
    formation,_=load(config);adapter=formation.adapter;adapter.core.enabled=arm!='A'
    agent=OmniAgent(adapter.engine,adapter.knowledge,ConversationStore(),memory=adapter)
    r=timed_query(agent,question,session_id='independent-process-B')
    r['worker_pid']=os.getpid();r['target_history_before']=0
    print(json.dumps(r,ensure_ascii=False));return 0


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label');p.add_argument('--worker-config');p.add_argument('--question');p.add_argument('--arm')
    args=p.parse_args()
    if args.worker_config:return worker(args.worker_config,args.question,args.arm)
    if not args.label:p.error('--label required')
    frozen=json.loads((BENCH/'manifest.json').read_text())
    for f,h in frozen['files'].items():assert digest(ROOT/f)==h,f
    runtime=ROOT/'runtime'/args.label;out=ROOT/'docs/memory_rl/runs'/args.label
    if runtime.exists() or out.exists():p.error('preserve evidence: unused label required')
    runtime.mkdir(parents=True);out.mkdir(parents=True)
    os.environ['ICT8_DISABLE_DENSE']='1';os.environ['ICT8_FAST_SQL']='0'
    payload=json.loads((BENCH/'tasks.json').read_text());sources=json.loads((BENCH/'sources.json').read_text())['sources']
    db=initialize_rich_demo_data(runtime/'rich.sqlite');dbsha=digest(db);before=source_hashes();scope=TrustedScope(**payload['scope'])
    records={a:[] for a in 'ABC'};formation_records=[]
    for task in payload['tasks']:
        for arm in 'ABC':
            lifecycle_started=time.perf_counter()
            taskroot=runtime/arm/task['id'];taskroot.mkdir(parents=True)
            knowledge=KnowledgeStore(taskroot/'knowledge');selected=[sources[k] for k in task['sources']]
            for src in selected:knowledge.ingest(src['text'].encode(),document_id=src['document_id'],title=src['title'],modality='txt',filename='definition.txt')
            engine=Nl2SqlEngine(db,reference_date=date.fromisoformat(payload['reference_date']))
            store=MemoryStore(taskroot/'memory.sqlite');core=MemoryCore(store,scope=scope,enabled=arm!='A')
            adapter=MemoryAdapter(core,engine,knowledge,database_source='db',clock=lambda:payload['reference_time'],formation_enabled=arm=='C')
            agent=OmniAgent(engine,knowledge,ConversationStore(),memory=adapter)
            config=taskroot/'admin.json';dump(config,{'scope':asdict(scope),'memory_db':str(store.path),'database':str(db),
                'knowledge_root':str(knowledge.root),'database_source':'db','reference_date':payload['reference_date'],'reference_time':payload['reference_time']})
            config.chmod(0o600)
            commands=[];candidate_rows=[];formation_start=time.perf_counter();formation=MemoryFormation(adapter)
            if arm=='B' and task['action']!='no_approval':
                proposed=[static_memory(adapter,s) for s in selected if s['contract'].get('binding')]
                schema,versions,metrics,values,*_=adapter._snapshot()
                for r in proposed:
                    conflict=any(o.term==r.term and o.binding!=r.binding for o in proposed)
                    if not conflict and not core.invalid_reason(r,RecallContext(r.term,payload['reference_time'],schema,versions,metrics,values),scope):store.put(r)
            # A/B read the same material too; only C captures candidates.
            real_put=MemoryStore.put
            if arm=='C':
                def forbidden(*a,**k):raise AssertionError('C cannot inject confirmed memory with put')
                MemoryStore.put=forbidden
            try:
                prep=timed_query(agent,task['preparation_question'],session_id='formation-A-'+task['id'])
                duplicate=timed_query(agent,task['preparation_question'],session_id='formation-duplicate') if task['action']=='duplicate' else None
                if arm=='C':
                    for c in formation.candidates():
                        checked=cli(config,'validate','--candidate',c['candidate_id'],'--digest',c['digest']);commands.append(checked)
                        reviewed=None
                        if task['action']!='no_approval':
                            reviewed=cli(config,'review','--candidate',c['candidate_id'],'--digest',c['digest'],'--decision','confirm',
                                '--request-id','review-'+c['candidate_id'],'--reason','Trusted local fixture review of explicit source contract; not authenticated enterprise approval')
                            commands.append(reviewed)
                        row={'task':task['id'],'candidate':c,'validation':checked['result'] or {},'review':reviewed['result'] if reviewed else None}
                        candidate_rows.append(row);formation_records.append(row)
                formation_ms=round((time.perf_counter()-formation_start)*1000,3)
                # Rebuild Store, engine, adapter and conversations. Child CLI has
                # written the only confirmed C records; parent does not inject.
                rebuilt,_=load(config);adapter=rebuilt.adapter;adapter.core.enabled=arm!='A'
                agent=OmniAgent(adapter.engine,adapter.knowledge,ConversationStore(),memory=adapter)
                target_session='target-B-'+task['id'];history_prep=None
                if task['action']=='history_source_changed':history_prep=timed_query(agent,'2025年华东'+selected[0]['contract']['term'],session_id=target_session)
                if task['action'] in {'source_changed','history_source_changed'}:
                    src=selected[0];adapter.knowledge.ingest((src['text']+'\n来源发布了新版本，需要重新审核。').encode(),document_id=src['document_id'],title=src['title'],modality='txt',filename='definition.txt')
                if task['action']=='revoke':
                    for r in adapter.core.store.scan(scope)[0]:commands.append(cli(config,'revoke','--memory-id',r.memory_id,'--digest',payload_digest(asdict(r)),'--request-id','revoke-'+r.memory_id,'--reason','fixture business withdrawal'))
                prior=len(agent.conversations.context(target_session))
                if task['id']=='p01':
                    command=[sys.executable,__file__,'--worker-config',str(config),'--question',task['question'],'--arm',arm]
                    worker_start=time.perf_counter();process=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=30)
                    r=json.loads(process.stdout.strip().splitlines()[-1]);r['process_command']=command;r['process_stderr']=process.stderr
                    r['process_wall_ms']=round((time.perf_counter()-worker_start)*1000,3)
                else:r=timed_query(agent,task['question'],session_id=target_session)
                r.update(id=task['id'],category=task['category'],end_to_end_ms=round((time.perf_counter()-lifecycle_started)*1000,3),formation_ms=formation_ms,preparation=prep,duplicate_preparation=duplicate,
                    history_preparation=history_prep,target_history_before=prior,candidates=candidate_rows,admin_commands=commands,
                    source_versions={d['document_id']:d['sha256'] for d in adapter.knowledge.list_documents()},
                    confirmed_records=[asdict(x) for x in adapter.core.store.scan(scope)[0]],
                    C_no_put_enforced=arm=='C',store_bytes=store.path.stat().st_size)
                with store.connect() as conn:
                    r['audit']={table:[dict(zip([x[0] for x in cursor.description],row)) for row in cursor.fetchall()]
                        for table in ('memory_reviews','memory_source_events','memory_candidate_events','memory_history')
                        for cursor in [conn.execute('SELECT * FROM '+table)]}
                records[arm].append(r)
            finally:MemoryStore.put=real_put
            print(json.dumps({'id':task['id'],'arm':arm,'status':(r['response'] or {}).get('status'),'consumed':r['memory'].get('consumed',[])},ensure_ascii=False),flush=True)
    # Scorer sees Gold only after every task has executed.
    gold=json.loads((BENCH/'gold.json').read_text())
    for arm,rows in records.items():
        (out/arm).mkdir()
        for r in rows:
            passed,details=score(gold['tasks'][r['id']],r['response'],db,r['source_versions']);r['pass']=bool(passed);r['score']=details
            dump(out/arm/(r['id']+'.json'),r)
    for r in formation_records:
        key=next(k for k,s in sources.items() if s['document_id']==r['candidate']['evidence']['document_id'])
        r['score']=formation_score(gold['formation'][key],r['candidate'],r['validation'],r['review'])
    dump(out/'formation.json',formation_records)
    summaries={arm:stats(rows) for arm,rows in records.items()}
    for arm,rows in records.items():
        summaries[arm].update(positive_passed=sum(r['pass'] for r in rows if r['category']=='cross_session'),positive_total=12,
            positive_consumed=sum(bool(r['memory'].get('consumed')) for r in rows if r['category']=='cross_session'),
            formation_ms_total=round(sum(r['formation_ms'] for r in rows),3),
            end_to_end_ms_total=round(sum(r['end_to_end_ms'] for r in rows),3),
            end_to_end_p50_ms=percentile([r['end_to_end_ms'] for r in rows],.5),
            end_to_end_p95_ms=percentile([r['end_to_end_ms'] for r in rows],.95))
    comparisons=[{'id':a['id'],'A':a['pass'],'B':b['pass'],'C':c['pass'],'C_consumed':c['memory'].get('consumed',[]),
        'BC_binding_equal':[r['binding'] for r in b['confirmed_records']]==[r['binding'] for r in c['confirmed_records']],
        'BC_rows_equal':(b['response'] or {}).get('result',{}).get('rows')==(c['response'] or {}).get('result',{}).get('rows'),
        'C_history_before':c['target_history_before']} for a,b,c in zip(records['A'],records['B'],records['C'])]
    fs=[r['score'] for r in formation_records];promoted=sum(x['promoted'] for x in fs)
    report={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'created_at':datetime.now(timezone.utc).isoformat(),
        'source_before':before,'source_after':source_hashes(),'source_stable':before==source_hashes(),'database_sha256_before':dbsha,'database_sha256_after':digest(db),
        'frozen_manifest_sha256':digest(BENCH/'manifest.json'),'frozen_hashes':frozen['files'],'runner_sha256':digest(Path(__file__)),
        'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'packages':dict(sorted((d.metadata['Name'],d.version) for d in importlib.metadata.distributions())),
        'arms':summaries,'comparison':comparisons,'formation_metrics':{'candidates':len(fs),'candidate_exact_source_matches':sum(x['candidate_accurate'] for x in fs),
            'validated':sum(x['validation_passed'] for x in fs),'promoted':promoted,'wrong_promotions':sum(x['wrong_promotion'] for x in fs)},
        'configuration':{'model':None,'model_calls':0,'tokens':0,'reference_time':payload['reference_time'],'reference_date':payload['reference_date'],
            'max_items':3,'max_characters':1800,'formation':'deterministic explicit source contract; trusted local OS CLI; no parameters trained'},
        'limitations':['Exposed synthetic development fixture, not real enterprise/official data.','Static B is allowed offline provisioning; C refuses put and promotes only through child CLI.',
            'Candidate exact-source precision includes correctly retained missing bindings; it is not independent enterprise truth.','Formation time includes automatic local CLI decisions under frozen fixture policy, not real human waiting time.',
            'End-to-end starts before per-task document ingestion and ends on target return, including local review subprocesses and Store rebuild; excludes shared DB seeding, scorer, artifact serialization and human waiting.']}
    dump(out/'summary.json',report);print(json.dumps({'arms':summaries,'formation':report['formation_metrics'],'source_stable':report['source_stable']},ensure_ascii=False))


if __name__=='__main__':raise SystemExit(main())
