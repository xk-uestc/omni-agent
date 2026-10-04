"""Inspect literal selections and rejected checks; no answers/reference input."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from model_runtime import enable_local_model,local_model_headers
from backend.responses_client import StructuredResponses
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore
from backend.visual_source_answer import route_visual_source_fallback


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store',type=Path,required=True)
    parser.add_argument('--document',required=True)
    parser.add_argument('--question',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().parent!=(ROOT/'docs').resolve() or args.output.exists():
        parser.error('New docs output required')
    enable_local_model()
    client=StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],
        model='gpt-6-luna',reasoning='medium',http_headers=local_model_headers())
    calls=[];generate=client.generate
    def observed(*positional,**keywords):
        value=generate(*positional,**keywords)
        calls.append({'operation':keywords['name'],'output':value,'audit':client.audit})
        return value
    client.generate=observed
    store=KnowledgeStore(args.store,generator=GroundedGenerator(client))
    result,trace=route_visual_source_fallback(store,args.question,
        store.search(args.question,document_id=args.document),document_id=args.document)
    output={'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'development_diagnosis_not_accuracy_score','reference_opened':False,
        'calls':calls,'result':result,'trace':trace}
    with args.output.open('x',encoding='utf-8') as stream:json.dump(output,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'trace':trace['status'],'calls':[{k:c[k] for k in ('operation','output')} for c in calls]}))


if __name__=='__main__':main()
