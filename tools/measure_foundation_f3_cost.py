"""Paired same-environment production cost probe; no Gold or model calls."""
import argparse
import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc

from foundation_common import ROOT, dump, percentile, environment
from freeze_foundation_f2 import BENCH
from evaluate_foundation_f2 import raw_file


def main():
    p=argparse.ArgumentParser();p.add_argument('--backend-root',type=Path,required=True);p.add_argument('--output',required=True);a=p.parse_args()
    # evaluate_foundation_f2 imports the current backend: reload for a separate
    # baseline process before instantiating any store or measuring anything.
    for name in list(sys.modules):
        if name=='backend' or name.startswith('backend.'):del sys.modules[name]
    sys.path.insert(0,str(a.backend_root));KnowledgeStore=importlib.import_module('backend.knowledge_store').KnowledgeStore
    tasks=json.loads((BENCH/'dev.json').read_text());families={t['source_family'] for t in tasks};rows=[]
    with tempfile.TemporaryDirectory(prefix='f3-cost-') as temp:
        s=KnowledgeStore(temp)
        for d in json.loads((BENCH/'documents.json').read_text()):
            if d['family'] in families:s.ingest(raw_file(d),document_id=d['id'],title=d['title'],modality=d['type'],filename=d['id']+'.'+d['type'])
        for task in tasks:s.answer(task['query'])
        for task in tasks:
            times=[]
            for _ in range(3):
                t=time.perf_counter();s.answer(task['query']);times.append((time.perf_counter()-t)*1000)
            tracemalloc.start();base=tracemalloc.get_traced_memory()[0];result=s.answer(task['query']);_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
            b=result.get('evidence_bundle') or {}
            rows.append({'id':task['id'],'hot_query_ms':times,'python_peak_query_increment_bytes':peak-base,
                'bundle_serialized_bytes':len(json.dumps(b,ensure_ascii=False).encode()),'status':result['status']})
        index_bytes=sum(f.stat().st_size for f in Path(temp).glob('retrieval-index*'))
    times=[t for r in rows for t in r['hot_query_ms']]
    dump(a.output,{'environment':environment(),'backend_root':str(a.backend_root),'backend_loaded_from':importlib.import_module('backend.knowledge_store').__file__,
        'metrics':{'p50_ms':percentile(times,.5),'p95_ms':percentile(times,.95),'python_peak_query_p50_bytes':percentile([r['python_peak_query_increment_bytes'] for r in rows],.5),
        'python_peak_query_max_bytes':max(r['python_peak_query_increment_bytes'] for r in rows),'persistent_index_bytes':index_bytes},'records':rows,
        'policy':'same authored dev corpus; 3 warm repeats; tracemalloc separate from timings; includes existing answer navigation; generator not_run'})
    print(json.dumps({'p50_ms':percentile(times,.5),'p95_ms':percentile(times,.95),'index_bytes':index_bytes}))


if __name__=='__main__':main()
