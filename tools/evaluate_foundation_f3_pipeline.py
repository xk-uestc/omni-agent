"""F3 production answer/preparation receipts; frozen literals, source-bound scoring.

Top-K scores still come from the unchanged F2 scorer in a separate run. No model
provider is configured; neither prepared evidence nor stub reviews are answers.
"""
import argparse
from collections import defaultdict
import json
import resource
import tempfile
import time

from foundation_common import ROOT, dump, environment, percentile, sha, source_hashes
from freeze_foundation_f2 import BENCH
from evaluate_foundation_f2 import raw_file
from backend.knowledge_store import KnowledgeStore
from backend.source_answer_dossier import build_dossier


def run(split, output):
    manifest=BENCH/'manifest.json'
    assert all(sha(ROOT/p)==h for p,h in json.loads(manifest.read_text())['files'].items())
    before=source_hashes(); tasks=json.loads((BENCH/f'{split}.json').read_text())
    families={t['source_family'] for t in tasks}; receipts=[]; timings=[]
    with tempfile.TemporaryDirectory(prefix='f3-pipeline-') as temp:
        store=KnowledgeStore(temp); rss_before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        for d in json.loads((BENCH/'documents.json').read_text()):
            if d['family'] in families:
                store.ingest(raw_file(d),document_id=d['id'],title=d['title'],modality=d['type'],filename=d['id']+'.'+d['type'])
        index_bytes=sum(p.stat().st_size for p in store.root.glob('retrieval-index*'))
        records={r.document_id:r for r in store.records()}
        for task in tasks:
            # Actual answer entry, including existing question-part navigation.
            calls=[]; reads=[]; initial_hits=[]
            real_search=store.search; real_prepare=store.prepare_evidence
            def counted_search(*args,**kwargs):
                calls.append({'query':args[0], 'top_k':kwargs.get('top_k',4),'document_id':kwargs.get('document_id')})
                found=real_search(*args,**kwargs)
                if len(calls)==1:
                    initial_hits.extend(found[0] if isinstance(found,tuple) else found)
                return found
            def counted_prepare(*args,**kwargs):
                b=real_prepare(*args,**kwargs);reads.append(b['extra_native_page_reads']);return b
            store.search=counted_search;store.prepare_evidence=counted_prepare
            start=time.perf_counter();result=store.answer(task['query'],top_k=task['top_k']); elapsed=(time.perf_counter()-start)*1000
            cold_calls=list(calls);cold_reads=list(reads); bundle=result.get('evidence_bundle')
            text=defaultdict(str)
            # Rehydrate actual chunk bodies, not audit snippets or cross-source
            # concatenation. Additional native pages remain a distinct channel.
            for citation in result.get('citations',[]):
                cid=citation.get('document_id')
                if cid in records:text[citation['metadata']['document_id']]+='\n'+records[cid].content
            prepared_text=defaultdict(str,text)
            topk_prepared_text=defaultdict(str)
            for hit in initial_hits:
                topk_prepared_text[hit.metadata['document_id']]+='\n'+records[hit.document_id].content
            for p in (bundle or {}).get('pages',[]):prepared_text[p['document_id']]+='\n'+p['text']
            for p in (bundle or {}).get('pages',[]):topk_prepared_text[p['document_id']]+='\n'+p['text']
            dossier_receipt=None
            if bundle and bundle['status']=='complete_for_declared_scope':
                hits=real_search(task['query'],top_k=task['top_k'])
                pages,scope=build_dossier(store,task['query'],hits,evidence_bundle=real_prepare(task['query'],hits))
                dossier_receipt={'pages':len(pages),'chars':scope['native_chars'],'scope':scope['scope'],
                    'replay_extra_reads':scope['scanned_pages'], 'matching_rows':len(bundle['matching_rows'])}
            # Three warm production queries, preserving all cost counters.
            hot=[]
            for _ in range(3):
                tick=time.perf_counter();store.answer(task['query'],top_k=task['top_k']);hot.append((time.perf_counter()-tick)*1000)
            timings+=hot
            store.search=real_search;store.prepare_evidence=real_prepare
            receipts.append({'id':task['id'],'kind':task['kind'],'query':task['query'],
                'source_text':dict(text),'expanded_source_text':dict(prepared_text),
                'topk_plus_bundle_source_text':dict(topk_prepared_text),
                'status':result['status'],'answer_mode':result['answer_mode'],
                'production_trace':result.get('trace'), 'bundle':bundle,'dossier_preparation':dossier_receipt,
                'retrieval_call_count':len(cold_calls),'retrieval_calls':cold_calls,'native_page_reads':sum(cold_reads),
                'query_ms':elapsed,'hot_query_ms':hot,'generator_visible_chars':0,'answer_metrics':'not_run',
                'prepared_generator_chars':(bundle or {}).get('native_chars',sum(map(len,text.values()))),
                'rejected_bundle':bool(bundle and bundle['status'] not in ('complete_for_declared_scope','not_applicable'))})
        index_after=sum(p.stat().st_size for p in store.root.glob('retrieval-index*'))
        rss_after=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Read frozen Gold only after production results and receipts are complete.
    gold=json.loads((BENCH/'scoring.json').read_text())
    for r in receipts:
        spans=[(did,literal) for did,literals in gold[r['id']]['required'].items() for literal in literals]
        recovered=[literal in r['expanded_source_text'].get(did,'') for did,literal in spans]
        topk_recovered=[literal in r['topk_plus_bundle_source_text'].get(did,'') for did,literal in spans]
        r.update(required_count=len(spans),recovered_count=sum(recovered),
            expanded_evidence_recall=sum(recovered)/len(spans) if spans else None,
            expanded_complete=all(recovered) if spans else None,expected_abstain=gold[r['id']]['expected_abstain'])
        r.update(topk_plus_bundle_recall=sum(topk_recovered)/len(spans) if spans else None,
                 topk_plus_bundle_complete=all(topk_recovered) if spans else None)
        task=next(t for t in tasks if t['id']==r['id']);decoys=set(task.get('wrong_binding_sources',[]))
        r['expanded_wrong_binding']=bool(decoys.intersection(r['expanded_source_text']))
        r['erroneous_empty_evidence']=bool(spans and not any(r['expanded_source_text'].values()))
    valued=[r for r in receipts if r['expanded_evidence_recall'] is not None]
    summary={'tasks':len(receipts),'answerable':len(valued),
        'expanded_evidence_recall':sum(r['expanded_evidence_recall'] for r in valued)/len(valued),
        'expanded_complete':sum(r['expanded_complete'] is True for r in receipts),
        'topk_plus_bundle_recall':sum(r['topk_plus_bundle_recall'] for r in valued)/len(valued),
        'topk_plus_bundle_complete':sum(r['topk_plus_bundle_complete'] is True for r in receipts),
        'expanded_wrong_binding':sum(r['expanded_wrong_binding'] for r in receipts),
        'table_bundle_complete':sum((r['bundle'] or {}).get('status')=='complete_for_declared_scope' for r in receipts),
        'erroneous_empty_evidence':sum(r['erroneous_empty_evidence'] for r in receipts),
        'hot_query_p50_ms':percentile(timings,.5),'hot_query_p95_ms':percentile(timings,.95),
        'retrieval_call_counts':[r['retrieval_call_count'] for r in receipts],
        'native_page_read_counts':[r['native_page_reads'] for r in receipts],
        'prepared_generator_chars_p50':percentile([r['prepared_generator_chars'] for r in receipts],.5),
        'prepared_generator_chars_max':max(r['prepared_generator_chars'] for r in receipts),
        'generator_visible_chars':0,'answer_metrics':'not_run',
        'rss_peak_increment_kib':rss_after-rss_before,'index_bytes_before_queries':index_bytes,'index_bytes_after_queries':index_after,
        'persistent_algorithm_index_increment_bytes':0}
    assert before==source_hashes()
    dump(output,{'environment':environment(),'split':split,'manifest_sha256':sha(manifest),'source_hashes':before,
        'scoring_policy':'unchanged frozen exact literal matching, bound to required source; expanded distinct from Top-K',
        'cost_policy':'answer calls counted; separate dossier reconstruction cost reported per task; 3 warm repeats',
        'metrics':summary,'records':receipts,'claim':'authored synthetic tasks, not public benchmark; no generator calls'})
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--split',choices=['dev','retained'],required=True);p.add_argument('--output',required=True)
    a=p.parse_args();run(a.split,a.output)
