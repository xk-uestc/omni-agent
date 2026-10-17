"""Run the frozen F2 evaluator through a persistent BM25 or SQLite FTS5 probe."""
import argparse
import json
from pathlib import Path
from foundation_common import ROOT, sha
from backend import knowledge_store
from backend.cross_source import JsonDocumentRetriever
from backend.retrieval_index import PersistentBM25Index, PersistentFTS5Index
from evaluate_foundation_f2 import run as run_frozen_scorer


def run(split,strategy,output):
    output=Path(output)
    if output.exists():raise FileExistsError(output)
    cls=PersistentBM25Index if strategy=='persistent' else PersistentFTS5Index
    original_init=knowledge_store.KnowledgeStore.__init__
    original_search=knowledge_store.KnowledgeStore.search
    original_retriever=knowledge_store.JsonDocumentRetriever
    managers=[]
    class IndexedRetriever(JsonDocumentRetriever):
        def __init__(self,records):
            self.documents=tuple(records)
            self.index=managers[-1]
            stats=self.index.statistics(records)
            self._document_frequency=stats['document_frequency']
            self._average_document_length=stats['average_length']
        def search(self,query,*,top_k=4,candidate_limit=20):
            return self.index.search(query,self.documents,top_k=top_k,candidate_limit=candidate_limit)[0]
    def init(store,root,**kwargs):
        kwargs['use_persistent_bm25']=strategy!='dynamic'
        original_init(store,root,**kwargs)
        manager=store.persistent_bm25 if strategy=='persistent' else cls(Path(root)/'f2-retrieval-index.sqlite')
        if strategy=='fts5':
            store.persistent_bm25=manager
        managers.append(manager)
        store._f2_manager=manager;store._f2_warmed=False
    def search(store,*args,**kwargs):
        if not store._f2_warmed:
            records=store.records()
            store._f2_manager.statistics(records)
            store._f2_warmed=True
        return original_search(store,*args,**kwargs)
    knowledge_store.KnowledgeStore.__init__=init
    knowledge_store.KnowledgeStore.search=search
    knowledge_store.JsonDocumentRetriever=IndexedRetriever
    try:run_frozen_scorer(split,output)
    finally:
        knowledge_store.KnowledgeStore.__init__=original_init
        knowledge_store.KnowledgeStore.search=original_search
        knowledge_store.JsonDocumentRetriever=original_retriever
    report=json.loads(output.read_text())
    manager=managers[-1]
    report['index_experiment']={'strategy':strategy,'schema_version':manager.__class__.__name__,
        'cold_build_ms':manager.last_build_ms,'last_index_status':manager.last_status,
        'index_bytes':manager.last_index_bytes,
        'fresh_process_reopen':'measured by new manager in separate lifecycle tests',
        'corruption_policy':'fall back to frozen dynamic BM25; never return unverified cached content',
        'harness_sha256':sha(__file__)}
    report['index_strategy']=strategy+'-persistent-retrieval-index'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'strategy':strategy,'metrics':report['metrics'],'index':report['index_experiment']}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--strategy',choices=['persistent','fts5'],required=True)
    p.add_argument('--split',choices=['dev','retained'],required=True);p.add_argument('--output',required=True)
    args=p.parse_args();run(args.split,args.strategy,args.output)
