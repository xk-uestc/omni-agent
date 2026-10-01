from backend.knowledge_store import KnowledgeStore


def test_contract_identifier_is_mandatory_scope_and_unknown_abstains(tmp_path):
    store = KnowledgeStore(tmp_path/'knowledge')
    for index in range(30):
        store.ingest(f'CASE{index:04d}约定保修期限为{12+index%7}个月。'.encode(),
                     document_id=f'contract-{index}', title='合同资料', modality='txt', filename='contract.txt')
    result = store.answer('CASE0005约定保修期限是多少个月？')
    assert result['status'] == 'ok'
    assert result['citations']
    assert all('CASE0005' in hit['snippet'] for hit in result['citations'])
    assert '17个月' in result['answer']
    assert store.answer('CASE9999约定保修期限是多少个月？')['status'] == 'insufficient_evidence'


def test_identifier_boundary_does_not_accept_longer_prefix_collision(tmp_path):
    store = KnowledgeStore(tmp_path/'knowledge')
    store.ingest('CASE00050约定保修期限为8个月。'.encode(), document_id='longer', title='合同资料', modality='txt', filename='a.txt')
    assert store.answer('case0005的保修期限')['status'] == 'insufficient_evidence'
