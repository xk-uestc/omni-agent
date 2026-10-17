"""Post-run evidence audit; never changes frozen scores or replays model calls."""
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import statistics
from evaluate_memory_m1b1 import ROOT,dump,digest
from backend.memory.admin import load
from backend.memory.core import MemoryCore,MemoryStore,TrustedScope
from backend.memory.adapter import MemoryAdapter
from backend.memory.experience import ExperienceSelector,ExperienceFormation
from backend.memory.formation import LocalReviewContext
from backend.memory.extraction import digest as object_digest
from backend.knowledge_store import KnowledgeStore


def main():
    out=ROOT/'docs/memory_rl/runs/m1c-formal-final-20261010';runtime=ROOT/'runtime/m1c-formal-final-20261010'
    summary=json.loads((out/'summary.json').read_text());rows=[json.loads(p.read_text()) for p in sorted(out.glob('[dh]*-[ABCD].json'))]
    manifest=json.loads((ROOT/'benchmarks/task_experience_m1c_20261010/manifest.json').read_text())
    report={'frozen_hashes_match':all(digest(ROOT/p)==h for p,h in manifest['files'].items()),'source_stable':summary['source_hashes_before']==summary['source_hashes_after'],
        'db_stable':summary['db_before']==summary['db_after'],'heldout_seen_only_in_formal_final':not any(p.name.startswith('h') for p in (out.parent/'m1c-formal-20261010').glob('[dh]*-[ABCD].json')),
        'target_count':len(rows),'new_session_history_zero':all(r['response']['context_turns']==0 for r in rows),'arms':{},'B_C_pairs':[]}
    pct=lambda vs,q:sorted(vs)[int((len(vs)-1)*q)]
    for arm in ['A','B','C','D']:
        subset=[r for r in rows if r['arm']==arm]
        report['arms'][arm]={**summary['arm_metrics'][arm],
            'normalized_model_plan_valid':sum(r['response'].get('planner_source')=='model_validated' for r in subset),
            'actual_required_operation_coverage':sum(r['score']['operation_coverage'] for r in subset),
            'result_correct':sum(r['score']['result_correct'] for r in subset),'source_attribution_correct':sum(r['score']['source_correct'] for r in subset),
            'tool_calls':sum(r['tool_calls'] for r in subset),'wall_p50_ms':statistics.median(r['wall_ms'] for r in subset),'wall_p95_ms':pct([r['wall_ms'] for r in subset],.95),
            'retrieval_total_ms':sum(r['decision'].get('retrieval_ms',0) for r in subset),'selected_tasks':[r['task']['id'] for r in subset if r['decision']['selected']],
            'model_json_failures':sum(c['audit'].get('error_type')=='JSONDecodeError' for r in subset for c in r['model_calls'])}
    for ident in sorted({r['task']['id'] for r in rows}):
        b=next(r for r in rows if r['task']['id']==ident and r['arm']=='B');c=next(r for r in rows if r['task']['id']==ident and r['arm']=='C')
        report['B_C_pairs'].append({'id':ident,'same_selected':b['decision']['selected']==c['decision']['selected'],
            'same_initial_model_context':(b['model_calls'][0]['context'] if b['model_calls'] else None)==(c['model_calls'][0]['context'] if c['model_calls'] else None),
            'B_pass':b['score']['task_success'],'C_pass':c['score']['task_success']})
    all_calls=[]
    for label in ['m1c-model-preflight-20261010','m1c-formal-20261010','m1c-formal-final-20261010']:
        directory=out.parent/label
        for f in directory.glob('*.json'):
            d=json.loads(f.read_text())
            if 'calls' in d and isinstance(d['calls'],list):all_calls.extend(d['calls'])
            if 'model_calls' in d and isinstance(d['model_calls'],list):all_calls.extend(d['model_calls'])
    calls={c['audit']['call_number']:c['audit'] for c in all_calls}
    ledger=[json.loads(line) for line in (ROOT/'runtime/private/m1c-model-call-ledger.jsonl').read_text().splitlines()]
    report['cumulative_cost']={'reserved_calls':len(ledger),'recorded_call_audits':len(calls),'missing_response_audit_numbers':[e['call'] for e in ledger if e['call'] not in calls],
        'known_input_tokens':sum(c.get('input_tokens') or 0 for c in calls.values()),'known_output_tokens':sum(c.get('output_tokens') or 0 for c in calls.values()),
        'unknown_tokens_not_zero':True,'cap':144,'within_approved_budget':len(ledger)<=144,'currency_cost':'not independently verified; billing dashboard authoritative'}
    # Actual confirmed model-formed B memory, not a vacuous stale/revoke check on missing A seed.
    original,_=load(runtime/'admin.json','task_experience');base=original.adapter;checks=[]
    question='先从数据库查2024年销量排名第一的地区，再根据该地区检索冠军团队的方法，保留来源。'
    for mode in ['stale','revoked','scope']:
        root=ROOT/'runtime'/('m1c-governance-'+mode)
        if root.exists():raise ValueError('preserve prior supplemental governance run')
        root.mkdir();shutil.copy2(base.core.store.path,root/'memory.sqlite');shutil.copytree(runtime/'knowledge',root/'knowledge')
        k=KnowledgeStore(root/'knowledge');a=MemoryAdapter(MemoryCore(MemoryStore(root/'memory.sqlite'),scope=base.core.scope,enabled=True),base.engine,k,clock=base.clock)
        before=ExperienceSelector(a).select(question);assert len(before['selected'])==1
        if mode=='stale':
            source,_=k.original('methods');source.write_bytes(source.read_bytes()+b'\nchanged')
        elif mode=='revoked':
            c=root/'admin.json';c.write_text(json.dumps({'scope':asdict(a.core.scope),'memory_db':str(a.core.store.path)}));c.chmod(0o600)
            record=a.core.store.scan(a.core.scope)[0][0]
            ExperienceFormation(a).revoke(record.memory_id,object_digest(asdict(record)),context=LocalReviewContext.from_config(c),request_id='supplemental-revoke',reason='Governance verification on actual model-formed memory')
        else:a.core.scope=TrustedScope('m1c','other-project',base.core.scope.data_sources)
        after=ExperienceSelector(a).select(question)
        checks.append({'mode':mode,'before':before,'after':after,'rejected':not after['selected']})
    report['supplemental_governance']=checks
    report['limitations']=['h07 frozen scorer checks safe non-answer, not accuracy of clarification explanation; generic model-unavailable message is not a correct missing-source diagnosis.',
        'd08/h08 main-run forecast lifecycle controls are vacuous because forecast seed failed; supplemental B checks and lifecycle unit tests provide nonvacuous evidence.',
        'Oracle and Fixed selected the same single candidate; paired outcome differences are not selection-policy gains.',
        'D matches maximum context/output budget, not actual input token count.','Formal results are single runs and cannot establish statistical gains.']
    dump(out/'verification.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in {'supplemental_governance','arms','B_C_pairs'}},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
