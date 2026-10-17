"""Reproduce the scoped storage probe on current real KnowledgeStore."""
import argparse
import tempfile
import time
from foundation_common import dump, source_hashes, environment
from backend.knowledge_store import KnowledgeStore

p = argparse.ArgumentParser(); p.add_argument('--output', required=True); args = p.parse_args()
before = source_hashes()
with tempfile.TemporaryDirectory(prefix='f1-storage-') as root:
    s = KnowledgeStore(root); start = time.perf_counter()
    for i in range(100):
        s.ingest(('Annual operations report\n\n'+(('Service uptime and repair incident statistics for department '+str(i)+'.\n')*60)).encode(),document_id=f'doc{i}',title=f'Operations {i}',modality='txt',filename='r.txt')
    build = (time.perf_counter()-start)*1000
    values=[]
    for _ in range(8):
        start=time.perf_counter();s.search('What are the repair incident statistics?',document_id='doc1');values.append((time.perf_counter()-start)*1000)
    dump(args.output, {'environment':environment(),'source_before':before,'source_after':source_hashes(),
                      'documents':100,'chunks':len(s.records()),'scoped_chunks':len(s.records('doc1')),
                      'build_ms':build,'scoped_query_ms':values,'generation_calls':0})
print(values)
