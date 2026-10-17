"""M1-B1 real A/B execution. Frozen scorers/inputs remain unmodified.

The fixture provisioner is offline test code, never imported by the service.
It knows candidate definitions but never Gold SQL/answers. Only score() sees Gold.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
from io import BytesIO
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
import time
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

from evaluate_memory_sensitive_m1a import ROOT, BENCH, digest, dump
from evaluate_context_semantics_20261009 import build_fixtures, score as context_score, percentile
from score_memory_sensitive_m1a import score
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_rich_demo_data
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore
from backend.memory.core import MemoryCore, MemoryStore, MemoryRecord, TrustedScope
from backend.memory.adapter import MemoryAdapter


def documents(root,sources,variant):
    from openpyxl import Workbook
    store=KnowledgeStore(root)
    store.ingest(sources['terms_changed' if variant=='terms_changed' else 'terms'].encode(),
        document_id='terms',title='业务术语确认卡',modality='txt',filename='terms.txt')
    store.ingest(sources['policy'].encode(),document_id='policy',title='指标与计划',modality='txt',filename='policy.txt')
    workbook=Workbook()
    for row in sources['target_rows_changed' if variant=='targets_changed' else 'target_rows']:workbook.active.append(row)
    raw=BytesIO();workbook.save(raw);stable=BytesIO()
    with ZipFile(raw) as source_zip,ZipFile(stable,'w',ZIP_DEFLATED) as dest:
        for name in source_zip.namelist():
            item=ZipInfo(name,(2026,10,10,0,0,0));item.compress_type=ZIP_DEFLATED
            dest.writestr(item,source_zip.read(name))
    store.ingest(stable.getvalue(),document_id='targets',title='区域计划',modality='xlsx',filename='targets.xlsx')
    return store


def provision(store,candidates,version,*,scope_override=None):
    """Explicit developer fixture confirmations, not authenticated user claims.

    Governance adversaries intentionally carry fixture receipts for filter tests.
    No preparation answer is used as a binding. Task experience is archived as
    unsupported, never promoted by this experiment or supplied to a planner.
    """
    records=[]
    for c in candidates:
        binding=dict(c.get('binding',{}))
        if 'channel' in binding:binding['filters']=[{'column':'channel','value':binding.pop('channel')}]
        semantic=c['type']=='business_semantics'
        r=MemoryRecord(memory_id=c['id'],memory_type=c['type'],term=c['content'].split('指',1)[0] if semantic else '跨源方法',
            content=c['content'],binding=binding,scope=scope_override or TrustedScope(**c['scope']),
            provenance={'authority':c['authority'],'confirmation_id':c['evidence_id'],'evidence_id':c['evidence_id'],
                'verification_id':'frozen-fixture-contract-not-real-user' if semantic else 'unsupported-not-promoted'},
            source_version=version,valid_from=c['valid_from'],valid_to=c['valid_to'],
            verification_state='confirmed' if semantic and c['verification_state']=='confirmed_fixture_contract' else 'candidate',
            created_at=c['valid_from'],updated_at=c['valid_from'])
        store.put(r);records.append(asdict(r))
    return records


def source_hashes():
    return {str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}


def timed_query(agent,question,**kwargs):
    trace=[];start=time.perf_counter()
    try:
        response=agent.query(question,trace_callback=trace.append,**kwargs)
        error=None
    except Exception as exc:
        response=None;error={'type':type(exc).__name__,'message':str(exc)[:300]}
    wall=round((time.perf_counter()-start)*1000,3)
    memory=(response or {}).get('memory',{})
    return {'response':response,'error':error,'execution_status':'error' if error else 'executed',
        'wall_ms':wall,'trace':trace,'memory':memory,'model_calls':0,'input_tokens':0,'output_tokens':0,
        'tokens_basis':'no model instantiated',
        'business_tool_calls':sum(e.get('status')=='running' for e in trace),
        'memory_operations':sum(e.get('tool') in {'memory.recall','memory.observe'} for e in trace)}


def stats(records):
    return {'total':len(records),'passed':sum(r['pass'] for r in records),
        'model_calls':sum(r['model_calls'] for r in records),'input_tokens':0,'output_tokens':0,
        'business_tool_calls':sum(r['business_tool_calls'] for r in records),
        'memory_operations':sum(r['memory_operations'] for r in records),
        'wall_ms_total':round(sum(r['wall_ms'] for r in records),3),
        'wall_ms_median':round(statistics.median(r['wall_ms'] for r in records),3),
        'wall_ms_p95':percentile([r['wall_ms'] for r in records],.95),
        'recall_ms_total':round(sum(r['memory'].get('recall_ms',0) for r in records),3),
        'observe_ms_total':round(sum(r['memory'].get('observe_ms',0) for r in records),3)}


def run_sensitive(output,runtime,paths,payload,sources,pool):
    database,aliases=paths['mixed']
    golds=json.loads((BENCH/'gold.json').read_text())['tasks']
    stores={v:documents(runtime/'documents'/v,sources,v) for v in ['original','terms_changed','targets_changed']}
    doc_versions={v:{d['document_id']:d['sha256'] for d in k.list_documents()} for v,k in stores.items()}
    def engine():return Nl2SqlEngine(database,aliases_path=aliases,reference_date=date.fromisoformat(payload['reference_date']),
        metric_catalog_path=runtime/'no-catalog.json')
    scope=TrustedScope(**payload['tasks'][0]['scope'])
    author=MemoryAdapter(MemoryCore(None,scope=scope,enabled=True),engine(),stores['original'],database_source='mixed')
    version=author.source_version(('terms',))
    records={arm:[] for arm in ('A','B')};all_candidates={c['id']:c for c in pool['candidates']}
    candidate_evidence=[]
    # Each task defines an isolated candidate availability scenario, identically
    # provisioned in A and B. Original input questions and session IDs unchanged.
    for task in payload['tasks']:
        for arm in ('A','B'):
            taskroot=runtime/arm/task['id'];taskroot.mkdir(parents=True)
            memory_store=MemoryStore(taskroot/'memory.sqlite')
            supplied=provision(memory_store,[all_candidates[k] for k in task['candidate_ids']],version)
            core=MemoryCore(memory_store,scope=TrustedScope(**task['scope']),enabled=arm=='B')
            adapter=MemoryAdapter(core,engine(),stores[task['source_variant']],database_source='mixed',clock=lambda:payload['reference_time'])
            agent=OmniAgent(adapter.engine,adapter.knowledge,ConversationStore(),memory=adapter)
            prep=[timed_query(agent,q,session_id=task['preparation_session']) for q in task['preparation_questions']]
            # Reopen durable store across the preparation/target boundary.
            core.store=MemoryStore(memory_store.path)
            before_bytes=memory_store.path.stat().st_size
            prior=len(agent.conversations.context(task['target_session']))
            assert task['same_session_control'] or prior==0
            r=timed_query(agent,task['question'],session_id=task['target_session'])
            passed,detail=score(golds[task['id']],r['response'] or {},database,doc_versions[task['source_variant']])
            consumed=r['memory'].get('consumed',[])
            r.update(id=task['id'],category=task['category'],pass_=bool(passed),score=detail,
                target_history_before=prior,target_session=task['target_session'],preparation_session=task['preparation_session'],
                preparation=prep,candidate_ids=task['candidate_ids'],source_versions=doc_versions[task['source_variant']],
                supplemental_consumption_verified=(bool(passed) and bool(consumed)) if task['category']=='benefit_semantics' else None,
                memory_store_bytes_before=before_bytes,memory_store_bytes_after=memory_store.path.stat().st_size,
                archived_events=memory_store.events())
            r['pass']=r.pop('pass_');records[arm].append(r)
            (output/arm).mkdir(exist_ok=True);dump(output/arm/(task['id']+'.json'),r)
            candidate_evidence.append({'arm':arm,'task':task['id'],'records':supplied})
            print(json.dumps({'suite':'sensitive16','arm':arm,'id':task['id'],'pass':passed,'consumed':consumed},ensure_ascii=False),flush=True)
    dump(output/'candidate-evidence.json',candidate_evidence)
    old=json.loads((ROOT/'docs/memory_rl/runs/m1a-memory-sensitive-no-memory-20261010/summary.json').read_text())
    original={r['id']:r for r in old['records']}
    comparison=[{'id':a['id'],'M1A':original[a['id']]['pass'],'A':a['pass'],'B':b['pass'],
        'A_status':(a['response'] or {}).get('status'),'B_status':(b['response'] or {}).get('status'),
        'selected':b['memory'].get('selected',[]),'consumed':b['memory'].get('consumed',[]),
        'decisions':b['memory'].get('decisions',[]),'target_history_before':b['target_history_before']} for a,b in zip(records['A'],records['B'])]
    report={'arms':{arm:stats(rs) for arm,rs in records.items()},'comparison':comparison,
        'semantics':{arm:{'passed':sum(r['pass'] for r in rs if r['category']=='benefit_semantics'),
            'consumption_verified':sum(bool(r['supplemental_consumption_verified']) for r in rs),'total':4} for arm,rs in records.items()},
        'same_session_control':'h01','independent_session_control':'h02','document_versions':doc_versions,
        'memory_store_total_bytes':{arm:sum(r['memory_store_bytes_after'] for r in rs) for arm,rs in records.items()},
        'memory_store_growth_target_bytes':{arm:sum(r['memory_store_bytes_after']-r['memory_store_bytes_before'] for r in rs) for arm,rs in records.items()},
        'C':{'status':'not_run','reason':'One legal candidate per semantic term; unsupported task_experience; no meaningful independent Oracle selection experiment.'}}
    dump(output/'sensitive-summary.json',report)
    return report


def run_context(output,runtime,paths,payload,sources,pool):
    context_bench=ROOT/'benchmarks/context_semantics_20261009'
    cases=json.loads((context_bench/'cases.json').read_text())['cases']
    baseline=json.loads((ROOT/'docs/memory_rl/runs/context-no-memory-20261010.json').read_text())
    assert digest(context_bench/'cases.json')==baseline['input_sha256']
    original={r['id']:r for r in baseline['cases']}
    scope=TrustedScope('synthetic-m1a','commerce-dev',('mixed','policy','targets','terms'))
    result={};storage={}
    for arm in ('A','B'):
        agents={};memories=[]
        for fixture,(database,aliases) in paths.items():
            # Match M1-A knowledge inventory (empty). A separate provisioning
            # source supplies schema-only test memories, which reference no docs.
            engine=Nl2SqlEngine(database,aliases_path=aliases,reference_date=date(2026,10,9),
                metric_catalog_path=context_bench/'demo_metric_catalog-frozen.json' if fixture=='rich' else runtime/'no-catalog.json',
                result_artifact_dir=runtime/'context-results'/arm/fixture)
            knowledge=KnowledgeStore(runtime/'context-knowledge'/arm/fixture)
            ms=MemoryStore(runtime/'context-memory'/arm/(fixture+'.sqlite'));memories.append(ms)
            core=MemoryCore(ms,scope=scope,enabled=arm=='B')
            adapter=MemoryAdapter(core,engine,knowledge,database_source='mixed',clock=lambda:payload['reference_time'])
            # Provisioning API is trusted offline only. Enable snapshot utility
            # identically in both groups without executing recall for A.
            author=MemoryAdapter(MemoryCore(None,scope=scope,enabled=True),engine,knowledge,database_source='mixed')
            version=author.source_version(())
            provision(ms,[c for c in pool['candidates'] if c['id'] in {'cost','online','margin'}],version,scope_override=scope)
            agents[fixture]=OmniAgent(engine,knowledge,ConversationStore(),memory=adapter)
            engine.schema()
        before=sum(m.path.stat().st_size for m in memories)
        rows=[]
        for i,case in enumerate(cases):
            r=timed_query(agents[case['fixture']],case['question'],session_id=case['session'],reset_context=case['reset_context'])
            passed,evidence=context_score(case,r['response'] or {},paths[case['fixture']][0])
            r.update(id=case['id'],category=case['category'],fixture=case['fixture'],score=evidence)
            r['pass']=bool(passed);rows.append(r)
            if i%25==0:print(json.dumps({'suite':'context185','arm':arm,'done':i+1,'passed':sum(x['pass'] for x in rows)}),flush=True)
        result[arm]=rows;storage[arm]={'before_bytes':before,'after_bytes':sum(m.path.stat().st_size for m in memories),
            'event_count':sum(len(m.events()) for m in memories)}
        dump(output/('context-'+arm+'.json'),{'cases':rows,'summary':stats(rows),'storage':storage[arm]})
    comparison=[{'id':a['id'],'M1A':original[a['id']]['pass'],'A':a['pass'],'B':b['pass'],
        'off_on_rows_equal':(a['response'] or {}).get('result',{}).get('rows')==(b['response'] or {}).get('result',{}).get('rows'),
        'off_on_sql_equal':(a['response'] or {}).get('result',{}).get('sql')==(b['response'] or {}).get('result',{}).get('sql'),
        'off_on_status_equal':(a['response'] or {}).get('status')==(b['response'] or {}).get('status'),
        'selected':b['memory'].get('selected',[]),'consumed':b['memory'].get('consumed',[])} for a,b in zip(result['A'],result['B'])]
    report={'arms':{arm:stats(rs) for arm,rs in result.items()},'comparison':comparison,'storage':storage,
        'regressions_vs_m1a':{arm:[r['id'] for r in comparison if r['M1A'] and not r[arm]] for arm in ('A','B')},
        'failures':{arm:[r['id'] for r in result[arm] if not r['pass']] for arm in ('A','B')},
        'input_sha256':digest(context_bench/'cases.json'),'catalog_sha256':digest(context_bench/'demo_metric_catalog-frozen.json')}
    dump(output/'context-summary.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',required=True)
    args=parser.parse_args()
    frozen=json.loads((BENCH/'manifest.json').read_text())
    for name,sha in frozen['files'].items():
        assert digest(ROOT/name)==sha,'frozen input/scorer changed: '+name
    output=ROOT/'docs/memory_rl/runs'/args.label;runtime=ROOT/'runtime'/args.label
    if output.exists() or runtime.exists():parser.error('preserve evidence: choose unused label')
    output.mkdir(parents=True);runtime.mkdir(parents=True)
    os.environ['ICT8_FAST_SQL']='0';os.environ['ICT8_DISABLE_DENSE']='1'
    payload=json.loads((BENCH/'tasks.json').read_text());sources=json.loads((BENCH/'sources.json').read_text());pool=json.loads((BENCH/'candidate_pool.json').read_text())
    paths=build_fixtures(runtime,initialize_rich_demo_data);before=source_hashes()
    databases={f:digest(p) for f,(p,_) in paths.items()}
    sensitive=run_sensitive(output,runtime,paths,payload,sources,pool)
    context=run_context(output,runtime,paths,payload,sources,pool)
    after=source_hashes();dbafter={f:digest(p) for f,(p,_) in paths.items()}
    metadata={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'created_at_utc':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,
        'packages':dict(sorted((d.metadata['Name'],d.version) for d in importlib.metadata.distributions())),
        'input_manifest_sha256':digest(BENCH/'manifest.json'),'frozen_hashes':frozen['files'],
        'runner_sha256':digest(Path(__file__)),'source_before':before,'source_after':after,'implementation_stable':before==after,
        'database_sha256_before':databases,'database_sha256_after':dbafter,'database_read_only_verified':databases==dbafter,
        'fixtures':{f:{'database':str(p),'aliases':str(a) if a else None,'aliases_sha256':digest(a) if a else None} for f,(p,a) in paths.items()},
        'config':{'model':None,'remote_calls':0,'rules':'original rules_basic','ICT8_FAST_SQL':'0','ICT8_DISABLE_DENSE':'1',
            'reference_date':payload['reference_date'],'reference_time':payload['reference_time'],'max_items':3,'max_characters':1800,
            'max_rows':200,'max_steps':300000,'max_seconds':2.0},
        'metrics_note':'One paired serial observation per task; no latency speedup claim. Trace business calls exclude recall/observe and internal SQL probes. Seeded store footprint reported separately from target event growth.',
        'real_remote_model':{'status':'not_run','reason':'No configured approved remote model endpoint/budget; mock model contract tested separately.'},
        'sensitive':sensitive['arms'],'context':context['arms']}
    # Record actual engine limits, not assumed defaults.
    sample=Nl2SqlEngine(paths['mixed'][0],aliases_path=paths['mixed'][1])
    metadata['config'].update({k:getattr(sample,k) for k in ('max_rows','max_steps','max_seconds')})
    dump(output/'metadata.json',metadata)
    print(json.dumps({'sensitive':metadata['sensitive'],'context':metadata['context'],'stable':before==after,'read_only':databases==dbafter},ensure_ascii=False))


if __name__=='__main__':main()
