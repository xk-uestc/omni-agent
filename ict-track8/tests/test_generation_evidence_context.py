"""General source-context regressions; no benchmark IDs or reference answers."""
from copy import deepcopy
import hashlib
import json

import pytest

from backend.cross_source import DocumentHit
from backend.evidence_context import bounded_prefix, sentence_texts, text_sha256
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore, SourceIntegrityError, SourceRevisionError
from backend.responses_client import GenerationError


def claim(text, quote=None):
    return {'abstain': False, 'claims': [{'text': text,
            'support': [{'citation_id': 1, 'quote': quote if quote is not None else text}]}]}


@pytest.mark.parametrize('source, expected', [
    ('Dr. Jane A. Miller works for Example Inc. in the U.S. Revenue is 12.5 dollars. '
     'Costs are 3.25 dollars.',
     ['Dr. Jane A. Miller works for Example Inc. in the U.S. Revenue is 12.5 dollars',
      ' Costs are 3.25 dollars']),
    ('The U.S. office exists. John A. Smith is the manager.',
     ['The U.S. office exists', ' John A. Smith is the manager']),
    ('收入100.25万元。成本80万元。', ['收入100.25万元', '成本80万元']),
    ('Not\ncovered. If approved, the limit is 12.50 dollars.',
     ['Not\ncovered', ' If approved, the limit is 12.50 dollars']),
])
def test_english_boundaries_preserve_abbreviations_initials_decimals_and_wrapping(source, expected):
    assert sentence_texts(source) == expected


def test_full_source_literal_english_fact_after_unrelated_sentences_is_verifiable():
    quote = 'The lead engineer represents Example Inc.'
    source = 'A general introduction is recorded. A different firm is also mentioned. ' + quote
    assert GroundedGenerator.validate(claim(quote), {1: source})[0]['text'] == quote


@pytest.mark.parametrize('text, quote, source', [
    ('The system covers misuse.', 'The system covers misuse.', 'It is not true that The system covers misuse. It covers defects.'),
    ('Revenue is 10 dollars.', 'Revenue is 10 dollars.', 'Projected Revenue is 10 dollars.'),
    ('The limit is 10 dollars.', 'The limit is 10 dollars.', 'If approved, The limit is 10 dollars.'),
    ('The limit is 10 dollars.', 'The limit is 10 dollars.', 'Unless cancelled, The limit is 10 dollars.'),
    ('The limit is 10 dollars.', 'The limit is 10 dollars.', 'When approved, The limit is 10 dollars.'),
    ('The limit is 10 dollars.', 'The limit is 10 dollars.', 'Only after approval, The limit is 10 dollars.'),
    ('Revenue is 10 dollars.', 'Revenue is 10 dollars.', 'Forecast data. Revenue is 10 dollars.'),
    ('Revenue is 10 dollars.', 'Revenue is 10 dollars.', 'Forecast:\nRevenue is 10 dollars.'),
    ('Revenue is 10 dollars.', 'Revenue is 10 dollars.', 'The following figures are projected. Revenue is 10 dollars.'),
    ('收入10万元', '收入10万元', '本节为预测数据。收入10万元。'),
])
def test_english_sentence_fix_does_not_authorize_deleting_scope(text, quote, source):
    with pytest.raises(GenerationError):
        GroundedGenerator.validate(claim(text, quote), {1: source})


@pytest.mark.parametrize('source, quote', [
    ('An unrelated forecast is unavailable. The actual cost is 12.50 dollars.',
     'The actual cost is 12.50 dollars.'),
    ('Forecast data. Projected revenue is 10 dollars.\n\nActual results. Revenue is 11 dollars.',
     'Revenue is 11 dollars.'),
    ('If approved, The limit is 10 dollars.', 'If approved, The limit is 10 dollars.'),
    ('The device does not cover misuse. The device covers defects.', 'The device covers defects.'),
])
def test_independent_complete_facts_and_preserved_conditions_still_pass(source, quote):
    assert GroundedGenerator.validate(claim(quote), {1: source})


