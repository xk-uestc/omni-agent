"""Versioned retrieval-index prototype must exactly preserve BM25 semantics."""
import hashlib
from concurrent.futures import ThreadPoolExecutor

from backend.cross_source import DocumentRecord, JsonDocumentRetriever
from backend.retrieval_index import PersistentBM25Index


def row(chunk, source, text, title='运行记录', family='甲方'):
    digest=hashlib.sha256((source+text).encode()).hexdigest()
    return DocumentRecord(chunk,title,text,'/original/'+source,{
        'document_id':source,'chunk_id':chunk,'source_sha256':digest,
        'source_locator':'page:1:block:1','page_no':1,'title_path':[family]})


def test_persistent_bm25_matches_dynamic_rank_and_evidence(tmp_path):
    records=[row('c1','d1','北区合格批次 R-17 读数 12.5','季度运行公报'),
             row('c2','d2','南区隔离批次 R-18 读数 31.5','季度运行公报'),
             row('c3','d3','北区合格批次 R-19 读数 19.5','历史公报')]
    index=PersistentBM25Index(tmp_path/'index.sqlite')
    index.statistics(records)
    for query in ['北区 合格 读数','季度运行公报 2026','R-19','没有命中的词']:
        dynamic=JsonDocumentRetriever(records).search(query,top_k=20,candidate_limit=100)
        indexed,status=index.search(query,records,top_k=20,candidate_limit=100)
        assert status=='persistent_index'
        assert [x.to_dict() for x in indexed]==[x.to_dict() for x in dynamic]


def test_rebuild_on_document_revision_and_delete_prevents_old_hits(tmp_path):
    index=PersistentBM25Index(tmp_path/'index.sqlite')
    old=row('old','d1','OLDREV17 唯一旧版 11.0')
    assert index.search('OLDREV17', [old],top_k=20)[1]=='persistent_index'
    new=row('new','d1','NEWREV22 现行版本 22.0')
    hits,status=index.search('OLDREV17',[new],top_k=20)
    assert status=='persistent_index' and index.last_rebuilt
    assert not hits
    hits,_=index.search('NEWREV22',[new],top_k=20)
    assert [x.document_id for x in hits]==['new']


def test_corrupt_index_falls_back_to_dynamic_without_losing_recall(tmp_path):
    path=tmp_path/'index.sqlite';path.write_bytes(b'not an sqlite database')
    rows=[row('c1','d1','校准周期 23 天')]
    hits,status=PersistentBM25Index(path).search('校准周期',rows,top_k=20)
    assert status.startswith('dynamic_fallback:')
    assert [x.document_id for x in hits]==['c1']
    assert path.read_bytes()==b'not an sqlite database'


def test_concurrent_readers_share_atomic_persistent_generation(tmp_path):
    path=tmp_path/'index.sqlite';index=PersistentBM25Index(path)
    records=[row(f'c{i}','d'+str(i),f'校准批次 {i} 结论合格') for i in range(40)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:index.search('校准 结论合格',records,top_k=20),range(12)))
    assert all(status=='persistent_index' for _,status in results)
    assert all([x.document_id for x in hits]==[x.document_id for x in results[0][0]] for hits,_ in results)
