"""Real offline SQL/document scale, concurrency and isolated fault measurements."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
import resource
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from foundation_common import ROOT, dump, environment, percentile, sha, source_hashes
from backend.knowledge_store import KnowledgeStore, SourceIntegrityError, SourceRevisionError
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.source_answer_dossier import build_dossier
from backend.omni_agent import OmniAgent
from backend.responses_client import GenerationError
from backend.session import ConversationStore


def timed(operation):
    start=time.perf_counter();value=operation();return value,(time.perf_counter()-start)*1000


def pdf(count):
    from reportlab.pdfgen import canvas
    buffer=BytesIO();c=canvas.Canvas(buffer,invariant=1)
    for i in range(count):
        for j in range(5):
            c.drawString(35,760-j*35,f'Field operating record page {i+1}, section {j+1}. Calibration service interval {10+(i%7)} days. '
                         'Status approved; budget 120 USD excluding tax.')
        c.showPage()
    c.save();return buffer.getvalue()


def document_measure(root,pages):
    s=KnowledgeStore(root);raw=pdf(pages)
    d,build=timed(lambda:s.ingest(raw,document_id='operations',title='Field record',modality='pdf',filename='r.pdf'))
    query='What is the field calibration service interval and budget?'
    cold,cold_ms=timed(lambda:s.search(query,with_audit=True))
    hits=cold[0]
    samples=[]
    for i in range(12):
        _,ms=timed(lambda:s.search(query,with_audit=True));samples.append(ms)
    _,verify_ms=timed(lambda:s.verify_source('operations',expected_sha256=d['sha256']))
    prepared,prepare_ms=timed(lambda:build_dossier(s,query,hits))
    answer,answer_ms=timed(lambda:s.answer(query))
    def request(_):
        h,ms=timed(lambda:s.search(query,with_audit=True))
        return {'ms':ms,'hits':len(h[0]),'source_sha_consistent':all(x.metadata['source_sha256']==d['sha256'] for x in h[0])}
    start=time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool: concurrent=list(pool.map(request,range(24)))
    seconds=time.perf_counter()-start
    restarted=KnowledgeStore(root);same=[h.document_id for h in restarted.search(query)]==[h.document_id for h in hits]
    changed,incremental_ms=timed(lambda:s.ingest(raw,document_id='new',title='New incremental source',modality='pdf',filename='new.pdf'))
    # Separate logical source replacement; old pins must fail.
    updated,update_ms=timed(lambda:s.ingest(pdf(1),document_id='operations',title='Field record',modality='pdf',filename='r.pdf'))
    revision_rejected=False
    if updated['sha256']!=d['sha256']:
        try: s.verify_source('operations',expected_sha256=d['sha256'])
        except SourceRevisionError: revision_rejected=True
    path,_=s.original('operations');path.write_bytes(b'fault injected only in disposable corpus')
    corrupt_rejected=False
    try:s.answer(query,document_id='operations')
    except SourceIntegrityError:corrupt_rejected=True
    return {'pages':pages,'source_sha256':d['sha256'],'source_bytes':len(raw),'chunks':d['chunk_count'],
            'index_build_ms':build,'cold_query_ms':cold_ms,'query_ms':samples,
            'query_p50_ms':statistics.median(samples),'query_p95_ms':percentile(samples,.95),
            'source_verification_ms':verify_ms,'answer_preparation_ms':prepare_ms,
            'prepared_pages':len(prepared[0]),'prepared_scope':prepared[1],
            'offline_answer_ms':answer_ms,'offline_answer_status':answer['status'],
            'offline_answer_mode':answer.get('answer_mode'),'generated_end_to_end':'not_run',
            'concurrency':{'workers':4,'requests':len(concurrent),'samples':concurrent,
                           'qps':len(concurrent)/seconds,'p50_ms':percentile([r['ms'] for r in concurrent],.5),
                           'p95_ms':percentile([r['ms'] for r in concurrent],.95),'failures':sum(not r['source_sha_consistent'] for r in concurrent)},
            'restart_identical_hits':same,'incremental_index_build_ms':incremental_ms,
            'replacement_ms':update_ms,'old_revision_rejected':revision_rejected,'corrupt_original_rejected':corrupt_rejected,
            'disk_bytes':sum(p.stat().st_size for p in Path(root).rglob('*') if p.is_file())}


def sql_measure(path,rows):
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE sales_orders (order_id INTEGER PRIMARY KEY, order_date TEXT, region TEXT, sales_amount REAL)')
        start=time.perf_counter()
        connection.executemany('INSERT INTO sales_orders VALUES (?,?,?,?)',
            ((i+1,'2025-06-15','华东' if i%2==0 else '华南',float(i%101)) for i in range(rows)))
        connection.commit();index_ms=(time.perf_counter()-start)*1000
    engine=Nl2SqlEngine(path);query='2025年华东销售额'
    first,cold=timed(lambda:engine.answer(query));values=[];success=[]
    expected=sum(float(i%101) for i in range(rows) if i%2==0)
    for i in range(12):
        r,ms=timed(lambda:engine.answer(query));values.append(ms)
        success.append(r.status=='ok' and len(r.rows)==1 and list(r.rows[0].values())==[expected])
    def request(_):
        r,ms=timed(lambda:engine.answer(query));return {'ms':ms,'passed':r.status=='ok' and list(r.rows[0].values())==[expected] if r.rows else False}
    start=time.perf_counter()
    with ThreadPoolExecutor(max_workers=4) as pool: concurrency=list(pool.map(request,range(24)))
    seconds=time.perf_counter()-start
    return {'rows':rows,'database_sha256':sha(path),'build_ms':index_ms,'cold_query_ms':cold,
            'query_ms':values,'p50_ms':statistics.median(values),'p95_ms':percentile(values,.95),
            'passed':sum(success),'requests':len(values),'disk_bytes':path.stat().st_size,
            'concurrency':{'workers':4,'qps':len(concurrency)/seconds,'samples':concurrency,
                           'failures':sum(not r['passed'] for r in concurrency)}}


def faults(root):
    results=[]
    for kind in ['missing_index','corrupt_database','store_lock']:
        s=KnowledgeStore(root/kind);s.ingest(b'calibration 17 days',document_id='p',title='Policy',modality='txt',filename='p.txt')
        locked=None
        if kind=='missing_index':
            with s.connect() as c:c.execute('DROP TABLE chunks')
        elif kind=='corrupt_database':s.database.write_bytes(b'not a database')
        else:
            locked=sqlite3.connect(s.database);locked.execute('BEGIN EXCLUSIVE')
        start=time.perf_counter();error=None
        try:s.search('calibration')
        except sqlite3.DatabaseError as exc:error=type(exc).__name__
        finally:
            if locked:locked.rollback();locked.close()
        results.append({'fault':kind,'detected':error is not None,'error_type':error,'wall_ms':(time.perf_counter()-start)*1000,
                        'scope':'disposable_fixture_only'})
    class Unavailable:
        def generate(self,*args,**kwargs):raise GenerationError('fixture unavailable',status=503)
    engine=Nl2SqlEngine(initialize_database(root/'model.sqlite'))
    s=KnowledgeStore(root/'model-k');agent=OmniAgent(engine,s,ConversationStore(),Unavailable())
    response=agent.query('请从数据库查销售额再根据结果检索文档')
    results.append({'fault':'model_service_unavailable','status':response['status'],
                    'failure_trace':response['failure_trace'],'actual_remote_calls':0})
    return results


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    before=source_hashes();records=[]
    with tempfile.TemporaryDirectory(prefix='f1-perf-') as temp:
        root=Path(temp)
        for pages in [10,100,500]:records.append(document_measure(root/f'pages-{pages}',pages))
        sql=[sql_measure(root/f'rows-{n}.sqlite',n) for n in [1000,10000,100000]]
        fault=faults(root/'faults')
    report={'environment':environment(),'source_before':before,'source_after':source_hashes(),
            'script_sha256':sha(__file__),'documents':records,'sql':sql,'faults':fault,
            'rss_max_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'paid_calls':0,'model_generation_calls':0,'tokens':0,
            'scope':'real_local_paths_synthetic_isolated_data_not_model_RAG_QA','clock':'perf_counter nearest-rank P95, 12 warm requests, nonexclusive environment'}
    assert report['source_before']==report['source_after']
    dump(args.output,report)
    print(json.dumps({'pages':[{'n':r['pages'],'p50':r['query_p50_ms'],'p95':r['query_p95_ms']} for r in records],
                      'sql':[{'n':r['rows'],'passed':r['passed'],'p50':r['p50_ms'],'p95':r['p95_ms']} for r in sql],
                      'faults':[{'fault':f['fault'],'detected':f.get('detected')} for f in fault]}))


if __name__=='__main__':main()
