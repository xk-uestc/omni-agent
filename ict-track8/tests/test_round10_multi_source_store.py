"""Integration contracts: preserve all parts and rehydrate original evidence."""
from copy import deepcopy
import pytest

from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.source_span_answer import bind_source_span_answer
from backend.evidence_recovery import recovery_eligible
from tests.test_grounded_span_answer import fixture
from tests.test_source_span_store import Client

QUESTION = 'Who is the research lead and who is the finance lead?'
TEXT = b'The research lead is Mira Chen. The finance lead is Noah Lee.'


def prepare(tmp_path):
    client = Client()
    store = KnowledgeStore(tmp_path / 'knowledge', generator=GroundedGenerator(client))
    store.ingest(TEXT, document_id='record', title='Research and finance leads', modality='txt', filename='r.txt')
    return store, client


def test_single_span_cannot_publish_compound_even_if_model_would_approve():
    _, citation = fixture(TEXT.decode())
    client = Client()
    result = bind_source_span_answer(QUESTION, [citation], client)
    assert result['status'] == 'unsupported' and client.calls == []
    assert result['reason'] == 'source_literal_requires_multi_fragment_contract'


def test_store_dispatches_multi_contract_and_rehydrates_for_replay(tmp_path, monkeypatch):
    store, client = prepare(tmp_path)
    snapshots = []
    def bind(question, citations, api):
        assert question == QUESTION and api is client
        snapshots.append(deepcopy(citations))
        return {'status': 'model_reviewed', 'answer_value': 'Mira Chen\nNoah Lee', 'model_audits': []}
    def replay(question, result, citations):
        assert question == QUESTION and citations == snapshots[0]
        return True
    monkeypatch.setattr('backend.source_multi_span_answer.bind_multi_source_answer', bind)
    monkeypatch.setattr('backend.source_multi_span_answer.replay_multi_source_proof', replay)
    result = store.answer(QUESTION)
    assert result['status'] == 'ok' and result['answer'] == 'Mira Chen\nNoah Lee'
    assert result['answer_mode'] == 'source_multi_span_model_reviewed'
    assert result['trace'][-1]['stage'] == 'evidence_first_multi_source_span'
    assert len(snapshots) == 1 and not any('source_span' in call for call in client.calls)


def test_failed_multi_replay_never_publishes_or_reverts_to_single(tmp_path, monkeypatch):
    store, _ = prepare(tmp_path)
    monkeypatch.setattr('backend.source_multi_span_answer.bind_multi_source_answer',
        lambda *args: {'status': 'model_reviewed', 'answer_value': 'Mira Chen', 'model_audits': []})
    monkeypatch.setattr('backend.source_multi_span_answer.replay_multi_source_proof', lambda *args: False)
    result = store.answer(QUESTION)
    assert result['status'] == 'insufficient_evidence' and 'answer_span_result' not in result
    assert any(t['stage'] == 'evidence_first_multi_source_span' and t['status'] == 'source_replay_failed'
               for t in result['trace'])


def test_original_source_replaced_during_multi_recovery_raises(tmp_path, monkeypatch):
    store, _ = prepare(tmp_path)
    def bind(*args):
        store.ingest(b'Changed original leads.', document_id='record', title='Changed', modality='txt', filename='r.txt')
        return {'status': 'unsupported', 'model_audits': []}
    monkeypatch.setattr('backend.source_multi_span_answer.bind_multi_source_answer', bind)
    with pytest.raises(SourceRevisionError):
        store.answer(QUESTION)


def test_successful_complete_claims_are_not_compressed_to_one_span(tmp_path, monkeypatch):
    store, client = prepare(tmp_path)
    monkeypatch.setattr('backend.source_multi_span_answer.bind_multi_source_answer',
        lambda *args: {'status': 'unsupported', 'reason': 'multi_semantic_review_rejected', 'model_audits': []})
    def answer(question, citations):
        text = citations[0]['generation_evidence']['text']
        return {'status': 'ok', 'answer': 'Mira Chen\nNoah Lee',
            'claims': [{'text': text, 'support': [{'citation_id': 1, 'quote': text}]}]}
    monkeypatch.setattr(store.generator, 'answer', answer)
    result = store.answer(QUESTION)
    assert result['answer'] == 'Mira Chen\nNoah Lee' and result['claims']
    assert result['answer_strategy'] == 'complete_reviewed_claims_without_single_span_projection'
    assert 'answer_span_result' not in result and client.calls == []


def test_complete_multi_source_answer_is_not_regenerated_for_better_score():
    assert not recovery_eligible({'status': 'ok', 'answer_mode': 'source_multi_span_model_reviewed'}, Client())


@pytest.mark.parametrize('reason', ['multi_provider_failed', 'multi_selection_provider_invalid', 'multi_review_provider_invalid'])
def test_multi_provider_failure_does_not_trigger_generation_or_other_requests(tmp_path, monkeypatch, reason):
    store, client = prepare(tmp_path)
    monkeypatch.setattr('backend.source_multi_span_answer.bind_multi_source_answer',
        lambda *args: {'status': 'unsupported', 'reason': reason, 'model_audits': []})
    result = store.answer(QUESTION)
    assert result['status'] == 'insufficient_evidence' and client.calls == []
    assert result['answer_completeness'] == 'multi_part_provider_unavailable'
