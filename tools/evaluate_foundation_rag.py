"""Layered real KnowledgeStore retrieval evaluation. Gold is scoring-only."""
import argparse
from io import BytesIO
import json
import math
import resource
import tempfile
import time
from foundation_common import ROOT, dump, environment, sha, source_hashes, percentile
from backend.knowledge_store import KnowledgeStore

BENCH = ROOT / 'benchmarks/foundation_f1_20261010'


def populate(root, embedder=None):
    store = KnowledgeStore(root, embedder=embedder)
    for d in json.loads((BENCH/'documents.json').read_text()):
        if d['type'] == 'pdf':
            from reportlab.pdfgen import canvas
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
            out = BytesIO(); c = canvas.Canvas(out, invariant=1)
            for text in d['pages']:
                c.setFont('STSong-Light', 11)
                for i, line in enumerate(text.splitlines()):
                    # Short lines retain native row provenance; never use OCR fixture output.
                    for part in range(0, len(line), 80):
                        c.drawString(36, 760-i*55-part//80*15, line[part:part+80])
                c.showPage()
            c.save(); raw = out.getvalue()
        else: raw = d['text'].encode()
        store.ingest(raw, document_id=d['id'], title=d['title'], modality=d['type'], filename=d['id']+'.'+d['type'])
    return store


def evaluate(store, tasks, strategy):
    gold = json.loads((BENCH/'scoring.json').read_text())
    bodies = {r.document_id: r for r in store.records()}
    records = []
    for task in tasks:
        start = time.perf_counter()
        options = {'strategy': strategy} if strategy != 'flat' else {}
        hits, audit = store.search(task['query'], top_k=task['top_k'], with_audit=True, **options)
        wall = (time.perf_counter()-start)*1000
        expected = gold[task['id']]
        sources = list(dict.fromkeys(h.metadata['document_id'] for h in hits))
        found = {did: '\n'.join(bodies[h.document_id].content for h in hits if h.metadata['document_id'] == did)
                 for did in sources}
        spans = [(did, literal) for did, values in expected['evidence'].items() for literal in values]
        recalls = [literal in found.get(did, '') for did, literal in spans]
        relevant = [any(literal in bodies[h.document_id].content for literal in expected['evidence'].get(h.metadata['document_id'], []))
                    for h in hits]
        # Binary qrels: an annotated necessary span from its annotated source.
        all_relevant = sum(any(lit in r.content for lit in expected['evidence'].get(r.metadata['document_id'], [])) for r in bodies.values())
        dcg = sum(int(rel)/math.log2(i+2) for i, rel in enumerate(relevant))
        ideal = sum(1/math.log2(i+2) for i in range(min(task['top_k'], all_relevant)))
        source_valid = []
        for h in hits:
            d = store.document(h.metadata['document_id'])
            source_valid.append(d['sha256'] == h.metadata['source_sha256'] and
                any(c['chunk_id'] == h.document_id and c['source_locator'] == h.metadata['source_locator'] for c in d['chunks']))
        records.append({'id': task['id'], 'kind': task['kind'], 'query': task['query'],
            'hits': [h.to_dict() for h in hits], 'audit': audit, 'query_ms': wall,
            'source_recall_at_k': len(set(sources).intersection(expected['sources']))/len(expected['sources']) if expected['sources'] else None,
            'evidence_recall_at_k': sum(recalls)/len(recalls) if recalls else None,
            'mrr': next((1/(i+1) for i, rel in enumerate(relevant) if rel), 0) if spans else None,
            'ndcg': dcg/ideal if ideal else None,
            'irrelevant_source_ratio': sum(did not in expected['sources'] for did in sources)/len(sources) if sources else 0,
            'complete_annotated_evidence': all(recalls) if spans else None,
            'sha_locator_correct': all(source_valid), 'abstention_expected': expected['should_abstain'],
            'empty_retrieval': not hits, 'false_complete_claims': sum(h.metadata.get('evidence_selection', {}).get('semantic_sufficiency') == 'complete' for h in hits),
            'answer': {'status': 'not_run', 'end_to_end_correctness': 'not_run', 'fidelity': 'not_run',
                       'citation_correctness': 'not_run', 'false_refusal': 'not_run', 'reasonable_refusal': 'not_run'}})
    metrics = {}
    for key in ['source_recall_at_k','evidence_recall_at_k','mrr','ndcg','irrelevant_source_ratio']:
        values = [r[key] for r in records if r[key] is not None]
        metrics[key] = sum(values)/len(values) if values else None
    metrics.update(tasks=len(records), complete_annotated_evidence=sum(r['complete_annotated_evidence'] is True for r in records),
                   answerable_tasks=sum(not r['abstention_expected'] for r in records),
                   query_p50_ms=percentile([r['query_ms'] for r in records], .5),
                   query_p95_ms=percentile([r['query_ms'] for r in records], .95),
                   dense_executed=sum(r['audit'].get('dense_status') == 'enabled' for r in records),
                   sha_locator_correct=all(r['sha_locator_correct'] for r in records),
                   false_complete_claims=sum(r['false_complete_claims'] for r in records))
    return records, metrics


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True); p.add_argument('--split', choices=['dev','retained'], default='dev')
    p.add_argument('--strategy', choices=['flat','hierarchical'], default='flat')
    p.add_argument('--dense', action='store_true'); args = p.parse_args()
    frozen = json.loads((BENCH/'manifest.json').read_text())
    assert all(sha(ROOT/path) == digest for path, digest in frozen['files'].items())
    before = source_hashes()
    embedder = None
    if args.dense:
        from backend.dense_retrieval import LocalBgeEmbedder
        embedder = LocalBgeEmbedder(ROOT/'models/bge-small-zh-v1.5')
    tasks = json.loads((BENCH/(args.split+'.json')).read_text())
    with tempfile.TemporaryDirectory(prefix='f1-rag-') as root:
        t = time.perf_counter(); store = populate(root, embedder); build_ms = (time.perf_counter()-t)*1000
        t = time.perf_counter(); warm_count = store.warm_dense_index() if args.dense else 0
        warm_ms = (time.perf_counter()-t)*1000
        records, metrics = evaluate(store, tasks, args.strategy)
        report = {'environment': environment(), 'source_before': before, 'source_after': source_hashes(),
                  'input_manifest_sha256': sha(BENCH/'manifest.json'), 'scorer_sha256': sha(__file__),
                  'strategy': args.strategy, 'dense_requested': args.dense, 'split': args.split,
                  'build_ms': build_ms, 'dense_warm_ms': warm_ms, 'dense_warm_chunks': warm_count,
                  'rss_max_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  'model_generation_calls': 0, 'paid_calls': 0, 'tokens': 0, 'records': records, 'metrics': metrics,
                  'scope': 'synthetic_local_retrieval_not_real_generated_RAG_QA'}
        assert report['source_before'] == report['source_after']
        dump(args.output, report); print(json.dumps(metrics))


if __name__ == '__main__': main()
