"""Compare retained request audits for identical questions and native operations.

Descriptive observations only: different API calls, not controlled latency or
semantic-accuracy proof. No benchmark answer, score or source report is changed.
"""
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def load(path):
    raw=path.read_bytes();report=json.loads(raw)
    if (report.get('implementation_stable') is not True
            or report.get('gold_used_as_corpus_or_model_input') is not False
            or report.get('model')!='gpt-6-luna' or report.get('reasoning')!='medium'):
        raise ValueError('stable_reference_isolated_model_report_required')
    return report,hashlib.sha256(raw).hexdigest()


def operations(case):
    grouped=defaultdict(list)
    for audit in case.get('api_audits',[]):
        name=audit.get('operation','')
        if name.startswith(('native_row_selection','native_row_comparison')):
            grouped[name].append(audit)
    return grouped


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before',type=Path,required=True)
    parser.add_argument('--after',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists() or args.output.resolve().parent!=(ROOT/'docs').resolve():
        parser.error('New output directly in docs required')
    before,bsha=load(args.before);after,asha=load(args.after)
    old={c['ID']:c for c in before['cases']};new={c['ID']:c for c in after['cases']}
    if set(old)!=set(new) or before['store_path']!=after['store_path']:
        raise ValueError('same_questions_and_original_store_required')
    rows=[];unpaired=[]
    for identifier,a in old.items():
        b=new[identifier]
        if a['question']!=b['question']:raise ValueError('question_changed')
        opsa,opsb=operations(a),operations(b)
        for name in sorted(set(opsa)|set(opsb)):
            left,right=opsa.get(name,[]),opsb.get(name,[])
            if len(left)!=len(right):
                unpaired.append({'ID':identifier,'operation':name,'before_calls':len(left),'after_calls':len(right)})
                continue
            for index,(x,y) in enumerate(zip(left,right),1):
                if not all(z.get('status')=='completed' and z.get('model_verified') is True for z in (x,y)):
                    unpaired.append({'ID':identifier,'operation':name,'reason':'provider_call_not_completed'});continue
                rows.append({'ID':identifier,'operation':name,'occurrence':index,
                    'question_sha256':hashlib.sha256(a['question'].encode()).hexdigest(),
                    'before_input_tokens':x['input_tokens'],'after_input_tokens':y['input_tokens'],
                    'before_latency_ms':x['latency_ms'],'after_latency_ms':y['latency_ms'],
                    'before_cached_input_tokens':x.get('cached_input_tokens',0),
                    'after_cached_input_tokens':y.get('cached_input_tokens',0)})
    it_before=sum(r['before_input_tokens'] for r in rows);it_after=sum(r['after_input_tokens'] for r in rows)
    lat_before=sum(r['before_latency_ms'] for r in rows);lat_after=sum(r['after_latency_ms'] for r in rows)
    report={'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'same_exposed_question_operation_request_audits_not_controlled_latency_or_accuracy',
        'before_report_sha256':bsha,'after_report_sha256':asha,'paired_calls':rows,'unpaired_calls':unpaired,
        'summary':{'paired_native_calls':len(rows),'before_input_tokens':it_before,'after_input_tokens':it_after,
            'input_token_reduction_fraction':round(1-it_after/it_before,6) if it_before else None,
            'before_observed_latency_ms_sum':round(lat_before,3),'after_observed_latency_ms_sum':round(lat_after,3)},
        'limitations':['Existing reports are immutable; no new API requests or rescore.',
            'Different API calls and provider/cache/load conditions: no controlled speedup claim.',
            'Call alignment is original question ID, text and operation; unmatched calls retained separately.',
            'Input tokens include instructions and other context, not registry JSON alone.',
            'Factual correctness must use separate retained evaluation; this cannot award accuracy points.']}
    payload=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    with args.output.open('x',encoding='utf-8') as f:f.write(payload)
    print(json.dumps(report['summary']))


if __name__=='__main__':main()
