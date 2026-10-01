"""Offline native-grid facts and counterexamples; no APIs or external PDFs."""
import hashlib
from copy import deepcopy

import fitz
import pytest

from backend.visual_tables import VisualTableError, extract_pdf_tables, lookup_table_fact


ROWS = [['Area', '2025 Actual', '2026 Forecast'],
        ['Alpha', '125', '250'], ['Beta', '125', '300'], ['Gamma', '80', '160']]


def draw_grid(page, x=70, y=70, rows=None, *, border=True, merged_header=False, partial_word=False):
    rows = rows or ROWS
    for col in range(4):
        if border:
            start = y + 35 if merged_header and col == 2 else y
            page.draw_line((x + col * 100, start), (x + col * 100, y + len(rows) * 35))
    for row in range(len(rows) + 1):
        if border:
            page.draw_line((x, y + row * 35), (x + 300, y + row * 35))
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if text:
                position = (x + 5 + c * 100, y + 20 + r * 35)
                if partial_word and r == 1 and c == 1:
                    position = (x + 191, y + 20 + r * 35)
                page.insert_text(position, text, fontsize=9)


def make_pdf(rotation=0, crop=False, *, rows=None, border=True, merged_header=False, partial_word=False, adjacent=False):
    with fitz.open() as document:
        page = document.new_page(width=800 if adjacent else 420, height=320)
        draw_grid(page, rows=rows, border=border, merged_header=merged_header, partial_word=partial_word)
        if adjacent:
            draw_grid(page, x=410, rows=[['Area', '2025 Actual', '2026 Forecast'],
                                        ['Alpha', '777', '888'], ['Beta', '125', '999'], ['Gamma', '80', '160']])
        if crop:
            page.set_cropbox(fitz.Rect(40, 30, 390, 270))
        page.set_rotation(rotation)
        return document.tobytes()


def extract(raw, **kwargs):
    return extract_pdf_tables(raw, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest(), **kwargs)


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('crop', [False, True])
def test_rotation_cropbox_preserve_table_header_row_value_and_true_pdf_coordinates(rotation, crop):
    raw = make_pdf(rotation, crop)
    result = extract(raw)
    assert result['status'] == 'layout_verified', result
    assert len(result['tables']) == 1
    table = result['tables'][0]
    assert table['row_count'] == 4 and table['column_count'] == 3
    assert table['column_header_paths'] == [['Area'], ['2025 Actual'], ['2026 Forecast']]
    assert table['row_headers'] == ['Alpha', 'Beta', 'Gamma']
    assert [[cell['raw_text'] for cell in row] for row in table['cells']] == ROWS
    # PDF user space bottom-left coordinates are checked against the actual
    # inserted grid, not merely two mutually inverse but wrong matrices.
    assert table['bbox_pdf_user_pt'] == pytest.approx([70, 110, 370, 250])
    matrix = fitz.Matrix(*result['mappings']['fitz_unrotated_to_display'])
    pdf_matrix = fitz.Matrix(*result['mappings']['pdf_user_to_fitz_unrotated'])
    for row in table['cells']:
        for cell in row:
            assert list(fitz.Rect(cell['bbox_fitz_unrotated_pt']) * matrix) == pytest.approx(cell['bbox_display_pt'])
            assert list(fitz.Rect(cell['bbox_pdf_user_pt']) * pdf_matrix) == pytest.approx(cell['bbox_fitz_unrotated_pt'])
    fact = lookup_table_fact(result, table_id=table['table_id'], row_header='Beta', column_header_path=['2025 Actual'])
    assert fact['raw_value'] == '125' and fact['fact_key']['row_index'] == 2
    assert fact['fact_key']['column_index'] == 1
    assert fact['validation_scope'] == 'native_text_layer_not_visible_ocr'
    assert fact['numeric_validation'] == 'not_parsed_or_unit_bound'
    # Extracting does not change the caller's source bytes or PDF Rotate.
    with fitz.open(stream=raw, filetype='pdf') as original:
        assert original[0].rotation == rotation


