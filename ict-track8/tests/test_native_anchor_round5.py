"""Literal PDF signs and legacy anchor locations, without benchmark answers."""
from copy import deepcopy
import hashlib
from itertools import combinations, product
import json
import time

import fitz
import pytest

from backend.answer_contract import native_source_only_contract_valid
from backend.chunk_cleaning import DocumentChunker
from backend.evidence_context import text_sha256
from backend.knowledge_store import KnowledgeStore, SourceRevisionError
from backend.native_anchor import (ANCHOR_POLICY_VERSION, anchor_ranges,
                                   line_wrap_alias_positions, literal_compact)
from backend.pdf_native_context import extract_native_context, extract_native_page_context


def pdf(entries):
    with fitz.open() as document:
        page = document.new_page(width=600, height=700)
        for x, y, text in entries:
            page.insert_text((x, y), text, fontsize=10)
        return document.tobytes()


@pytest.mark.parametrize('source', [
    'The loss is -\nnegative and recorded.',
    'The loss is -\n12 units.',
    'The interval is 3-\n5 percent.',
    'The term is well-known.',
    'The terms are state-of-\nthe-art.',
    'The expression is x-\ny.',
    'The expression is 12alpha-\nbeta.',
    'The expression is alpha+-\nbeta.',
    'The expression is alpha−\nbeta.',
    'The expression is alpha–\nbeta.',
    'The expression is alpha—\nbeta.',
    'A statement ends here-\nNew requirements begin.',
])
def test_signs_and_noneligible_boundaries_remain_literal_at_ingest_and_location(source):
    ingested, _ = DocumentChunker._clean_pdf_page(source, set())
    assert ingested == source
    assert not line_wrap_alias_positions(source)
    assert literal_compact(ingested) == literal_compact(source)
    omitted = source.replace('-\n', '').replace('-known', 'known')
    if omitted != source:
        assert not anchor_ranges(source, omitted)


@pytest.mark.parametrize('source, old_anchor', [
    ('The report covers co-\nordinated activities.',
     'The report covers coordinated activities.'),
    ('Employment among U.S.-\nbased businesses grew.',
     'Employment among U.S.based businesses grew.'),
    ('The report covers co-\nordinated part-\ntime activities.',
     'The report covers coordinated part-time activities.'),
    ('The report covers co-\nordinated part-\ntime activities.',
     'The report covers co-ordinated parttime activities.'),
])
def test_legacy_alias_locates_full_original_range_without_rewriting_ingested_text(source, old_anchor):
    assert DocumentChunker._clean_pdf_page(source, set())[0] == source
    assert anchor_ranges(source, old_anchor) == [(0, len(source))]
    raw = pdf([(50, 80, source)])
    for extract in (extract_native_context, extract_native_page_context):
        result = extract(raw, 1, old_anchor)
        assert result and result['text'] == source
        assert result['anchor_match_policy'] == ANCHOR_POLICY_VERSION
        assert result['anchor_match']['members'][0]['source_range'] == [0, len(source)]
        assert result['evidence_sha256'] == text_sha256(source)
        assert result['source_sha256'] == hashlib.sha256(raw).hexdigest()
        assert result['calculator_input_eligible'] is False
        assert native_source_only_contract_valid(result)
        assert extract(raw, 1, old_anchor, max_chars=len(source) - 1) is None


@pytest.mark.parametrize('entries', [
    [(50, 80, 'Employment among U.S.based businesses grew.'),
     (50, 200, 'Employment among U.S.-\nbased businesses grew.')],
    [(50, 80, 'Employment among U.S.-\nbased businesses grew.'),
     (50, 200, 'Employment among U.S.based businesses grew.')],
    [(50, 80, 'The reform is documented. The re-\nform is documented.')],
])
def test_literal_hit_must_not_hide_a_competing_alias_occurrence(entries):
    anchor = 'reform' if len(entries) == 1 else 'Employment among U.S.based businesses grew.'
    raw = pdf(entries)
    assert extract_native_context(raw, 1, anchor) is None
    assert extract_native_page_context(raw, 1, anchor) is None


def test_overlapping_occurrences_are_part_of_the_uniqueness_union():
    assert anchor_ranges('banana', 'ana') == [(1, 4), (3, 6)]
    assert extract_native_context(pdf([(50, 80, 'banana')]), 1, 'ana') is None


def test_alias_can_only_delete_a_proven_native_line_end_not_a_block_boundary():
    raw = pdf([(50, 80, 'A co-'), (50, 200, 'ordinated activity is recorded.')])
    assert extract_native_page_context(raw, 1, 'A coordinated activity') is None
    assert not anchor_ranges('A co-\n\nordinated activity.', 'A coordinated activity.')


def test_literal_cross_block_anchor_does_not_hide_a_page_alias_candidate():
    raw = pdf([(50, 80, 'The reform'), (50, 100, 'is documented.'),
               (50, 220, 'The re-\nform is documented.')])
    assert extract_native_context(raw, 1, 'The reform is documented.') is None
    assert extract_native_page_context(raw, 1, 'The reform is documented.') is None


def test_literal_source_hyphen_never_matches_a_query_that_deleted_an_interior_term_sign():
    assert not anchor_ranges('The well-known activity.', 'The wellknown activity.')
    assert anchor_ranges('The well-known activity.', 'The well-known activity.') == [(0, 24)]


