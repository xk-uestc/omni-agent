"""Observe a retained failed anonymous query, without reference/model hints."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import StructuredResponses
from backend.native_row_selection import route_native_row_selection
from evaluate_ohr_bench import implementation_snapshot,safe_audits
from model_runtime import enable_local_model,local_model_headers


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--case-id',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().parent!=(ROOT/'docs').resolve() or args.output.exists():
        parser.error('New output directly in docs required')
    baseline=json.loads(args.baseline.read_text(encoding='utf-8'))
    if baseline.get('gold_sent_to_model') is not False or baseline.get('implementation_stable') is not True:
        parser.error('Stable isolated-reference baseline required')
    case=next(c for c in baseline['cases'] if c['case_id']==args.case_id)
    location=Path(baseline['run_directory']).resolve()/case['case_id']/'knowledge'
    if not location.is_relative_to((ROOT/'runtime').resolve()):
        parser.error('Retained local source required')
    enable_local_model('gpt-6-luna');before=implementation_snapshot()
    client=StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],
        model='gpt-6-luna',reasoning='medium',http_headers=local_model_headers())
    observed=[];original_generate=client.generate
    def observe(*a,**kw):
        value=original_generate(*a,**kw)
        if kw.get('name','').startswith('native_row_selection'):
            observed.append({'operation':kw['name'],'returned_selection_or_review':value})
        return value
    client.generate=observe
    store=KnowledgeStore(location,generator=GroundedGenerator(client))
    result,trace=route_native_row_selection(store,case['question'],store.search(case['question'],top_k=4))
    after=implementation_snapshot()
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'observed_production_plan_protocol_not_accuracy',
        'gold_sent_to_model':False,'question':case['question'],'case_id':case['case_id'],
        'model':'gpt-6-luna','reasoning':'medium','observed':observed,'result':result,'trace':trace,
        'api_audits':safe_audits(client),'implementation_stable':before==after,
        'implementation_file_sha256_start':before,'implementation_file_sha256_end':after}
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({'status':result['status'] if result else None,'reason':trace.get('reason'),'stable':before==after}))


if __name__=='__main__':main()
