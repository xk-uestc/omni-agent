"""Fresh PDF geometry/anchor coverage, with no benchmark cases or model calls."""
from copy import deepcopy

import fitz
import pytest

from backend.chunk_cleaning import DocumentChunker
from backend.evidence_context import text_sha256
from backend.native_continuation import continuation_state, enrich_native_line_styles
from backend.pdf_native_context import (extract_native_page_context, _rotated_cell_row,
                                        _tabular_runs)
from backend.native_anchor import ANCHOR_POLICY_VERSION


def native_pdf(entries):
    with fitz.open() as document:
        page = document.new_page(width=600, height=700)
        for x, y, text in entries:
            page.insert_text((x, y), text, fontsize=10)
        return document.tobytes()


def split_table_pdf(*, forecast=False, rows=3):
    entries = [(80, 80, 'Table 7. Annual service records'),
               (80, 100, 'Service'), (240, 100, 'Budget'), (350, 100, 'Actual')]
    if forecast:
        entries = [(80, 35, 'Forecast data.'), (80, 55, 'Units: thousands USD.')] + entries
    for index in range(rows):
        entries.extend([(80, 110 + 28 * index, f'Service-{index + 1}'),
                        (240, 110 + 28 * index, str(31 + index)),
                        (350, 110 + 28 * index, str(42 + index))])
    return native_pdf(entries)


def rotated_row_pdf(*, cells=3, page_rotation=90):
    values = [(650, 'Overall precision'), (440, 'Field samples'),
              (260, 'The result is at most 35 percent when both samples are detected')]
    with fitz.open() as document:
        page = document.new_page(width=600, height=700)
        for y, text in values[-cells:]:
            page.insert_text((150, y), text, fontsize=8, rotate=90)
        page.set_rotation(page_rotation)
        return document.tobytes()


def test_ingestion_line_wrap_equivalence_locates_original_without_rewriting_it():
    raw = native_pdf([(50, 80, 'The report covers co-\nordinated activities.')])
    result = extract_native_page_context(raw, 1, 'The report covers coordinated activities.')
    assert result and result['text'] == 'The report covers co-\nordinated activities.'
    assert result['text_sha256'] == text_sha256(result['text'])
    assert result['anchor_match_policy'] == ANCHOR_POLICY_VERSION
    assert not result['calculator_input_eligible']
    assert result == extract_native_page_context(raw, 1, 'The report covers coordinated activities.')


@pytest.mark.parametrize('source,anchor', [
    ('The report covers co-ordinated activities.', 'The report covers coordinated activities.'),
    ('The interval is 3-\n5 percent.', 'The interval is 35 percent.'),
    ('The report covers co-\nordinated activities.', 'The report covers uncoordinated activities.'),
])
def test_anchor_match_does_not_remove_interior_hyphens_digits_or_change_words(source, anchor):
    assert extract_native_page_context(native_pdf([(50, 80, source)]), 1, anchor) is None


def test_unclosed_noun_phrase_cannot_drop_its_following_condition():
    assert continuation_state('The policy is active. The limit') == 'unknown'
    raw = native_pdf([(50, 80, 'The policy is active. The limit'),
                      (50, 100, 'applies only to certified components. Other records follow.')])
    result = extract_native_page_context(raw, 1, 'The policy is active. The limit')
    assert result and result['mode'] == 'original_native_complete_continuation'
    assert result['text'].endswith('applies only to certified components.')
    assert 'Other records' not in result['text']
    assert extract_native_page_context(raw, 1, 'The policy is active. The limit',
                                       max_chars=len(result['text']) - 1) is None


def test_separate_numeric_font_runs_within_prose_do_not_create_a_table():
    lines = []
    for index in range(3):
        y = 50 + index * 15
        lines.extend([{'text': 'The baseline power is', 'bbox': [20, y, 115, y + 12]},
                      {'text': str(index + 1), 'bbox': [117, y, 123, y + 12]},
                      {'text': 'megawatts.', 'bbox': [125, y, 178, y + 12]}])
    assert not _tabular_runs({'native_lines': lines})
    for line in lines:
        if line['text'].isdigit():
            line['bbox'][0] += 20
            line['bbox'][2] += 20
    # Moving only the number across the suffix produces overlapping runs,
    # not a repeated independent cell gutter.
    assert not _tabular_runs({'native_lines': lines})


def test_narrow_caption_recovers_all_independent_rows_in_physical_order():
    raw = split_table_pdf()
    result = extract_native_page_context(raw, 1, 'Annual service records')
    assert result and result['mode'] == 'original_native_complete_captioned_table_region'
    assert result['text'] == ('Table 7. Annual service records\nService Budget Actual\n'
                              'Service-1 31 42\nService-2 32 43\nService-3 33 44')
    assert len(result['members']) >= 4 and len(result['native_row_layout']) == 5
    assert not result['calculator_input_eligible']
    assert result == extract_native_page_context(raw, 1, 'Annual service records')
    assert extract_native_page_context(raw, 1, 'Service-2 32 43')['text'] == result['text']
    assert extract_native_page_context(raw, 1, 'Annual service records',
                                       max_chars=len(result['text']) - 1) is None


