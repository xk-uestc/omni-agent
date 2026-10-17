"""Replay production bounded evidence and costs; no generator or Gold input."""
import argparse
import hashlib
import json
import resource
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ict-track8'))
from backend.knowledge_store import KnowledgeStore


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--root',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists():p.error('Preserve prior run')
    predictions=json.loads((args.run/'predictions.json').read_text())
    store=KnowledgeStore(args.run/'knowledge',generator=None)
    cases=[];times=[]
    for q in predictions:
        hits=store.search(q['question'],top_k=4)
        for _ in range(5):
            t=time.perf_counter();store.search(q['question'],top_k=4);times.append((time.perf_counter()-t)*1000)
        citations=[{'citation_id':i+1,**h.to_dict()} for i,h in enumerate(hits)]
        prepared,omitted=store._generation_citations(citations)
        cases.append({'ID':q['ID'],'omitted':omitted,'prepared_chars':sum(len(c['generation_evidence']['text']) for c in prepared),
                      'prepared_hits':[{**{k:c[k] for k in ['document_id','title','source_uri','score','matched_terms','metadata']},
                                        'snippet':c['generation_evidence']['text']} for c in prepared]})
    tracemalloc.start();store.records();_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    artifacts={'cases':cases,'generation_calls':0,'generation_answer_accuracy':'not_run'}
    artifact=args.output.with_suffix('.predictions.json');artifact.write_text(json.dumps(artifacts,ensure_ascii=False,indent=2))
    # Prepared observations exist on disk before reference QA is read.
    from evaluate_ohr_bench import retrieval_scores
    from backend.cross_source import DocumentHit
    qa_raw=(args.root/'gold/OHR-qas_v2.json').read_bytes()
    manifest=json.loads((args.root/'ohr-manifest.json').read_text())
    if hashlib.sha256(qa_raw).hexdigest()!=manifest['qa_sha256']:raise ValueError('gold_changed')
    gold={q['ID']:q for q in json.loads(qa_raw)}
    for case in cases:
        case['prepared_evidence_scores']=retrieval_scores([DocumentHit(**h) for h in case.pop('prepared_hits')],gold[case['ID']])
    times.sort()
    report={'cases':cases,'generation_answer_accuracy':'not_run','generation_calls':0,
       'predictions_sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),
       'warm_query_p50_ms':statistics.median(times),'warm_query_p95_ms':times[int(.95*(len(times)-1))],
       'prepared_evidence_lcs_recall':statistics.mean(c['prepared_evidence_scores']['evidence_text_lcs_recall'] for c in cases),
       'prepared_chars_p50':statistics.median(c['prepared_chars'] for c in cases),'prepared_chars_max':max(c['prepared_chars'] for c in cases),
       'records_python_allocation_peak_bytes':peak,'process_peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
       'store_bytes':{f.name:f.stat().st_size for f in store.root.glob('*.sqlite')},'native_evidence_replay_not_generator_input':True}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='cases'}))


if __name__=='__main__':main()
