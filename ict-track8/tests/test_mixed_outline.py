from backend.knowledge_store import KnowledgeStore


def test_mixed_outlines_keep_nearest_heading_and_line_evidence(tmp_path):
    store=KnowledgeStore(tmp_path)
    raw='# 服务手册\n## 售后\n签收后7日内退货。\n#### 保修\n免费维修12个月。\n【风险提示】\n未知损坏需人工确认。\n第一章 其他\n1.1 支持\n紧急工单2小时响应。'.encode()
    record=store.ingest(raw,document_id='mixed',title='混合目录',modality='md',filename='m.md')
    chunks=store.document('mixed')['chunks']
    warranty=next(chunk for chunk in chunks if '免费维修' in chunk['text'])
    assert warranty['title_path']==['# 服务手册','## 售后','#### 保修']
    assert warranty['source_locator']=='lines:5-5'
    assert any(w.startswith('outline_level_jump:') for w in record['warnings'])
    assert record['routing']['chunk_strategy']=='hierarchical'