def test_bounded_prefix_cannot_start_after_negation_or_split_a_fact():
    source = 'The following figures are projected. ' + 'If approved, ' + 'x' * 2000 + '.'
    prefix, end, truncated = bounded_prefix(source, 1800)
    assert prefix == 'The following figures are projected.'
    assert source[:end] == prefix and truncated
    assert bounded_prefix('Not\n' + 'x' * 2000, 1800) == ('', 0, True)
    assert bounded_prefix('Complete fact.', 0) == ('', 0, True)


class RecordingClient:
    model = 'gpt-6-luna'

    def __init__(self, quote=None, mutation=None):
        self.quote, self.mutation = quote, mutation
        self.calls, self.audit = [], {}

    def generate(self, instructions, context, schema, **kwargs):
        self.calls.append(deepcopy(context))
        if self.mutation:
            self.mutation()
        self.audit = {'status': 'completed', 'http_status': 200, 'model': self.model}
        if self.quote is None:
            return {'abstain': True, 'claims': []}
        return claim(self.quote)


def store_with_text(tmp_path, text, client):
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    store.ingest(text.encode(), document_id='source', title='Evidence report', modality='txt', filename='source.txt')
    return store


def test_tail_fact_reaches_model_and_validator_without_changing_frontend_excerpt(tmp_path):
    quote = 'The primary engineer represents Example Inc.'
    source = 'Evidence report describes an organization. ' + 'Background context is available. ' * 20 + quote
    client = RecordingClient(quote)
    store = store_with_text(tmp_path, source, client)
    before = store.search('Evidence report organization')[0]
    assert quote not in before.snippet
    result = store.answer('Evidence report organization')
    hit = result['citations'][0]
    evidence = hit['generation_evidence']
    assert result['answer_mode'] == 'model_grounded' and result['status'] == 'ok'
    assert hit['snippet'] == before.snippet
    assert quote in client.calls[0]['evidence'][0]['text']
    assert client.calls[0]['evidence'][0]['text'] == evidence['text']
    assert evidence['offset_start'] == 0 and not evidence['truncated']
    chunk = store.document('source')['chunks'][0]
    assert chunk['text'][:evidence['offset_end']] == evidence['text']
    assert evidence['evidence_sha256'] == text_sha256(evidence['text'])
    assert evidence['chunk_sha256'] == text_sha256(chunk['text'])
    assert evidence['source_sha256'] == hashlib.sha256(source.encode()).hexdigest()


def test_dense_400_character_frontend_excerpt_can_rehydrate_the_same_authoritative_chunk(tmp_path, monkeypatch):
    quote = 'The final engineer works for Example Inc.'
    source = 'Background context. ' * 30 + quote
    client = RecordingClient(quote)
    store = store_with_text(tmp_path, source, client)
    record = store.records()[0]
    dense_hit = DocumentHit(record.document_id, record.title, .9, (), record.content[:400],
                            record.source_uri, {**record.metadata, 'dense_cosine': .9})
    monkeypatch.setattr(store, 'search', lambda *args, **kwargs: [dense_hit])
    result = store.answer('Who is the final engineer?')
    assert result['status'] == 'ok'
    assert result['citations'][0]['snippet'] == source[:400]
    assert quote not in result['citations'][0]['snippet']
    assert quote in client.calls[0]['evidence'][0]['text']


