"""Same-region deduplication must preserve competing sources and replay."""
from copy import deepcopy
import pytest

from backend.knowledge_store import KnowledgeStore, SourceIntegrityError
from backend.cross_source import DocumentHit
from tests.test_native_table_chain_probe import source


def citations(store, document_id):
    records = [r for r in store.records() if r.metadata['document_id'] == document_id
               and r.metadata['page_no'] == 2 and r.content.startswith('Label-1-')]
    assert len(records) >= 2
    return [{'citation_id': i + 1, **DocumentHit(r.document_id, r.title, 1, (),
            r.content, r.source_uri, r.metadata).to_dict()} for i, r in enumerate(records)]


def prepare(tmp_path):
    store = KnowledgeStore(tmp_path / 'knowledge')
    raw = source()
    store.ingest(raw, document_id='first', title='First', modality='pdf', filename='first.pdf')
    return store, raw


def test_repeated_table_anchors_use_one_full_region_and_replay(tmp_path):
    store, _ = prepare(tmp_path)
    hits = citations(store, 'first')
    expanded, omitted = store._generation_citations(hits)
    assert len(expanded) == 1
    assert len(omitted) == len(hits) - 1
    assert all(o['reason'] == 'duplicate_verified_native_context' for o in omitted)
    assert all(o['retained_citation_id'] == hits[0]['citation_id'] for o in omitted)
    assert all(f'Label-1-{i}' in expanded[0]['generation_evidence']['text'] for i in range(3))
    store._verify_generation_chunks(expanded)  # Raises on any changed original.


def test_same_bytes_registered_as_different_documents_are_competing_sources(tmp_path):
    store, raw = prepare(tmp_path)
    store.ingest(raw, document_id='second', title='Second', modality='pdf', filename='second.pdf')
    first, second = citations(store, 'first')[0], citations(store, 'second')[0]
    second['citation_id'] = 99
    expanded, omitted = store._generation_citations([first, second])
    assert len(expanded) == 2 and not omitted
    store._verify_generation_chunks(expanded)


def test_forged_duplicate_anchor_still_fails_source_verification(tmp_path):
    store, _ = prepare(tmp_path)
    hits = citations(store, 'first')
    hits[1] = deepcopy(hits[1])
    hits[1]['snippet'] = 'Fabricated duplicate anchor.'
    with pytest.raises(SourceIntegrityError):
        store._generation_citations(hits)


def test_distinct_page_scopes_are_not_deduplicated(tmp_path):
    store, _ = prepare(tmp_path)
    second = citations(store, 'first')[0]
    r = next(r for r in store.records() if r.metadata['document_id'] == 'first'
             and r.metadata['page_no'] == 1 and r.content.startswith('Label-0-'))
    first = {'citation_id': 99, **DocumentHit(r.document_id, r.title, 1, (),
             r.content, r.source_uri, r.metadata).to_dict()}
    expanded, omitted = store._generation_citations([first, second])
    assert len(expanded) == 2 and not omitted
    assert {e['generation_evidence']['page_no'] for e in expanded} == {1, 2}