def test_repeated_values_remain_different_full_fact_keys():
    result = extract(make_pdf())
    table = result['tables'][0]
    a = lookup_table_fact(result, table_id=table['table_id'], row_header='Alpha', column_header_path=['2025 Actual'])
    b = lookup_table_fact(result, table_id=table['table_id'], row_header='Beta', column_header_path=['2025 Actual'])
    assert a['raw_value'] == b['raw_value'] == '125'
    assert a['fact_id'] != b['fact_id'] and a['value_word_ids'] != b['value_word_ids']
    assert a['fact_key'] != b['fact_key']


def test_adjacent_tables_never_share_a_cell_or_header_binding():
    result = extract(make_pdf(adjacent=True))
    assert len(result['tables']) == 2, result
    first, second = result['tables']
    a = lookup_table_fact(result, table_id=first['table_id'], row_header='Alpha', column_header_path=['2025 Actual'])
    b = lookup_table_fact(result, table_id=second['table_id'], row_header='Alpha', column_header_path=['2025 Actual'])
    assert a['raw_value'] == '125' and b['raw_value'] == '777'
    assert a['table_id'] != b['table_id']
    assert not set(a['value_word_ids']) & set(b['value_word_ids'])
    with pytest.raises(VisualTableError, match='table_not_uniquely'):
        lookup_table_fact(result, table_id='invented-table', row_header='Alpha', column_header_path=['2025 Actual'])


@pytest.mark.parametrize('header', [['2025'], ['Actual'], ['2026 Forecast', '2025 Actual'], ['2025 actual'], []])
def test_actual_forecast_header_cannot_be_shortened_reordered_or_guessed(header):
    result = extract(make_pdf())
    with pytest.raises(VisualTableError):
        lookup_table_fact(result, table_id=result['tables'][0]['table_id'], row_header='Alpha', column_header_path=header)


def test_forecast_value_remains_in_forecast_column():
    result = extract(make_pdf())
    fact = lookup_table_fact(result, table_id=result['tables'][0]['table_id'], row_header='Alpha', column_header_path=['2026 Forecast'])
    assert fact['raw_value'] == '250'
    assert fact['fact_key']['column_index'] == 2


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_complete_crop_selects_original_table_partial_crop_abstains(rotation):
    raw = make_pdf(rotation, True)
    full = extract(raw)
    bbox = full['tables'][0]['bbox_display_pt']
    cropped = extract(raw, crop_display_pt=bbox)
    assert cropped['status'] == 'layout_verified'
    assert cropped['tables'][0]['column_header_paths'] == full['tables'][0]['column_header_paths']
    smaller = [bbox[0] + 10, bbox[1] + 10, bbox[2], bbox[3]]
    partial = extract(raw, crop_display_pt=smaller)
    assert partial['tables'] == [] and partial['status'] == 'relation_unverified'
    assert 'partial_table_crop' in partial['rejected_tables'][0]['reasons']


def test_borderless_table_is_not_upgraded_from_word_proximity():
    result = extract(make_pdf(border=False))
    assert result['tables'] == [] and result['status'] == 'relation_unverified'
    assert 'no_strict_line_grid_detected' in result['warnings']
    assert result['native_words']


def test_merged_parent_header_abstains_instead_of_dropping_a_header_level():
    result = extract(make_pdf(merged_header=True))
    assert result['tables'] == []
    assert any('merged' in reason for table in result['rejected_tables'] for reason in table['reasons'])


@pytest.mark.parametrize('rows', [
    [['Area', '2025', '2026'], *ROWS[1:]],
    [['Area', 'Amount', 'Amount'], *ROWS[1:]],
    [['Area', '', '2026 Forecast'], *ROWS[1:]],
    [ROWS[0], ['Alpha', '125', '250'], ['Alpha', '125', '300']],
    [ROWS[0], ['', '125', '250'], ['Beta', '125', '300']],
    [ROWS[0], ['Period', 'Actual', 'Forecast'], *ROWS[1:]],
    [['Area', 'Actual', 'Forecast'], ['Year', '2025', '2026'], *ROWS[1:]],
])
def test_ambiguous_headers_rows_and_text_header_layers_abstain(rows):
    result = extract(make_pdf(rows=rows))
    assert result['tables'] == [] and result['status'] == 'relation_unverified'
    assert result['rejected_tables']


