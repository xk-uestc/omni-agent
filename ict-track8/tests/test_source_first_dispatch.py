"""Production dispatch saves claim generation without weakening source checks."""
from types import SimpleNamespace
import pytest
from backend.knowledge_store import KnowledgeStore, SourceIntegrityError
from backend.source_span_answer import SOURCE_REVIEW, source_first_eligible, replay_source_span_proof
from tests.test_source_span_answer import Client

QUESTION = 'Who is the research lead for CASE0005?'
TEXT = 'The research lead for CASE0005 is Mira Chen.'


class SourceClient(Client):
    supports_source_first_projection = True


def setup(tmp_path, client=None):
    client = client or SourceClient()
    generated = []
    def fallback(question, citations):
        generated.append(question)
        return {'status': 'insufficient_evidence', 'answer': 'Not established',
                'claims': [], 'generation_attempts': []}
    store = KnowledgeStore(tmp_path/'knowledge', generator=SimpleNamespace(client=client, answer=fallback))
    store.ingest(TEXT.encode(), document_id='sample', title='Research lead', modality='txt', filename='research.txt')
    return store, client, generated


def test_direct_fact_skips_claim_generation_but_replays_original_source(tmp_path):
    store, client, generated = setup(tmp_path)
    result = store.answer(QUESTION)
    assert result['status'] == 'ok' and result['answer'] == 'Mira Chen'
    assert result['claims'] == [] and generated == [] and len(client.calls) == 2
    assert replay_source_span_proof(QUESTION, result['answer_span_result'], result['citations'])
    assert any(t.get('dispatch') == 'direct_identifier_fact_before_claim_generation' for t in result['trace'])


@pytest.mark.parametrize('flag', [key for key in SOURCE_REVIEW['properties'] if key != 'reason_code'])
def test_semantic_rejection_cannot_be_bypassed_by_claim_generation(tmp_path, flag):
    store, client, generated = setup(tmp_path, SourceClient(reject=flag))
    result = store.answer(QUESTION)
    assert result['status'] == 'insufficient_evidence' and generated == []
    assert result['answer_mode'] == 'source_span_unverified'


def test_provider_failure_does_not_trigger_more_generation(tmp_path):
    store, client, generated = setup(tmp_path, SourceClient(fail=1))
    result = store.answer(QUESTION)
    assert result['status'] == 'insufficient_evidence' and generated == [] and len(client.calls) == 1


def test_unbound_literal_falls_back_without_duplicate_raw_selection(tmp_path):
    store, client, generated = setup(tmp_path, SourceClient(span='Invented Person'))
    result = store.answer(QUESTION)
    assert result['status'] == 'insufficient_evidence'
    assert generated == [QUESTION] and len(client.calls) == 1


def test_changed_original_during_review_is_rejected(tmp_path):
    store, client, generated = setup(tmp_path)
    def change(index):
        if index == 2:
            store.ingest(b'The research lead for CASE0005 is Someone Else.', document_id='sample',
                title='Research lead', modality='txt', filename='research.txt')
    client.hook = change
    with pytest.raises(SourceIntegrityError):
        store.answer(QUESTION)
    assert generated == []


@pytest.mark.parametrize('question', [
    'Did CASE0005 pass?', 'What is the total for CASE0005?',
    'Compare CASE0005 and CASE0006', 'What percentage did CASE0005 achieve?',
    'Which items belong to CASE0005?', 'What is CASE0005 and who owns it?',
    'Why was CASE0005 rejected?', 'Who is the research lead?',
    '如果CASE0005成功，金额是多少？', 'CASE0005同比增长是多少？',
])
def test_unsupported_operations_keep_existing_dispatch(question):
    assert not source_first_eligible(question, SourceClient())


def test_other_adapters_models_or_reasoning_do_not_implicitly_opt_in():
    assert not source_first_eligible(QUESTION, Client())
    client = SourceClient(); client.model = 'different'
    assert not source_first_eligible(QUESTION, client)
    client.model = 'gpt-6-luna'; client.reasoning = 'high'
    assert not source_first_eligible(QUESTION, client)


def test_identifier_scoped_quantity_and_fullwidth_identifier_are_eligible():
    client = SourceClient()
    assert source_first_eligible('CASE0005约定保修期限是多少个月？', client)
    assert source_first_eligible('ＣＡＳＥ０００５约定保修期限是多少个月？', client)
