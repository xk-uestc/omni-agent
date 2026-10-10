"""Serial paired memory profile. Stage timings are inclusive, never additive."""
import argparse
from dataclasses import replace
from datetime import date
import json
import os
import platform
from pathlib import Path
import statistics
import subprocess
import time
from evaluate_memory_m1b1 import ROOT,digest,dump,source_hashes
from backend.nl2sql.seed import initialize_rich_demo_data
from backend.nl2sql.engine import Nl2SqlEngine
from backend.knowledge_store import KnowledgeStore
from backend.session import ConversationStore
from backend.omni_agent import OmniAgent
from backend.memory.core import MemoryCore,MemoryStore,MemoryRecord,TrustedScope
from backend.memory.adapter import MemoryAdapter


def quantile(values,fraction):
    values=sorted(values);return round(values[min(len(values)-1,int((len(values)-1)*fraction))],3)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',required=True);p.add_argument('--repeats',type=int,default=5);a=p.parse_args()
    out=ROOT/'docs/memory_rl/runs'/a.label;runtime=ROOT/'runtime'/a.label
    if out.exists() or runtime.exists():p.error('preserve evidence: unused label required')
    out.mkdir(parents=True);runtime.mkdir(parents=True)
    os.environ['ICT8_DISABLE_DENSE']='1';os.environ['ICT8_FAST_SQL']='0'
    db=initialize_rich_demo_data(runtime/'rich.sqlite');before=source_hashes();dbhash=digest(db)
    knowledge=KnowledgeStore(runtime/'knowledge')
    for i in range(20):knowledge.ingest(('性能诊断来源文档'+str(i)+'。无业务答案。'*150).encode(),document_id='profile-'+str(i),title='性能资料'+str(i),modality='txt',filename='source.txt')
    scope=TrustedScope('profile','single-project',('db',*(f'profile-{i}' for i in range(20))))
    samples=[];load_before=os.getloadavg();started=time.time()
    for size in (0,16,128):
        store=MemoryStore(runtime/f'memory-{size}.sqlite')
        def construct(enabled):
            engine=Nl2SqlEngine(db,reference_date=date(2026,10,9))
            adapter=MemoryAdapter(MemoryCore(store,scope=scope,enabled=enabled),engine,knowledge,database_source='db',clock=lambda:'2026-10-10T12:00:00+08:00')
            return OmniAgent(engine,knowledge,ConversationStore(),memory=adapter)
        author=construct(True).memory
        record=MemoryRecord('profile-0','business_semantics','海棠额','销售额合计',{'table':'sales_orders','column':'sales_amount','function':'SUM','filters':[]},scope,
            {'authority':'profile_fixture','confirmation_id':'c','evidence_id':'profile-0','verification_id':'v'},author.source_version(('profile-0',)),
            '2026-10-01T00:00:00+08:00',None,'confirmed','2026-10-01T00:00:00+08:00','2026-10-01T00:00:00+08:00')
        for i in range(size):store.put(replace(record,memory_id=f'profile-{i}',term='海棠额' if i==0 else f'无关术语{i}'))
        for match in ('no_hit','hit') if size else ('no_hit',):
            question='2025年华东'+('海棠额' if match=='hit' else '销售额')
            for warm in (False,True):
                for repeat in range(a.repeats):
                    # Alternate pair order to reduce systematic warm/cache bias.
                    for enabled in ((False,True) if repeat%2==0 else (True,False)):
                        agent=construct(enabled);m=agent.memory
                        if warm:agent.query(question)
                        phase={};calls={};originals=[]
                        def instrument(obj,name,label):
                            old=getattr(obj,name);originals.append((obj,name,old))
                            def wrapped(*args,**kwargs):
                                st=time.perf_counter()
                                try:return old(*args,**kwargs)
                                finally:
                                    phase[label]=phase.get(label,0)+(time.perf_counter()-st)*1000;calls[label]=calls.get(label,0)+1
                            setattr(obj,name,wrapped)
                        for obj,name,label in [(m,'_snapshot','memory_snapshot'),(knowledge,'list_documents','document_inventory'),
                            (knowledge,'verify_source','document_sha'),(store,'scan','store_scan'),(store,'connect','store_connect_factory'),
                            (agent.engine,'_snapshot_for','engine_snapshot'),(agent.engine,'extract_required_intent','canonical_and_rule_plan'),
                            (m.core,'recall','core_recall'),(m.core,'observe','observe'),(m,'prepare','prepare'),
                            (agent,'_query_without_memory','original_query_chain')]:instrument(obj,name,label)
                        start=time.perf_counter();cpu=time.process_time()
                        try:r=agent.query(question)
                        finally:
                            wall=(time.perf_counter()-start)*1000;cpu_ms=(time.process_time()-cpu)*1000
                            for obj,name,old in reversed(originals):setattr(obj,name,old)
                        samples.append({'size':size,'match':match,'warm':warm,'repeat':repeat,'enabled':enabled,'wall_ms':round(wall,3),
                            'cpu_ms':round(cpu_ms,3),'phase_ms':{k:round(v,3) for k,v in phase.items()},'calls':calls,
                            'status':r['status'],'consumed':r.get('memory',{}).get('consumed',[]),'rows':r.get('result',{}).get('rows')})
        print(json.dumps({'size':size,'completed_samples':len(samples)}),flush=True)
    groups=[]
    for size,match,warm,enabled in sorted({(r['size'],r['match'],r['warm'],r['enabled']) for r in samples}):
        rs=[r for r in samples if (r['size'],r['match'],r['warm'],r['enabled'])==(size,match,warm,enabled)]
        groups.append({'size':size,'match':match,'warm':warm,'enabled':enabled,'samples':len(rs),
            'p50_ms':quantile([r['wall_ms'] for r in rs],.5),'p95_ms':quantile([r['wall_ms'] for r in rs],.95),
            'phase_mean_ms':{k:round(statistics.mean(r['phase_ms'].get(k,0) for r in rs),3) for k in sorted({k for r in rs for k in r['phase_ms']})},
            'mean_calls':{k:round(statistics.mean(r['calls'].get(k,0) for r in rs),3) for k in sorted({k for r in rs for k in r['calls']})}})
    report={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_hashes':before,
        'source_stable':before==source_hashes(),'database_unchanged':dbhash==digest(db),'database_sha256':dbhash,
        'runner_sha256':digest(Path(__file__)),'machine':platform.platform(),'cpu_count':os.cpu_count(),'load_before':load_before,'load_after':os.getloadavg(),
        'elapsed_s':round(time.time()-started,3),'groups':groups,'samples':samples,
        'limitations':['No other agent evaluation/test job runs concurrently; unrelated host workload is not controllable.',
            'Five samples per condition; descriptive quantiles, no statistical speedup claim.','Cold means fresh engine caches, not cleared OS page cache.',
            'Stage wrappers measure inclusive time. connect factory counts constructions, not connection opening/commit latency; observe/scan contain actual I/O.',
            'No-hit off/on perform same business SQL; hit off cannot answer unknown term, so hit latency is not equal-work speed comparison.']}
    dump(out/'profile.json',report);print(json.dumps({'groups':groups,'load_before':load_before,'load_after':report['load_after'],'source_stable':report['source_stable']},ensure_ascii=False))


if __name__=='__main__':main()
