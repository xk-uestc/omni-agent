"""Independent frozen F2 retrieval/evidence scoring on real parser outputs."""
import argparse
from io import BytesIO
import json
import math
import resource
import tempfile
import time
from collections import Counter
from foundation_common import ROOT, dump, environment, sha, source_hashes, percentile
from backend.knowledge_store import KnowledgeStore
from freeze_foundation_f2 import BENCH


def raw_file(doc):
    if doc['type'] != 'pdf':
        return doc['text'].encode()
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    out = BytesIO()
    c = canvas.Canvas(out, invariant=1)
    for page in doc['pages']:
        c.setFont('STSong-Light', 9)
        for i, line in enumerate(page.splitlines()):
            for part in range(0, len(line), 85):
                c.drawString(28, 770 - i*22 - part//85*12, line[part:part+85])
        c.showPage()
    c.save()
    return out.getvalue()


def run(split, output, bench=BENCH):
    manifest_path=bench/'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        assert all(sha(ROOT/path) == value for path, value in manifest['files'].items())
    before = source_hashes()
    tasks = json.loads((bench/f'{split}.json').read_text())
    task_families = {x['source_family'] for x in tasks}
    all_docs = json.loads((bench/'documents.json').read_text())
    docs = [d for d in all_docs if d['family'] in task_families]
    gold = json.loads((bench/'scoring.json').read_text())
    records = []
    with tempfile.TemporaryDirectory(prefix='f2-rag-') as temp:
        store = KnowledgeStore(temp)
        t = time.perf_counter()
        source_bytes = {}
        for d in docs:
            raw = raw_file(d)
            source_bytes[d['id']] = raw
            store.ingest(raw, document_id=d['id'], title=d['title'], modality=d['type'], filename=d['id']+'.'+d['type'])
        build_ms = (time.perf_counter()-t)*1000
        all_chunks = {x.document_id: x for x in store.records()}
        for task in tasks:
            started = time.perf_counter()
            hits, audit = store.search(task['query'], top_k=task['top_k'], with_audit=True)
            elapsed = (time.perf_counter()-started)*1000
            expected = gold[task['id']]
            ids = [h.metadata['document_id'] for h in hits]
            unique = list(dict.fromkeys(ids))
            hit_text = {docid: '\n'.join(all_chunks[h.document_id].content for h in hits
                            if h.metadata['document_id'] == docid) for docid in unique}
            required = expected['required']
            spans = [(docid, literal) for docid, values in required.items() for literal in values]
            recovered = [literal in hit_text.get(docid, '') for docid, literal in spans]
            relevant_ranks = [i+1 for i,h in enumerate(hits)
                if any(literal in all_chunks[h.document_id].content
                       for literal in required.get(h.metadata['document_id'], []))]
            binary = [i+1 in relevant_ranks for i in range(len(hits))]
            dcg = sum(int(v)/math.log2(i+2) for i,v in enumerate(binary))
            total_relevant = sum(any(lit in rec.content for lit in values)
                for rec in all_chunks.values() for docid, values in required.items()
                if rec.metadata['document_id'] == docid)
            ideal = sum(1/math.log2(i+2) for i in range(min(task['top_k'],total_relevant)))
            source_gold = set(required)
            decoys = set(task.get('wrong_binding_sources', []))
            wrong = sorted(set(unique).intersection(decoys))
            pins = []
            for h in hits:
                d = store.document(h.metadata['document_id'])
                pins.append(d['sha256'] == h.metadata['source_sha256'] and
                    any(c['chunk_id'] == h.document_id and c['source_locator'] == h.metadata['source_locator'] for c in d['chunks']))
            records.append({'id':task['id'],'family':task['source_family'],'kind':task['kind'],
                'query':task['query'],'gold_sources':sorted(source_gold),'retrieved_sources':unique,
                'hit_chunk_ids':[h.document_id for h in hits], 'hit_pages':[h.metadata.get('page_no') for h in hits],
                'hit_evidence':{docid:hit_text[docid] for docid in unique}, 'audit':audit,'query_ms':elapsed,
                'source_recall_at_k':len(source_gold.intersection(unique))/len(source_gold) if source_gold else None,
                'evidence_recall_at_k':sum(recovered)/len(recovered) if recovered else None,
                'required_evidence_count':len(spans),'recovered_evidence_count':sum(recovered),
                'complete_required_evidence':all(recovered) if recovered else None,
                'mrr':1/min(relevant_ranks) if relevant_ranks else 0 if spans else None,
                'ndcg':dcg/ideal if ideal else None,
                'wrong_entity_source_binding':bool(wrong),'wrong_binding_sources':wrong,
                'sha_locator_correct':all(pins),'expected_abstain':expected['expected_abstain'],
                'retrieval_empty':not hits,
                'unsupported_retrieval_contamination': bool(expected['expected_abstain'] and hits),
                'answer_metrics':{'end_to_end_correctness':'not_run','fidelity':'not_run','citation_correctness':'not_run'}})
        query_times=[r['query_ms'] for r in records]
        ans=[r for r in records if r['source_recall_at_k'] is not None]
        def mean(key):
            values=[r[key] for r in ans if r[key] is not None]
            return sum(values)/len(values) if values else None
        summary={'task_count':len(records),'source_family_count':len(task_families),'document_count':len(docs),
            'chunk_count':len(all_chunks),'source_recall_at_k':mean('source_recall_at_k'),
            'evidence_recall_at_k':mean('evidence_recall_at_k'),'mrr':mean('mrr'),'ndcg':mean('ndcg'),
            'complete_required_evidence':sum(r['complete_required_evidence'] is True for r in ans),
            'wrong_entity_source_binding':sum(r['wrong_entity_source_binding'] for r in records),
            'unsupported_retrieval_contamination':sum(r['unsupported_retrieval_contamination'] for r in records),
            'sha_locator_correct':all(r['sha_locator_correct'] for r in records),
            'query_p50_ms':percentile(query_times,.5),'query_p95_ms':percentile(query_times,.95),
            'answer_metrics':'not_run'}
        report={'environment':environment(),'split':split,'source_families':sorted(task_families),
            'source_before':before,'source_after':source_hashes(),'input_manifest_sha256':sha(manifest_path) if manifest_path.exists() else None,
            'scorer_sha256':sha(__file__),'index_strategy':'no-change-dynamic-BM25','build_ms':build_ms,
            'corpus_bytes':sum(map(len,source_bytes.values())),'rss_max_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'cpu_seconds':time.process_time(),'metrics':summary,'records':records,
            'claim':'authored synthetic parser-real retrieval/evidence test; not public or official QA benchmark'}
        assert before == source_hashes()
        dump(output,report)
        print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--split',choices=['dev','retained'],required=True);p.add_argument('--output',required=True)
    p.add_argument('--input-dir',type=lambda x:__import__('pathlib').Path(x),default=BENCH,help='for pre-freeze dev exploration only')
    a=p.parse_args();run(a.split,a.output,a.input_dir)