def test_caption_expansion_preserves_declarations_and_has_no_budget_prefix_fallback():
    raw = split_table_pdf(forecast=True)
    result = extract_native_page_context(raw, 1, 'Annual service records')
    assert result and result['text'].startswith('Forecast data.\nUnits: thousands USD.')
    assert 'Service-3 33 44' in result['text']
    assert extract_native_page_context(raw, 1, 'Annual service records',
                                       max_chars=len(result['text']) - 1) is None


def test_distant_same_baseline_sidebar_cannot_be_inserted_as_table_cells():
    with fitz.open(stream=split_table_pdf(), filetype='pdf') as document:
        page = document[0]
        for y, text in ((100, 'RELATED NOTES'), (110, 'Case alpha'),
                        (138, 'Case beta'), (166, 'Case gamma')):
            page.insert_text((480, y), text, fontsize=10)
        raw = document.tobytes()
    # MuPDF can place the notes and table header into one physical block.
    # Fail closed instead of cropping that block or treating the sidebar as a
    # new table column, and do not fall back to the isolated caption.
    assert extract_native_page_context(raw, 1, 'Annual service records') is None


def test_rotated_conditional_table_row_preserves_all_literal_cells_and_source_boxes():
    raw = rotated_row_pdf()
    result = extract_native_page_context(raw, 1, 'The result is at most 35 percent')
    assert result and result['mode'] == 'original_native_complete_rotated_row'
    assert 'when both samples are detected' in result['text']
    assert 'Field samples' in result['text'] and 'Overall precision' in result['text']
    proof = result['rotated_native_row']
    assert proof['page_rotation_degrees'] == 90
    assert len(proof['display_cell_components_pt']) == 3
    assert proof['semantic_row_column_binding_verified'] is False
    assert result['calculator_input_eligible'] is False
    assert result['unit_insertions'] == []
    assert all(line['native_direction'] == (0.0, -1.0) for line in result['members'][0]['lines'])
    assert result == extract_native_page_context(raw, 1, 'The result is at most 35 percent')
    assert extract_native_page_context(raw, 1, 'The result is at most 35 percent',
                                       max_chars=len(result['text']) - 1) is None


@pytest.mark.parametrize('kwargs', [{'cells': 2}, {'page_rotation': 0}, {'page_rotation': 270}])
def test_rotated_prose_and_unproven_orientation_do_not_bypass_sentence_closure(kwargs):
    assert extract_native_page_context(rotated_row_pdf(**kwargs), 1,
                                       'The result is at most 35 percent') is None


def test_rotated_row_requires_fresh_direction_and_overlapping_display_cells():
    with fitz.open(stream=rotated_row_pdf(), filetype='pdf') as document:
        page = document[0]
        matrix = tuple(page.rotation_matrix)
        page.set_rotation(0)
        blocks = DocumentChunker._pdf_text_blocks(page)
        enrich_native_line_styles(blocks, page)
    row = blocks[0]
    assert _rotated_cell_row(row, 90, matrix)
    changed = deepcopy(row)
    changed['native_lines'][0]['native_direction'] = (1.0, 0.0)
    assert _rotated_cell_row(changed, 90, matrix) is None
    changed = deepcopy(row)
    del changed['native_lines'][0]['native_direction']
    assert _rotated_cell_row(changed, 90, matrix) is None
    changed = deepcopy(row)
    changed['native_lines'][0]['bbox'][0] += 30
    changed['native_lines'][0]['bbox'][2] += 30
    assert _rotated_cell_row(changed, 90, matrix) is None


@pytest.mark.parametrize('kind', ['split_table', 'rotated_row'])
def test_store_replays_new_geometric_proof_and_rejects_tampered_geometry(tmp_path, kind):
    from backend.knowledge_store import KnowledgeStore, SourceRevisionError
    raw = split_table_pdf() if kind == 'split_table' else rotated_row_pdf()
    question = 'Service-2 Budget Actual' if kind == 'split_table' else '35 percent when both samples are detected'
    store = KnowledgeStore(tmp_path / kind)
    store.ingest(raw, document_id=kind, title=kind, modality='pdf',
                 filename=kind + '.pdf', language='eng')
    hits = store.search(question, top_k=4)
    citations, _ = store._generation_citations([
        {'citation_id': index, **hit.to_dict()} for index, hit in enumerate(hits, 1)])
    contexts = [c['generation_evidence']['native_context'] for c in citations
                if c.get('generation_evidence', {}).get('native_context')]
    mode = ('original_native_complete_captioned_table_region' if kind == 'split_table'
            else 'original_native_complete_rotated_row')
    context = next(c for c in contexts if c['mode'] == mode)
    store._verify_generation_chunks(citations)
    assert context['calculator_input_eligible'] is False
    if kind == 'split_table':
        context['native_row_layout'][0][0]['bbox_fitz_unrotated_pt'][0] += 1
    else:
        context['rotated_native_row']['semantic_row_column_binding_verified'] = True
    with pytest.raises(SourceRevisionError):
        store._verify_generation_chunks(citations)
