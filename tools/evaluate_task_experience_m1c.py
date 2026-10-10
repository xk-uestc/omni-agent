"""Frozen, real Omni A/B/Oracle/plain-control; target Gold is scoring-only."""
import argparse
from copy import deepcopy
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import shutil
import subprocess
import time
import sys
from evaluate_memory_m1b1 import ROOT,dump,digest,source_hashes
from m1c_fixture import BENCH,setup,seed_cases,TITLES
from m1c_model_client import BudgetedPlanner
from score_task_experience_m1c import score
from backend.omni_agent import OmniAgent,fusion_retrieval_summary
from backend.fusion_result_answer import calculation_answer
from backend.session import ConversationStore
from backend.knowledge_store import KnowledgeStore
from backend.memory.core import MemoryStore,MemoryCore,TrustedScope,encoded
from backend.memory.adapter import MemoryAdapter
from backend.memory.experience import ExperienceFormation,ExperienceSelector
from backend.memory.extraction import digest as object_digest
from backend.memory.formation import LocalReviewContext

NOW='2026-10-10T12:00:00+00:00'
SCOPE=TrustedScope('m1c','competition-demo',tuple(['database',*TITLES]))

def adapter(engine,k,path):return MemoryAdapter(MemoryCore(MemoryStore(path),scope=SCOPE,enabled=True),engine,k,clock=lambda:NOW)

def versions(k):return {d['document_id']:d['sha256'] for d in k.list_documents()}

def seed_verifier(gold,engine,k):
    def verify(question,tasks,result):
        rendered=deepcopy(result);rendered['execution_plan']=tasks
        answer=calculation_answer(tasks,rendered,k)
        if answer:rendered['answer']=answer
        fusion_retrieval_summary(rendered)
        return score(gold,{'status':result['status'],'result':rendered},engine.database_path,versions(k))
    return verify

def make_pool(root,out,engine,k,aliases,client):
    a=adapter(engine,k,root/'seed-memory.sqlite');formation=ExperienceFormation(a)
    config=root/'admin.json';config.write_text(json.dumps({'scope':asdict(SCOPE),'memory_db':str(a.core.store.path),'database':str(engine.database_path),
        'knowledge_root':str(root/'knowledge'),'database_source':'database','aliases':str(aliases),'catalog':str(root/'no-catalog.json'),
        'reference_date':'2026-10-09','reference_time':NOW}));config.chmod(0o600)
    authority=LocalReviewContext.from_config(config);gold=json.loads((BENCH/'seed_gold.json').read_text());records=[]
    for index,seed in enumerate(seed_cases()):
        model=None;first=len(client.calls)
        if index<2:
            model=OmniAgent(engine,k,ConversationStore(),client).query(seed['question'],session_id='seed-session-'+str(index))
            evaluated=score(gold[index],model,engine.database_path,versions(k))
            dump(out/f'seed-agent-{index}.json',{'question':seed['question'],'response':model,'score':evaluated,'calls':client.calls[first:]})
            if not evaluated['task_success']:
                records.append({'index':index,'formed':False,'reason':'real_agent_failed_independent_whole_task','score':evaluated});continue
            tasks=model['result']['execution_plan'];receipt={'model':client.audit['response_model'],'model_verified':client.audit['model_verified'],
                'plan_sha256':object_digest(tasks),'question_sha256':object_digest(seed['question']),
                'model_call_audit_sha256':object_digest([c['audit'] for c in client.calls[first:]])}
            origin='agent_execution'
        else:
            tasks=seed['tasks'];receipt=None;origin='developer_verified_seed'
            from backend.dependency_agent import DependencyAgent
            checked=seed_verifier(gold[index],engine,k)(seed['question'],tasks,DependencyAgent(engine,k).run(tasks,original_question=seed['question']))
            if not checked['task_success']:
                records.append({'index':index,'formed':False,'reason':'developer_graph_failed_full_scope','score':checked});continue
        c=formation.capture_verified_run(seed['question'],tasks,verifier=seed_verifier(gold[index],engine,k),verifier_id=digest(Path(__file__).with_name('score_task_experience_m1c.py')),origin=origin,planner_receipt=receipt)
        # Actual local administrator child processes; no direct Store.put.
        cli=[]
        for action,extra in [('validate',[]),('review',['--decision','confirm','--request-id',f'seed-review-{index}','--reason','Independent whole-task fixture verification'])]:
            cmd=[sys.executable,'-m','backend.memory.admin','--config',str(config),'--type','task_experience',action,'--candidate',c['candidate_id'],'--digest',c['digest'],*extra]
            import os
            result=subprocess.run(cmd,cwd=ROOT,env={**os.environ,'PYTHONPATH':str(ROOT/'ict-track8')},capture_output=True,text=True)
            cli.append({'command':cmd,'exit':result.returncode,'stdout':result.stdout})
            if result.returncode:raise ValueError('local seed review failed; see receipts')
        records.append({'index':index,'formed':True,'candidate':c,'cli':cli})
        if index==0:
            alternative=[tasks[-2],*tasks[:-2],tasks[-1]]
            # Same proven semantics, different legal independent read ordering.
            other=formation.capture_verified_run(seed['question'],alternative,verifier=seed_verifier(gold[index],engine,k),verifier_id=digest(Path(__file__).with_name('score_task_experience_m1c.py')))
            formation.validate(other['candidate_id'],other['digest'])
            review=formation.review(other['candidate_id'],other['digest'],context=authority,request_id='alternative-seed',decision='confirm',reason='Verified alternate independent read ordering')
            records.append({'index':'alternative_order','formed':True,'candidate':other,'review':review})
    dump(out/'seed-formation.json',records)
    return a.core.store.path

