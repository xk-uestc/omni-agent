"""Four no-memory real-planner probes; total call budget is explicit."""
import argparse
import json
import subprocess
from diagnose_memory_m1c import environment,cases
from evaluate_memory_m1b1 import ROOT,dump,digest,source_hashes
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore
from m1c_model_client import BudgetedPlanner


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--max-calls',type=int,required=True);p.add_argument('--label',required=True);a=p.parse_args()
    runtime=ROOT/'runtime'/a.label;out=ROOT/'docs/memory_rl/runs'/a.label
    if runtime.exists() or out.exists():raise ValueError('preserve prior probes')
    runtime.mkdir(parents=True);out.mkdir(parents=True);engine,knowledge=environment(runtime)
    client=BudgetedPlanner(a.config,ROOT/'runtime/private/m1c-model-call-ledger.jsonl',max_calls=a.max_calls)
    records=[];before=digest(engine.database_path)
    for case in cases():
        first=len(client.calls);trace=[]
        result=OmniAgent(engine,knowledge,ConversationStore(),client).query(case['question'],session_id='fresh',trace_callback=trace.append)
        record={'workflow':case['workflow'],'question':case['question'],'response':result,'calls':client.calls[first:],'trace':trace}
        records.append(record);dump(out/(case['workflow']+'.json'),record)
        print(json.dumps({'workflow':case['workflow'],'status':result['status'],'planner_source':result.get('planner_source'),'model_calls':len(client.calls)-first,'error':result.get('result',{}).get('error_code')},ensure_ascii=False),flush=True)
    dump(out/'summary.json',{'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_hashes':source_hashes(),
        'db_before':before,'db_after':digest(engine.database_path),'calls':len(client.calls),
        'input_tokens':sum(c['audit'].get('input_tokens') or 0 for c in client.calls),'output_tokens':sum(c['audit'].get('output_tokens') or 0 for c in client.calls),
        'scores':'diagnostic statuses only; independent whole-task evaluation follows frozen scorer','workflows':[{ 'workflow':r['workflow'],'status':r['response']['status']} for r in records]})

if __name__=='__main__':main()
