"""Real OHR originals -> production KnowledgeStore -> frozen post-hoc scoring.

No generator or planner model. Answer accuracy is explicitly not_run.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from evaluate_ohr_bench import doc_id, retrieval_scores


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--split', choices=('dev', 'retained'), default='dev')
    p.add_argument('--allow-retained', action='store_true')
    p.add_argument('--native-ocr', action='store_true')
    args = p.parse_args()
    if args.split=='retained' and not args.allow_retained:
        p.error('Retained evaluation requires frozen implementation')
    if args.output.exists():
        p.error('Do not overwrite a run')
    args.output.mkdir(parents=True)
    m = json.loads((args.root/'ohr-manifest.json').read_text())
    docs = [d for d in m['documents'] if d['split']==args.split]
    queries = [q for q in m['cases'] if q['split']==args.split]
    pipeline = None
    if args.native_ocr:
        from backend.ocr import OcrPipeline, RapidOcrExecutor
        pipeline = OcrPipeline(RapidOcrExecutor())
    store = KnowledgeStore(args.output/'knowledge', ocr_pipeline=pipeline, generator=None)
    ingests = []
    for d in docs:
        raw = (args.root/d['pdf_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=d['pdf_sha256']:
            raise ValueError('frozen_source_changed')
        t = time.perf_counter()
        record = store.ingest(raw, document_id=doc_id(d['doc_name']), title=d['doc_name'][-200:],
                              modality='pdf', filename=Path(d['pdf_path']).name)
        ingests.append({'doc_name':d['doc_name'], 'sha256':d['pdf_sha256'], 'seconds':time.perf_counter()-t,
                        'chunks':record['chunk_count'], 'stats':record['stats']})
    predictions = []
    for q in queries:
        t = time.perf_counter()
        hits = store.search(q['questions'], top_k=4)
        cold = time.perf_counter()-t
        warm = []
        for _ in range(3):
            t = time.perf_counter(); store.search(q['questions'], top_k=4); warm.append(time.perf_counter()-t)
        predictions.append({'ID':q['ID'], 'question':q['questions'], 'hits':[h.to_dict() for h in hits],
                            'cold_query_seconds':cold, 'warm_query_seconds':warm})
    # Predictions are fixed on disk BEFORE reference answers/evidence are opened.
    (args.output/'predictions.json').write_text(json.dumps(predictions,ensure_ascii=False,indent=2))
    from backend.cross_source import DocumentHit
    raw = (args.root/'gold/OHR-qas_v2.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=m['qa_sha256']:
        raise ValueError('frozen_gold_changed')
    gold = {q['ID']:q for q in json.loads(raw)}
    for result in predictions:
        q = gold[result['ID']]
        expected = next(x for x in queries if x['ID']==result['ID'])
        if hashlib.sha256(json.dumps(q,ensure_ascii=False,sort_keys=True).encode()).hexdigest()!=expected['original_row_sha256']:
            raise ValueError('frozen_row_changed')
        hits = [DocumentHit(**h) for h in result['hits']]
        result['scores'] = retrieval_scores(hits,q)
    n = len(predictions)
    summary = {k:sum(float(r['scores'][k]) for r in predictions)/n for k in
               ('document_hit','any_evidence_page_hit','all_evidence_pages_hit','evidence_text_lcs_recall','gold_answer_lexically_present')}
    report = {'split':args.split, 'native_ocr':args.native_ocr,'documents':ingests, 'cases':predictions,
              'summary':summary, 'top_k':4, 'generation_answer_accuracy':'not_run',
              'gold_used_as_corpus_or_parser_input':False, 'scope':'new_frozen_resource_bounded_OHR_subset_not_leaderboard'}
    (args.output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    main()
