"""Compound coverage regressions; no public answers or provider requests."""
import pytest

from backend.answer_contract import question_contract, whole_answer_shape_error
from backend.knowledge_store import KnowledgeStore


@pytest.mark.parametrize('question,count', [
    ('项目负责人是谁，预算是多少，截止日期是什么？', 3),
    ('项目使用哪些设备；由谁负责验收？', 2),
    ('项目2031年负责人是谁，2032年负责人是谁？', 2),
    ('Who is the lead for Project-A7; what is its budget; when is its deadline?', 3),
    ('Who is the lead?\nWhat is the deadline?', 2),
    ('负责人、预算和截止日期分别是什么？', 1),
])
def test_explicit_question_parts_enter_complete_answer_lane(question, count):
    contract = question_contract(question)
    assert len(contract['requested_parts']) == count
    assert contract['multiple_requested_fields']


@pytest.mark.parametrize('question', [
    'Formula and Diapers的收入是多少？',
    'What is the revenue when A and B are active?',
    '项目预算为237万元时，负责人是谁？',
    '2031年和2032年销售额合计是多少？',
    'What does the label "who; what" mean?',
])
def test_entity_conditions_and_quoted_punctuation_are_not_subquestions(question):
    assert not question_contract(question)['multiple_requested_fields']


def test_unknown_fact_is_not_a_completed_who_answer():
    assert whole_answer_shape_error('Who is the finance lead?', [
        {'text': 'No finance lead is stated in this record.'}]) == 'answer_contains_explicit_missing_fact'
    assert whole_answer_shape_error('预算是多少？', [
        {'text': '本记录没有记载预算。'}]) == 'answer_contains_explicit_missing_fact'
    assert whole_answer_shape_error('Does the record state the finance lead?', [
        {'text': 'No finance lead is stated in this record.'}]) is None
    assert whole_answer_shape_error('是否记载预算？', [{'text': '本记录没有记载预算。'}]) is None


def test_compound_navigation_finds_all_sources_with_top_k_one(tmp_path):
    store = KnowledgeStore(tmp_path)
    for index, text in enumerate(['星河项目负责人是林青。', '星河项目预算为237万元。',
                                  '星河项目截止日期是2031年11月16日。']):
        store.ingest(text.encode(), document_id=f'record-{index}', title=f'Record {index}',
                     modality='txt', filename=f'{index}.txt')
    question = '星河项目负责人是谁，预算是多少，截止日期是什么？'
    original = store.search(question, top_k=1)
    hits, audit = store._question_part_hits(question, original, top_k=1)
    assert {hit.metadata['document_id'] for hit in hits} == {'record-0', 'record-1', 'record-2'}
    assert audit['question_preserved'] is True and audit['navigation_only'] is True
    assert audit['semantic_sufficiency'] == 'not_established'


def test_explicit_document_and_page_filters_are_kept_in_every_subquery(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path)
    calls = []
    def search(query, **kwargs):
        calls.append((query, kwargs))
        return []
    monkeypatch.setattr(store, 'search', search)
    question = 'Who is the lead for Project-A7; what is its budget?'
    hits, audit = store._question_part_hits(question, [], top_k=1, document_id='pinned', page_no=3)
    assert not hits and calls
    assert all(call[1]['document_id'] == 'pinned' and call[1]['page_no'] == 3 for call in calls)
    assert all('Project-A7' in query for query, _ in calls)
    assert all(question in query for query, _ in calls)
    assert audit['model_called'] is False


def test_single_question_has_no_extra_search(tmp_path, monkeypatch):
    store = KnowledgeStore(tmp_path)
    monkeypatch.setattr(store, 'search', lambda *a, **kw: pytest.fail('single question must not expand'))
    assert store._question_part_hits('Who is the lead?', [], top_k=4) == ([], None)