def test_private_use_source_glyph_cannot_collide_with_internal_optional_hyphen_marker():
    source = 'The marker \U000f0000 in co-\nordinated records is literal.'
    anchor = 'The marker \U000f0000 in coordinated records is literal.'
    assert anchor_ranges(source, anchor) == [(0, len(source))]


def _exhaustive_alias_oracle(source, anchor):
    """Independent subset enumeration used only on small matching fixtures."""
    def variants(text):
        eligible = sorted(line_wrap_alias_positions(text))
        for count in range(len(eligible) + 1):
            for removed in combinations(eligible, count):
                offsets = [i for i, char in enumerate(text) if not char.isspace() and i not in removed]
                yield ''.join(text[i] for i in offsets), offsets
    result = set()
    for native, offsets in variants(source):
        for query, _ in variants(anchor):
            if not query:
                continue
            start = 0
            while (left := native.find(query, start)) >= 0:
                result.add((offsets[left], offsets[left + len(query) - 1] + 1))
                start = left + 1
    return sorted(result)


def test_deterministic_alias_matcher_preserves_exhaustive_subset_union_ranges():
    # All combinations of inline literal hyphen, eligible native wrap and
    # joined legacy spelling; some queries also contain a compulsory hyphen
    # immediately beside a word whose other boundary is optional.
    boundaries = ('-', '-\n', '')
    texts = ['co' + a + 'ordinated part' + b + 'time re' + c + 'form.'
             for a, b, c in product(boundaries, repeat=3)]
    for source in texts:
        for anchor in texts:
            assert anchor_ranges(source, anchor) == _exhaustive_alias_oracle(source, anchor)
            page = source + '\n\n' + source.replace('-\n', '')
            assert anchor_ranges(page, anchor) == _exhaustive_alias_oracle(page, anchor)
    for source, anchor in [('ab-\ncd-abcd', 'abcd'), ('banana ba-\nnana', 'ana'),
                           ('pre-\nfix post-\nfix.', '-fix'),
                           ('co-\nordinated.', 'co-\nordinated wrong.')]:
        assert anchor_ranges(source, anchor) == _exhaustive_alias_oracle(source, anchor)


def test_many_optional_wraps_with_a_failed_suffix_do_not_exponentially_backtrack():
    source = 'co-\nordinated ' * 64 + 'end.'
    anchor = 'co-\nordinated ' * 64 + 'wrong.'
    started = time.perf_counter()
    assert anchor_ranges(source, anchor) == []
    # Before atomic disjoint consumption, only 16 wraps took 11 seconds.
    # This generous bound protects navigation resource limits without timing
    # normal PDF extraction or a provider/network call.
    assert time.perf_counter() - started < 2


def test_legacy_jobs_chunk_gets_fresh_literal_evidence_and_pinned_anchor_ranges(tmp_path):
    source = 'Employment among U.S.-\nbased businesses grew.'
    raw = pdf([(50, 80, source)])
    store = KnowledgeStore(tmp_path / 'knowledge')
    store.ingest(raw, document_id='employment', title='Employment', modality='pdf', filename='jobs.pdf')
    hit = next(hit for hit in store.search('Employment businesses') if 'businesses' in hit.snippet)
    identifier = hit.metadata['chunk_id']
    # Emulate a pre-fix stored chunk; its content cannot authorize rewritten
    # generation evidence or suppress a second occurrence on the source page.
    with store.connect() as connection:
        row = connection.execute('SELECT payload FROM chunks WHERE chunk_id=?', (identifier,)).fetchone()
        payload = json.loads(row[0])
        payload['text'] = source.replace('-\n', '')
        connection.execute('UPDATE chunks SET payload=? WHERE chunk_id=?', (json.dumps(payload), identifier))
    hit = next(hit for hit in store.search('Employment businesses') if hit.metadata['chunk_id'] == identifier)
    citations, omitted = store._generation_citations([{'citation_id': 1, **hit.to_dict()}])
    assert citations and not omitted
    evidence = citations[0]['generation_evidence']
    assert evidence['text'] == source
    assert evidence['native_context']['anchor_match']['members'][0]['source_range'] == [0, len(source)]
    store._verify_generation_chunks(citations)
    for field in ('source_sha256', 'text_sha256', 'anchor_match_policy'):
        forged = deepcopy(citations)
        forged[0]['generation_evidence']['native_context'][field] = 'forged'
        with pytest.raises(SourceRevisionError):
            store._verify_generation_chunks(forged)
    forged = deepcopy(citations)
    forged[0]['generation_evidence']['native_context']['anchor_match']['members'][0]['source_range'][0] += 1
    with pytest.raises(SourceRevisionError):
        store._verify_generation_chunks(forged)
    forged = deepcopy(citations)
    forged[0]['generation_evidence']['native_context']['members'][0]['bbox_fitz_unrotated_pt'][0] += 1
    with pytest.raises(SourceRevisionError):
        store._verify_generation_chunks(forged)


def test_new_policy_contract_cannot_relabel_an_old_anchor_proof():
    raw = pdf([(50, 80, 'The reform is documented.')])
    result = extract_native_context(raw, 1, 'reform')
    assert native_source_only_contract_valid(result)
    result['anchor_match_policy'] = 'whitespace_and_printed_alphabetic_line_wrap_hyphen_only'
    assert not native_source_only_contract_valid(result)
    result['anchor_match_policy'] = ANCHOR_POLICY_VERSION
    del result['anchor_match']
    assert not native_source_only_contract_valid(result)
