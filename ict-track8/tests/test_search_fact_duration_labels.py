"""Duration event linking must preserve qualifiers, units and source replay."""
import pytest

from backend.evidence_fact import extract_search_fact
from backend.knowledge_store import KnowledgeStore


def fact(tmp_path, source, label, unit='小时'):
    store = KnowledgeStore(tmp_path / 'knowledge')
    store.ingest(source.encode(), document_id='process', title='流程标准', modality='txt', filename='process.txt')
    evidence = {'hits': [hit.to_dict() for hit in store.search('专用流程 '+label)]}
    return extract_search_fact(store, evidence, scope='专用流程', label=label, unit=unit)


@pytest.mark.parametrize('label', ['首次响应', '首次响应时间', '首次响应时长', '首次响应小时数'])
def test_literal_event_with_explicit_duration(tmp_path, label):
    result = fact(tmp_path, '专用流程应在2小时内首次响应。', label)
    assert result['value'] == 2
    assert result['quote'] == '专用流程应在2小时内首次响应'
    assert result['label_binding']['literal_label'] == '首次响应'
    assert result['sha256'] and result['source_uri'].endswith('/process/original')


@pytest.mark.parametrize('event,unit,value', [('样本复核', '分钟', 7), ('交付验收', '天', 3), ('新设备校准', '小时', 9)])
def test_not_business_specific(tmp_path, event, unit, value):
    result = fact(tmp_path, f'专用流程必须在{value}{unit}内{event}。', event+'时间', unit)
    assert result['value'] == value
    assert result['label_binding']['literal_label'] == event


@pytest.mark.parametrize('source,label,unit', [
    ('专用流程应在2小时内首次响应。', '关闭时间', '小时'),
    ('专用流程应在2小时内首次响应。', '平均响应时间', '小时'),
    ('专用流程应在2小时内首次响应。', '首次响应分钟数', '小时'),
    ('专用流程应在2小时内首次响应。', '首次响应时间', '分钟'),
    ('专用流程完成日期为2天。', '完成日期时间', '天'),
    ('专用流程样本复核费用为2元。', '样本复核时间', '元'),
    ('专用流程首次响应2小时或3小时。', '首次响应时间', '小时'),
    ('专用流程首次响应至少2小时。', '首次响应时间', '小时'),
    ('专用流程首次响应不是2小时。', '首次响应时间', '小时'),
    ('专用流程首次响应后2小时内关闭。', '首次响应时间', '小时'),
    ('专用流程关闭需2小时并记录首次响应。', '首次响应时间', '小时'),
    ('专用流程首次响应为2小时后再次响应。', '首次响应时间', '小时'),
    ('专用流程应在2小时内完成首次响应审核。', '首次响应时间', '小时'),
    ('专用流程未在2小时内首次响应。', '首次响应时间', '小时'),
    ('专用流程没有在2小时内首次响应。', '首次响应时间', '小时'),
    ('专用流程不得在2小时内首次响应。', '首次响应时间', '小时'),
    ('专用流程禁止在2小时内首次响应。', '首次响应时间', '小时'),
])
def test_no_scope_metric_unit_or_numeric_weakening(tmp_path, source, label, unit):
    with pytest.raises(ValueError):
        fact(tmp_path, source, label, unit)
