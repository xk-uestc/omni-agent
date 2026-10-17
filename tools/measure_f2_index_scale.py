"""Same-corpus full KnowledgeStore A/B scale test for F2 index candidates."""
import json
import argparse
import tempfile
import time
import statistics
import resource
from pathlib import Path
from foundation_common import ROOT, dump, environment, percentile, sha, source_hashes
from backend.knowledge_store import KnowledgeStore
from measure_foundation_performance import pdf


def timed(fn):
    t=time.perf_counter();value=fn();return value,(time.perf_counter()-t)*1000


def benchmark(root,pages,mode):
    store=KnowledgeStore(root,use_persistent_bm25=(mode=='persistent'));index=store.persistent_bm25
    raw=pdf(pages)
    doc,ingest_ms=timed(lambda:store.ingest(raw,document_id='scale',title='Field operating record',modality='pdf',filename='scale.pdf'))
    query='What is the field calibration service interval and approved operating budget?'
    cold,first_ms=timed(lambda:store.search(query,with_audit=True))
    warm=[];results=[]
    for _ in range(12):
        value,ms=timed(lambda:store.search(query,with_audit=True));warm.append(ms)
        results.append([(h.document_id,h.metadata['source_sha256'],h.metadata['source_locator']) for h in value[0]])
    restarted=KnowledgeStore(root,use_persistent_bm25=(mode=='persistent'))
    if mode=='persistent':
        stats,load_ms=timed(lambda:restarted.persistent_bm25.statistics(restarted.records()))
        reopen={'loaded_statistics':stats['document_count'],'load_ms':load_ms}
    else:reopen={'loaded_statistics':None,'load_ms':None}
    second,second_ms=timed(lambda:restarted.search(query,with_audit=True))
    same=[h.document_id for h in second[0]]==[h.document_id for h in cold[0]]
    index_bytes=(index.path.stat().st_size if mode=='persistent' and index.path.exists() else 0)
    return {'pages':pages,'chunks':doc['chunk_count'],'mode':mode,'source_bytes':len(raw),
        'ingest_ms':ingest_ms,'cold_query_ms':first_ms,'query_ms':warm,'p50_ms':statistics.median(warm),
        'p95_ms':percentile(warm,.95),'ranking_identical_across_warm_runs':len(set(tuple(r) for r in results))==1,
        'restart_same_hits':same,'restart_first_query_ms':second_ms,'index_cold_build_ms':index.last_build_ms if mode=='persistent' else 0,
        'index_reopen':reopen,'index_bytes':index_bytes,'returned_chunks':len(cold[0]),
        'source_sha_locator_pinned':all(x[1]==doc['sha256'] for x in results[0])}


def main(output='docs/foundation/runs/f2-index-scale-after.json'):
    before=source_hashes();out=[]
    with tempfile.TemporaryDirectory(prefix='f2-index-scale-') as t:
        for pages in [10,100,500,1000]:
            for mode in ['dynamic','persistent']:
                out.append(benchmark(Path(t)/f'{pages}-{mode}',pages,mode))
    report={'environment':environment(),'evaluated_source_commit':__import__('subprocess').check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'source_before':before,'source_after':source_hashes(),'script_sha256':sha(__file__),'pairs':out,
        'rss_max_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'paid_calls':0,'generation_calls':0,
        'scope':'synthetic native PDF parser, actual KnowledgeStore; local timings not official benchmark or generated QA'}
    assert before==source_hashes()
    dump(output,report)
    print(json.dumps([{'pages':x['pages'],'mode':x['mode'],'p50':round(x['p50_ms'],2),'p95':round(x['p95_ms'],2),
        'cold_build':round(x['index_cold_build_ms'],2),'index_bytes':x['index_bytes']} for x in out]))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='docs/foundation/runs/f2-index-scale-after.json')
    main(parser.parse_args().output)
