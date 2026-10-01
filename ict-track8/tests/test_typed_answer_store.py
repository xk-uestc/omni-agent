"""Production store projections preserve full facts and authoritative pins."""
import pytest

from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore, SourceRevisionError


class LiteralClient:
    model = 'gpt-6-luna'
    audit = {}

    def __init__(self, mutate=None):
        self.mutate = mutate

    def generate(self, instructions, context, schema, **kwargs):
        hit = context['evidence'][0]
        if self.mutate:
            self.mutate()
        return {'abstain': False, 'claims': [{'text': hit['text'],
                'support': [{'citation_id': hit['citation_id'], 'quote': hit['text']}]}]}


def make_store(tmp_path, text):
    client = LiteralClient()
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    store.ingest(text.encode(), document_id='record', title='Finance director revenue',
                 modality='txt', filename='record.txt')
    return store, client


@pytest.mark.parametrize('text,question,value', [
    ('Finance director: Mira Chen.', 'Who is the finance director?', 'Mira Chen'),
    ('Year: 2025. Revenue: -125.50 USD.', 'What amount is revenue in 2025?', '-125.50 USD'),
])
def test_store_projects_with_original_full_answer(tmp_path, text, question, value):
    store, _ = make_store(tmp_path, text)
    result = store.answer(question)
    assert result['answer_mode'] == 'model_grounded'
    assert text in result['answer']
    assert result['answer_projection']['answer_value'] == value
    assert result['answer_projection']['claims'] == result['claims']
    assert result['trace'][-1]['status'] == 'verified'


def test_unsupported_question_retains_full_fact(tmp_path):
    store, _ = make_store(tmp_path, 'Finance director: Mira Chen.')
    result = store.answer('Explain the finance director')
    assert result['answer_mode'] == 'model_grounded'
    assert 'Finance director: Mira Chen.' in result['answer']
    assert 'answer_projection' not in result
    assert result['trace'][-1]['status'] == 'unsupported'


def test_source_replacement_during_generation_never_projects(tmp_path):
    store, client = make_store(tmp_path, 'Finance director: Mira Chen.')
    client.mutate = lambda: store.ingest(b'Finance director: Sam Jones.', document_id='record',
                                        title='Finance director', modality='txt', filename='record.txt')
    with pytest.raises(SourceRevisionError):
        store.answer('Who is the finance director?')


def test_replay_failure_retains_full_answer_without_projection(tmp_path, monkeypatch):
    store, _ = make_store(tmp_path, 'Finance director: Mira Chen.')
    monkeypatch.setattr('backend.typed_answer.replay_answer_proof', lambda *args: False)
    result = store.answer('Who is the finance director?')
    assert 'answer_projection' not in result
    assert result['trace'][-1]['status'] == 'proof_replay_failed'
    assert 'Finance director: Mira Chen.' in result['answer']


def test_relevant_competing_evidence_cannot_project(tmp_path):
    store, _ = make_store(tmp_path, 'Finance director: Mira Chen.')
    store.ingest(b'Finance director: Sam Jones.', document_id='other', title='Finance director',
                 modality='txt', filename='other.txt')
    result = store.answer('Who is the finance director?')
    assert result['answer_mode'] == 'model_grounded'
    assert 'answer_projection' not in result
    assert result['trace'][-1]['reason'] == 'ambiguous_or_conflicting_slot'