def test_word_crossing_real_grid_boundary_is_not_assigned_to_nearest_cell():
    result = extract(make_pdf(partial_word=True))
    assert result['tables'] == []
    assert 'partial_or_cross_cell_word' in result['rejected_tables'][0]['reasons']


def test_missing_native_text_is_not_ocr_or_fabricated_numeric_data():
    with fitz.open() as document:
        page = document.new_page(width=420, height=320)
        for x in [70, 170, 270, 370]:
            page.draw_line((x, 70), (x, 210))
        for y in [70, 105, 140, 175, 210]:
            page.draw_line((70, y), (370, y))
        raw = document.tobytes()
    result = extract(raw)
    assert result['native_words'] == [] and result['tables'] == []
    assert result['status'] == 'relation_unverified'


def test_same_raw_pdf_and_selector_are_deterministic_source_change_invalidates_ids():
    raw = make_pdf()
    assert extract(raw) == extract(raw)
    another = make_pdf(rows=[ROWS[0], ['Alpha', '126', '250'], *ROWS[2:]])
    assert extract(raw)['evidence_id'] != extract(another)['evidence_id']
    with pytest.raises(VisualTableError, match='source_sha256_mismatch'):
        extract_pdf_tables(another, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest())


def test_lookup_returns_detached_literal_fact():
    result = extract(make_pdf())
    original = deepcopy(result)
    fact = lookup_table_fact(result, table_id=result['tables'][0]['table_id'], row_header='Alpha', column_header_path=['2025 Actual'])
    fact['fact_key']['column_header_path'].append('invented')
    assert result == original


@pytest.mark.parametrize('kwargs', [
    {'max_source_bytes': 1}, {'max_native_words': 1}, {'max_drawing_items': 1},
    {'max_cells': 1}, {'max_tables': True}, {'max_pages': 0},
    {'crop_display_pt': [0, 0, 10000, 10]}, {'crop_display_pt': [0, 0, float('nan'), 10]},
    {'crop_display_pt': [10, 10, 0, 0]}, {'crop_display_pt': 'https://example.com'},
])
def test_budgets_and_invalid_crop_are_rejected(kwargs):
    with pytest.raises(VisualTableError):
        extract(make_pdf(), **kwargs)


def test_drawing_budget_prevents_table_detection(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('table detector should not run')
    monkeypatch.setattr(fitz.Page, 'find_tables', forbidden)
    with pytest.raises(VisualTableError, match='before table detection'):
        extract(make_pdf(), max_drawing_items=1)


def test_neighboring_tables_obey_table_budget():
    with pytest.raises(VisualTableError, match='table budget'):
        extract(make_pdf(adjacent=True), max_tables=1)


@pytest.mark.parametrize('page_no', [0, True, 2])
def test_invalid_page_is_not_reinterpreted(page_no):
    raw = make_pdf()
    with pytest.raises(VisualTableError):
        extract_pdf_tables(raw, page_no=page_no, expected_source_sha256=hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize('raw', [b'', b'not-pdf', 'C:/private.pdf', 'https://example.com/file.pdf'])
def test_only_pinned_bytes_are_accepted(raw):
    with pytest.raises(VisualTableError):
        extract_pdf_tables(raw, page_no=1, expected_source_sha256='0' * 64)


def test_encrypted_and_over_page_budget_fail():
    with fitz.open() as document:
        document.new_page()
        document.new_page()
        raw = document.tobytes()
        encrypted = document.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='user')
    with pytest.raises(VisualTableError, match='page-count'):
        extract(raw, max_pages=1)
    with pytest.raises(VisualTableError, match='encrypted'):
        extract(encrypted)
