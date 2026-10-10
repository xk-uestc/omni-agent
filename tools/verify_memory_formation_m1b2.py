"""Verify formation lineage and scorer controls from saved actual executions."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from evaluate_memory_m1b1 import ROOT,digest,dump,source_hashes
from score_memory_formation_m1b2 import score


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--label',required=True);a=p.parse_args()
    out=ROOT/'docs/memory_rl/runs'/a.label;target=out/'verification.json'
    if target.exists():p.error('preserve evidence')
    bench=ROOT/'benchmarks/memory_formation_m1b2_20261010';manifest=json.loads((bench/'manifest.json').read_text())
    assert all(digest(ROOT/f)==h for f,h in manifest['files'].items())
    summary=json.loads((out/'summary.json').read_text());formation=json.loads((out/'formation.json').read_text())
    gold=json.loads((bench/'gold.json').read_text());sources=json.loads((bench/'sources.json').read_text())['sources']
    valid=0
    for row in formation:
        key=next(k for k,v in sources.items() if v['document_id']==row['candidate']['evidence']['document_id'])
        expected=gold['formation'][key]
        valid+=bool(expected['promotion_allowed'] and expected['binding'] is not None and row['score']['candidate_accurate'])
    proofs=[]
    for path in sorted((out/'C').glob('p*.json')):
        r=json.loads(path.read_text());memory=r['confirmed_records'][0];review=next(x for x in r['admin_commands'] if 'review' in x['command'])
        cid=memory['provenance']['candidate_id'];event_id=memory['task_trace_id']
        proof={'task':r['id'],'no_put_enforced':r['C_no_put_enforced'],'target_history_zero':r['target_history_before']==0,
            'candidate_link':any(c['candidate']['candidate_id']==cid for c in r['candidates']),
            'observed_event_link':any(e['event_id']==event_id for e in r['audit']['memory_source_events']),
            'child_cli_review_succeeded':review['returncode']==0 and review['result']['memory_id']==memory['memory_id'],
            'real_sql':bool(r['response']['result'].get('sql')),'task_gold_pass':r['pass'],
            'memory_consumed':memory['memory_id'] in r['memory']['consumed']}
        assert all(v for k,v in proof.items() if k!='task');proofs.append(proof)
    r=json.loads((out/'C/p09.json').read_text());base=r['response'];db=ROOT/'runtime'/a.label/'rich.sqlite';checks=[]
    def check(name,response,expect):
        passed,detail=score(gold['tasks']['p09'],response,db,r['source_versions'])
        combined=bool(passed) and bool(response.get('memory',{}).get('consumed'))
        checks.append({'name':name,'task_score':bool(passed),'consumed':bool(response.get('memory',{}).get('consumed')),'combined':combined,'expected':expect,'detail':detail})
        assert combined==expect,name
    check('actual_execution',base,True)
    q=deepcopy(base);q['result']['parameters'][2]='门店';check('wrong_channel',q,False)
    q=deepcopy(base);q['result']['parameters'][0]='2024-01-01';check('wrong_time',q,False)
    q=deepcopy(base);q['result']['sql']=q['result']['sql'].replace('sales_amount','quantity');check('wrong_field',q,False)
    q=deepcopy(base);q['result']['sql']=None;check('missing_execution',q,False)
    q=deepcopy(base);q['memory']['consumed']=[];check('no_consumption',q,False)
    baseline=json.loads((ROOT/'docs/memory_rl/runs/m1b1-ab-final-20261010/context-summary.json').read_text())
    latest=json.loads((ROOT/'docs/memory_rl/runs/m1b2-legacy-final-20261010/context-summary.json').read_text())
    old={r['id']:r for r in baseline['comparison']}
    legacy_equal=all(r['A']==old[r['id']]['A'] and r['B']==old[r['id']]['B'] for r in latest['comparison'])
    old16=json.loads((ROOT/'docs/memory_rl/runs/m1b1-ab-final-20261010/sensitive-summary.json').read_text())
    new16=json.loads((ROOT/'docs/memory_rl/runs/m1b2-legacy-final-20261010/sensitive-summary.json').read_text())
    legacy16_equal=all(a['id']==b['id'] and a['A']==b['A'] and a['B']==b['B'] for a,b in zip(old16['comparison'],new16['comparison']))
    data={'frozen_hashes_verified':True,'backend_matches_executed':source_hashes()==summary['source_after'],
        'database_read_only':summary['database_sha256_before']==summary['database_sha256_after'],
        'candidate_precision_definition':'Independent fixture Gold: complete, nonconflicting, accurate semantic candidate; separate from literal extraction fidelity.',
        'candidate_precision':{'numerator':valid,'denominator':len(formation)},'source_extraction_fidelity':{'numerator':sum(r['score']['candidate_accurate'] for r in formation),'denominator':len(formation)},
        'formation_proofs':proofs,'scorer_controls':checks,'legacy185_matches_m1b1':legacy_equal,'legacy16_matches_m1b1':legacy16_equal,
        'new_process_proof':{k:json.loads((out/'C/p01.json').read_text()).get(k) for k in ['worker_pid','process_command','target_history_before']},
        'BC_knowledge_bindings_equal':all(r['BC_binding_equal'] for r in summary['comparison']),
        'BC_rows_equal':all(r['BC_rows_equal'] for r in summary['comparison'])}
    dump(target,data);print(json.dumps({k:v for k,v in data.items() if k not in {'formation_proofs','scorer_controls','new_process_proof'}},ensure_ascii=False))


if __name__=='__main__':main()