class ArmSelector:
    def __init__(self,base,arm):self.base,self.arm=base,arm
    def select(self,question):
        if self.arm=='C':
            # Oracle reads only legal candidates and method shape, never target Gold.
            probe=self.base.select(question)
            eligible={c['memory_id']:c['experience'] for c in probe['state']['candidates'] if c['reason']=='eligible'}
            selected=min(eligible,key=lambda key:(len(eligible[key]['steps']),key)) if eligible else None
            return self.base.select(question,choose=lambda legal:selected)
        d=self.base.select(question)
        if self.arm=='D':
            d['selected']=[];d['action']={'type':'generic_planning_checklist'}
            d['advice']={'kind':'ordinary_planning_advice','checklist':['Preserve every user operation, source, region and time constraint.',
                'Generate a new complete dependency graph using current question, schema and document evidence.',
                'Read current formulas and typed cells; never reuse historical values.',
                'Bind SQL result dimensions to retrieval and verify each source. Clarify if unavailable.']}
        return d
    def observe(self,d,r):return self.base.observe(d,r)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);parser.add_argument('--label',required=True);parser.add_argument('--max-calls',type=int,default=144);args=parser.parse_args()
    manifest=json.loads((BENCH/'manifest.json').read_text())
    for p,h in manifest['files'].items():
        if digest(ROOT/p)!=h:raise ValueError('frozen input/scorer changed: '+p)
    runtime=ROOT/'runtime'/args.label;out=ROOT/'docs/memory_rl/runs'/args.label
    if runtime.exists() or out.exists():raise ValueError('preserve prior run')
    runtime.mkdir(parents=True);out.mkdir(parents=True)
    engine,k,aliases=setup(runtime);before=digest(engine.database_path);backend_before=source_hashes()
    client=BudgetedPlanner(args.config,ROOT/'runtime/private/m1c-model-call-ledger.jsonl',max_calls=args.max_calls)
    seed=make_pool(runtime,out,engine,k,aliases,client)
    inputs=json.loads((BENCH/'tasks.json').read_text());all_records=[]
    for index,task in enumerate(inputs):
        # Rotate order to reduce a fixed arm/time/cache confound.
        arms=['A','B','C','D'];arms=arms[index%4:]+arms[:index%4]
        for arm in arms:
            case_dir=runtime/(task['id']+'-'+arm);case_dir.mkdir();shutil.copytree(runtime/'knowledge',case_dir/'knowledge')
            local=KnowledgeStore(case_dir/'knowledge');shutil.copy2(seed,case_dir/'memory.sqlite')
            a=adapter(engine,local,case_dir/'memory.sqlite')
            if task['variant']=='stale_policy':
                text=json.loads((BENCH/'sources.json').read_text())['policy']+'\n资料版本：第二版。'
                local.ingest(text.encode(),document_id='policy',title=TITLES['policy'],modality='txt',filename='policy.txt')
            if task['variant']=='missing_targets':
                path,_=local.original('targets');path.unlink()
            if task['variant']=='revoked_forecast':
                c=case_dir/'admin.json';c.write_text(json.dumps({'scope':asdict(SCOPE),'memory_db':str(a.core.store.path)}));c.chmod(0o600)
                for record in a.core.store.scan(SCOPE)[0]:
                    if record.experience and '目标销售额' in record.experience['formula_labels']:
                        ExperienceFormation(a).revoke(record.memory_id,object_digest(asdict(record)),context=LocalReviewContext.from_config(c),request_id='revoke-'+record.memory_id,reason='fixture withdrawal')
            selector=ArmSelector(ExperienceSelector(a),arm) if arm!='A' else None
            trace=[];first=len(client.calls);start=time.perf_counter()
            response=OmniAgent(engine,local,ConversationStore(),client,experience=selector).query(task['question'],session_id='new-'+task['id'],trace_callback=trace.append)
            wall=round((time.perf_counter()-start)*1000,3);calls=deepcopy(client.calls[first:])
            planning=next((e for e in response.get('trace',[]) if e.get('stage')=='intent_planning'),{})
            decision=planning.get('task_experience',{'selected':[],'action':{'type':'no_experience'},'state':{'candidates':[]}})
            record={'task':task,'arm':arm,'response':response,'model_calls':calls,'decision':decision,'trace':trace,'wall_ms':wall,'source_versions':versions(local),
                'model_call_count':len(calls),'input_tokens':sum(c['audit'].get('input_tokens') or 0 for c in calls),'output_tokens':sum(c['audit'].get('output_tokens') or 0 for c in calls),
                'tool_calls':sum(e.get('status')=='running' for e in trace),'pool':[asdict(r) for r in a.core.store.scan(SCOPE)[0]],'events':a.core.store.events()}
            dump(out/f'{task["id"]}-{arm}.json',record);all_records.append(record)
            print(json.dumps({'id':task['id'],'arm':arm,'status':response['status'],'calls':len(calls),'selected':decision['selected']},ensure_ascii=False),flush=True)
    # Only now open target Gold; it never enters seed creation, retrieval or model contexts.
    gold=json.loads((BENCH/'gold.json').read_text())
    for r in all_records:
        g=gold[r['task']['id']];r['score']=score(g,r['response'],engine.database_path,r['source_versions'])
        selected=r['decision']['selected'];candidates=r['decision']['state']['candidates'];eligible={c['memory_id'] for c in candidates if c['reason']=='eligible'}
        r['selection_correct']=(bool(selected) if g['expected_experience'] else not selected) if r['arm'] in {'B','C'} else None
        r['stale_or_wrong_use']=bool(set(selected)-eligible)
        r['controller_feedback']={**r['score'],'cost':{'model_calls':r['model_call_count'],'input_tokens':r['input_tokens'],'output_tokens':r['output_tokens'],'tool_calls':r['tool_calls'],'wall_ms':r['wall_ms']},
            'failure_reason':r['response'].get('result',{}).get('error_code'),'trained_policy':False}
        dump(out/f'{r["task"]["id"]}-{r["arm"]}.json',r)
    summary={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_hashes_before':backend_before,'source_hashes_after':source_hashes(),
        'db_before':before,'db_after':digest(engine.database_path),'manifest_sha256':digest(BENCH/'manifest.json'),'runner_sha256':digest(Path(__file__)),
        'model':client.model,'temperature':0,'records':[],'arm_metrics':{}}
    for arm in ['A','B','C','D']:
        rows=[r for r in all_records if r['arm']==arm]
        summary['arm_metrics'][arm]={'passed':sum(r['score']['task_success'] for r in rows),'total':len(rows),'splits':{s:sum(r['score']['task_success'] for r in rows if r['task']['split']==s) for s in ['development','heldout']},
            'model_calls':sum(r['model_call_count'] for r in rows),'input_tokens':sum(r['input_tokens'] for r in rows),'output_tokens':sum(r['output_tokens'] for r in rows),'wall_ms':sum(r['wall_ms'] for r in rows),
            'selection_correct':sum(r['selection_correct'] is True for r in rows),'stale_or_wrong_use':sum(r['stale_or_wrong_use'] for r in rows)}
    summary['records']=[{'id':r['task']['id'],'arm':r['arm'],'score':r['score'],'selection_correct':r['selection_correct']} for r in all_records]
    summary['run_calls_including_seeds']=len(client.calls);summary['ledger_total']=len((ROOT/'runtime/private/m1c-model-call-ledger.jsonl').read_text().splitlines())
    dump(out/'summary.json',summary);print(json.dumps(summary['arm_metrics'],ensure_ascii=False),flush=True)

if __name__=='__main__':main()
