from backend.knowledge_store import KnowledgeStore


def put(store, did, body, title='季度公报'):
    return store.ingest(body.encode(), document_id=did, title=title, modality='txt', filename=did+'.txt')


def test_revoked_year_mention_is_not_current_fact(tmp_path):
    s = KnowledgeStore(tmp_path)
    put(s, 'old', '北岬研究院季度公报。对象代码E-17，年份2025。预算905元。该版本已撤销，不可作为2026年现行口径。')
    put(s, 'current', '北岬研究院季度公报。对象代码E-17，年份2026。预算1235元，不含租赁费。')
    hits = s.search('北岬研究院E-17的2026现行公报预算和租赁费？')
    assert {h.metadata['document_id'] for h in hits} == {'current'}


def test_affiliated_org_and_inapplicable_id_are_not_parent(tmp_path):
    s = KnowledgeStore(tmp_path)
    put(s, 'other', '对象林青属于北岬研究院的邻近机构北岬研究院附属组，不适用代码E-17。')
    put(s, 'current', '北岬研究院季度公报。对象代码E-17，年份2026。林青在北岬研究院任采样负责人。')
    assert {h.metadata['document_id'] for h in s.search('北岬研究院的林青在2026公报中担任什么职务？')} == {'current'}


def test_identifier_in_title_alone_is_not_identity(tmp_path):
    s = KnowledgeStore(tmp_path)
    put(s, 'other', '对象代码E-18，预算999元。', title='E-17 预算对照')
    assert not s.search('E-17预算')


def test_unspecified_competing_version_requires_clarification(tmp_path):
    s = KnowledgeStore(tmp_path)
    for year in (2025, 2026):
        put(s, 'v'+str(year), f'北岬研究院季度公报。对象代码E-17，年份{year}。预算{year}元。')
    hits, audit = s.search('北岬研究院E-17公报预算？', with_audit=True)
    assert not hits
    assert audit['source_binding']['status'] == 'requires_clarification'
    result = s.answer('北岬研究院E-17公报预算？')
    assert result['needs_clarification'] and result['clarification_code'] == 'source_version_required'


def test_negation_preserved_and_other_document_revocation_not_self(tmp_path):
    s = KnowledgeStore(tmp_path)
    put(s, 'current', '北岬研究院季度公报。对象代码E-17，年份2026。旧公报已撤销。本文件有效，预算不含租赁费。')
    assert s.search('北岬研究院E-17的2026公报预算是否包含租赁费？')
