"""Descriptive duration labels retain literal events, units and source guards."""
import pytest

from backend.evidence_fact import _label_span, extract_search_fact
from backend.knowledge_store import KnowledgeStore


def evidence(tmp_path, text):
    store = KnowledgeStore(tmp_path / 'knowledge')
    store.ingest(text.encode(), document_id='flow', title='实验流程',
                 modality='txt', filename='flow.txt')
    document = store.document('flow')
    hits = [{'snippet': chunk['text'],
             'source_uri': '/api/v1/knowledge/documents/flow/original',
             'metadata': {'document_id': 'flow', 'chunk_id': chunk['chunk_id'],
                          'source_sha256': document['sha256'],
                          'source_locator': chunk['source_locator']}}
            for chunk in document['chunks']]
    return store, {'hits': hits}


@pytest.mark.parametrize('event,suffix,unit,value', [
    ('样本复核', '时间要求', '分钟', 7),
    ('交付验收', '时长要求', '天', 3),
    ('新设备校准', '耗时要求', '小时', 9),
    ('设备复测', '小时数要求', '小时', 4),
    ('样本复核', '分钟数要求', '分钟', 8),
    ('交付验收', '天数要求', '天', 5),
    ('设备维护', '月数要求', '个月', 2),
])
def test_measurement_requirement_binds_same_literal_event(tmp_path, event, suffix, unit, value):
    text = f'专用流程应在{value}{unit}内{event}。'
    store, hits = evidence(tmp_path, text)
    label = event + suffix
    result = extract_search_fact(store, hits, scope='专用流程', label=label, unit=unit)
    assert result['value'] == value
    assert result['unit'] == unit and result['label'] == label
    assert result['label_binding']['literal_label'] == event
    assert result['label_binding']['method'] == 'literal_event_explicit_duration_unit'
    assert result['quote'] == text.rstrip('。')
    assert result['sha256'] == store.document('flow')['sha256']
    assert result['locator'] and result['chunk_id'] and result['sources']


def test_event_after_requirement_keeps_local_relation(tmp_path):
    store, hits = evidence(tmp_path, '专用流程样本复核要求为7分钟。')
    result = extract_search_fact(store, hits, scope='专用流程',
                                 label='样本复核时长要求', unit='分钟')
    assert result['value'] == 7
    assert result['quote'] == '专用流程样本复核要求为7分钟'


@pytest.mark.parametrize('source,label,unit', [
    ('专用流程应在7分钟内样本复核。', '样本批准时间要求', '分钟'),
    ('专用流程应在7分钟内样本复核。', '平均样本复核时长要求', '分钟'),
    ('专用流程应在7分钟内样本复核。', '样本复核分钟数要求', '小时'),
    ('专用流程应在7小时内样本复核。', '样本复核分钟数要求', '小时'),
    ('其他流程应在7分钟内样本复核。', '样本复核时间要求', '分钟'),
    ('专用流程样本复核费用为7元。', '样本复核费用要求', '元'),
    ('专用流程应在7分钟内样本复核。', '样本复核要求', '分钟'),
    ('专用流程应在7分钟内样本复核。', '样本复核时间建议', '分钟'),
    ('专用流程应在7分钟内样本复核。', '样本复核时间标准', '分钟'),
    ('专用流程应在7分钟内样本复核。', '样本复核时间要求至少', '分钟'),
    ('专用流程应在7分钟内样本复核。', '至少样本复核时间要求', '分钟'),
    ('专用流程完成日期为7天。', '完成日期时间要求', '天'),
    ('专用流程样本复核后7分钟内批准。', '样本复核时间要求', '分钟'),
    ('专用流程批准需7分钟并记录样本复核。', '样本复核时间要求', '分钟'),
    ('专用流程应在7分钟内完成样本复核审核。', '样本复核时间要求', '分钟'),
    ('专用流程样本复核7分钟或8分钟。', '样本复核时间要求', '分钟'),
])
def test_descriptor_does_not_replace_event_scope_unit_or_relation(tmp_path, source, label, unit):
    store, hits = evidence(tmp_path, source)
    with pytest.raises(ValueError):
        extract_search_fact(store, hits, scope='专用流程', label=label, unit=unit)


@pytest.mark.parametrize('source', [
    '专用流程未在7分钟内样本复核。',
    '专用流程没有在7分钟内样本复核。',
    '专用流程不得在7分钟内样本复核。',
    '专用流程样本复核不是7分钟。',
    '专用流程样本复核至少7分钟。',
    '专用流程样本复核大于7分钟。',
    '专用流程样本复核超过7分钟。',
    '专用流程样本复核为7至8分钟。',
])
def test_negation_and_numeric_bounds_remain_in_source_and_reject(tmp_path, source):
    store, hits = evidence(tmp_path, source)
    with pytest.raises(ValueError):
        extract_search_fact(store, hits, scope='专用流程', label='样本复核时间要求', unit='分钟')


def test_non_duration_literal_label_stays_exact_and_is_not_stripped(tmp_path):
    label = '用户要求复核费用'
    store, hits = evidence(tmp_path, f'专用流程{label}为7元。')
    result = extract_search_fact(store, hits, scope='专用流程', label=label, unit='元')
    assert result['label_binding']['literal_label'] == label
    assert result['label_binding']['method'] == 'literal_label'
    assert _label_span('专用流程复核费用为7元', label, '元') is None


@pytest.mark.parametrize('mutation', ['quote', 'source_hash', 'locator'])
def test_descriptor_still_requires_authentic_source_binding(tmp_path, mutation):
    store, hits = evidence(tmp_path, '专用流程应在7分钟内样本复核。')
    hit = hits['hits'][0]
    if mutation == 'quote':
        hit['snippet'] = '专用流程应在8分钟内样本复核。'
    elif mutation == 'source_hash':
        hit['metadata']['source_sha256'] = '0' * 64
    else:
        hit['metadata']['source_locator'] = 'body:forged'
    with pytest.raises(ValueError, match='来源不一致'):
        extract_search_fact(store, hits, scope='专用流程', label='样本复核时间要求', unit='分钟')
