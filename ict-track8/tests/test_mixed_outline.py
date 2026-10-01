from backend.knowledge_store import KnowledgeStore
import pytest


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


@pytest.mark.parametrize('newline', ['\n','\r\n','\r'])
def test_blank_lines_do_not_shift_original_source_locators(tmp_path, newline):
    store = KnowledgeStore(tmp_path)
    lines = ['', '', '# 服务手册', '', '', '签收后7日内退货。', '', '',
             '## 保修', '', '免费维修12个月。']
    raw = newline.join(lines).encode()
    store.ingest(raw,document_id='blank-lines',title='原文定位',modality='md',filename='b.md')
    chunks = store.document('blank-lines')['chunks']
    heading = next(chunk for chunk in chunks if chunk['text']=='## 保修')
    warranty = next(chunk for chunk in chunks if '免费维修' in chunk['text'])
    assert heading['source_locator']=='line:9'
    assert warranty['source_locator']=='lines:11-11'
    assert warranty['title_path']==['# 服务手册','## 保修']
    # Independently re-open the actual original, instead of trusting parser text.
    source_lines = store.original('blank-lines')[0].read_text(encoding='utf-8').splitlines()
    assert source_lines[8] == heading['text']
    assert source_lines[10] == warranty['text']
