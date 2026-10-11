import pytest
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from tools.evaluate_foundation_f2 import raw_file


def put(store, pages, did='source'):
    return store.ingest(raw_file({'type':'pdf','pages':pages}), document_id=did, title='Record', modality='pdf', filename='r.pdf')


def test_explicit_continuation_without_repeated_identifier(tmp_path):
    s=KnowledgeStore(tmp_path)
    put(s,['E-17校准周期为28天。','说明（续）\n停机日不计入周期。'])
    hits,a=s.search('E-17停机日如何处理？',with_audit=True)
    assert any('停机日不计入周期' in h.snippet for h in hits)
    assert any(link['relation']=='explicit_next_page_continuation' for link in a['linked_scope']['links'].values())


@pytest.mark.parametrize('second', ['说明（续）\n对象E-18停机日计入周期。','停机日不计入周期。'])
def test_other_sample_and_unrelated_page_not_inherited(tmp_path, second):
    s=KnowledgeStore(tmp_path);put(s,['E-17校准周期为28天。',second])
    hits,a=s.search('E-17停机日如何处理？',with_audit=True)
    assert all(h.metadata['page_no']==1 for h in hits)
    assert a['linked_scope']['linked_chunk_count']==0


def test_first_page_single_object_scope_and_revocation(tmp_path):
    s=KnowledgeStore(tmp_path)
    put(s,['对象代码E-17，年份2026。','隔离记录不得计入合格样本。'])
    hits=s.search('E-17的2026记录隔离如何处理？')
    assert any(h.metadata.get('identifier_scope_link') for h in hits)
    put(s,['对象代码E-17，年份2025。该版本已撤销。','隔离记录可以计入合格样本。'],'old')
    assert all(h.metadata['document_id']=='source' for h in s.search('E-17的2026现行记录隔离如何处理？'))
    put(s,['对象代码E-18，年份2026。','新样本说明。'])
    assert not s.search('E-17的2026现行记录隔离如何处理？')
    with pytest.raises(SourceRevisionError):s._verify_citation_sources([h.to_dict() for h in hits])


def test_document_navigation_budget_is_explicit(tmp_path):
    s=KnowledgeStore(tmp_path)
    put(s,['对象代码E-17。']+['例外条件不得合并。']*12)
    hits,a=s.search('E-17例外条件？',with_audit=True)
    assert all(h.metadata['page_no']==1 for h in hits)
    assert a['linked_scope']['status']=='budget_exhausted'