@pytest.mark.parametrize('mutation', ['replace', 'delete', 'tamper', 'chunk_edit'])
def test_sources_and_chunk_snapshot_checked_after_generation(tmp_path, mutation):
    client = RecordingClient()
    store = store_with_text(tmp_path, 'Evidence report confirms a valid source.', client)

    def change():
        if mutation == 'replace':
            store.ingest(b'Evidence report has a new version.', document_id='source', title='Evidence report', modality='txt', filename='source.txt')
        elif mutation == 'delete':
            with store.connect() as connection:
                connection.execute('DELETE FROM documents WHERE document_id=?', ('source',))
        elif mutation == 'tamper':
            document = store.document('source')
            (store.assets / document['asset']).write_bytes(b'changed bytes')
        else:
            with store.connect() as connection:
                row = connection.execute('SELECT chunk_id,payload FROM chunks').fetchone()
                payload = json.loads(row[1])
                payload['text'] += ' Edited.'
                connection.execute('UPDATE chunks SET payload=? WHERE chunk_id=?', (json.dumps(payload), row[0]))

    client.mutation = change
    with pytest.raises(SourceIntegrityError):
        store.answer('Evidence report source')
    assert len(client.calls) == 1


def test_source_replacement_between_search_and_expansion_fails_before_model(tmp_path, monkeypatch):
    client = RecordingClient()
    store = store_with_text(tmp_path, 'Evidence report confirms the old source.', client)
    hits = store.search('Evidence report source')
    store.ingest(b'Evidence report confirms a new source.', document_id='source', title='Evidence report', modality='txt', filename='source.txt')
    monkeypatch.setattr(store, 'search', lambda *args, **kwargs: hits)
    with pytest.raises(SourceRevisionError):
        store.answer('Evidence report source')
    assert not client.calls


def test_forged_excerpt_or_locator_cannot_rehydrate_other_text(tmp_path):
    client = RecordingClient()
    store = store_with_text(tmp_path, 'Evidence report confirms the source.', client)
    hit = {'citation_id': 1, **store.search('Evidence report source')[0].to_dict()}
    for forged in [{**hit, 'snippet': 'Invented facts.'},
                   {**hit, 'metadata': {**hit['metadata'], 'source_locator': 'page:999'}},
                   {**hit, 'source_uri': '/invented'}]:
        with pytest.raises(SourceIntegrityError):
            store._generation_citations([forged])


def test_resource_limits_and_complete_prefix_offsets(tmp_path):
    client = RecordingClient()
    store = KnowledgeStore(tmp_path, generator=GroundedGenerator(client))
    citations = []
    for index in range(10):
        store.ingest(('Evidence line is complete. ' * 60).encode(), document_id=f'doc{index}',
                     title='Evidence line', modality='txt', filename=f'{index}.txt')
    for index, record in enumerate(store.records(), 1):
        citations.append({'citation_id': index, 'document_id': record.document_id,
                          'title': record.title, 'source_uri': record.source_uri,
                          'snippet': record.content[:180], 'metadata': record.metadata})
    expanded, omitted = store._generation_citations(citations)
    assert len(expanded) <= 8 and sum(len(x['generation_evidence']['text']) for x in expanded) <= 12000
    assert omitted
    for hit in expanded:
        e = hit['generation_evidence']
        assert len(e['text']) <= 1800 and e['offset_start'] == 0
        record = next(r for r in store.records() if r.document_id == hit['document_id'])
        assert record.content[:e['offset_end']] == e['text']
        assert e['text'].rstrip().endswith('.')


def test_unverified_pdf_row_keeps_short_extract_and_never_becomes_expanded_model_fact(tmp_path):
    client = RecordingClient()
    store = store_with_text(tmp_path, 'Evidence table has source values.', client)
    with store.connect() as connection:
        row = connection.execute('SELECT chunk_id,payload FROM chunks').fetchone()
        chunk = json.loads(row[1])
        chunk.update({'modality': 'pdf', 'content_type': 'row'})
        connection.execute('UPDATE chunks SET payload=? WHERE chunk_id=?', (json.dumps(chunk), row[0]))
    result = store.answer('Evidence table source')
    assert not client.calls and 'generation_evidence' not in result['citations'][0]
    assert result['trace'][-2]['omitted'][0]['reason'] == 'pdf_table_layout_not_verified'
    assert result['trace'][-1]['status'] == 'no_safe_bounded_evidence'
