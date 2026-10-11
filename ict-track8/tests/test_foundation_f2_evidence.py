"""F2 checks for bounded repeated-header table continuation and row closure."""
from io import BytesIO

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from tools.evaluate_foundation_f2_evidence import read_table_chain


def make_source(*, duplicate=False, changed=False):
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    out=BytesIO();doc=canvas.Canvas(out,invariant=1)
    for page_no, ids in enumerate((['E-17-01','E-17-02'], ['E-17-03','E-17-04']), start=1):
        doc.setFont('STSong-Light',10)
        doc.drawString(40,790,'批次验收记录' if page_no==1 else '批次验收记录（续）')
        doc.drawString(40,770,'编号 | 区域 | 结论 | 读数(毫克/升) | 日期')
        for i, row_id in enumerate(ids):
            if duplicate and page_no==2 and i==0: row_id='E-17-02'
            area='北区' if row_id.endswith(('02','04')) else '南区'
            status='合格' if area=='北区' else '隔离'
            reading=99.5 if changed and page_no==1 and i==1 else i+10.5
            doc.drawString(40,745-i*20,f'{row_id} | {area} | {status} | {reading} | 2026-04-0{page_no}')
        if page_no==2: doc.drawString(40,680,'注：隔离记录不纳入合格样本统计。')
        doc.showPage()
    doc.save();return out.getvalue()


def test_repeated_header_chain_closes_rows_and_pins_source(tmp_path):
    store=KnowledgeStore(tmp_path/'knowledge')
    document=store.ingest(make_source(),document_id='table',title='Batch',modality='pdf',filename='batch.pdf')
    question='E-17批次北区合格记录'
    hits=store.search(question,top_k=4)
    result=read_table_chain(store,hits,question,page_budget=6)
    assert result['status']=='complete_for_declared_scope'
    assert result['source_sha256']==document['sha256']
    assert result['scanned_pages']==2 and result['row_scope']['global_source_closure'] is False
    assert len(result['row_scope']['matching_row_ids'])==2
    assert result['scope']['semantic_sufficiency']=='not_proved_by_page_coverage'
    assert [row['row_id'] for row in result['rows']]==['E-17-01','E-17-02','E-17-03','E-17-04']
    assert [row['row_id'] for row in result['rows'] if row['area']=='北区' and row['status']=='合格']==['E-17-02','E-17-04']


def test_page_budget_exhaustion_prevents_false_table_closure(tmp_path):
    store=KnowledgeStore(tmp_path/'knowledge')
    store.ingest(make_source(),document_id='table',title='Batch',modality='pdf',filename='batch.pdf')
    question='E-17批次北区合格记录'
    result=read_table_chain(store,store.search(question,top_k=4),question,page_budget=1)
    assert result['status']=='budget_exhausted'
    assert len(result['scope']['unscanned_pages'])==1


def test_duplicate_native_row_identity_fails_closed(tmp_path):
    store=KnowledgeStore(tmp_path/'knowledge')
    store.ingest(make_source(duplicate=True),document_id='table',title='Batch',modality='pdf',filename='batch.pdf')
    question='E-17批次北区合格记录'
    try:
        read_table_chain(store,store.search(question,top_k=4),question,page_budget=6)
    except ValueError as exc:
        assert 'duplicate_row_identity' in str(exc)
    else:
        raise AssertionError('duplicate source row ID must not be treated as a complete inventory')


def test_table_chain_refuses_a_source_replaced_after_retrieval(tmp_path):
    store=KnowledgeStore(tmp_path/'knowledge')
    store.ingest(make_source(),document_id='table',title='Batch',modality='pdf',filename='batch.pdf')
    question='E-17批次北区合格记录';hits=store.search(question,top_k=4)
    store.ingest(make_source(changed=True),document_id='table',title='Batch',modality='pdf',filename='batch.pdf')
    try:
        read_table_chain(store,hits,question,page_budget=6)
    except SourceRevisionError:
        pass
    else:
        raise AssertionError('old retrieval hits must not bind to a replacement source')
