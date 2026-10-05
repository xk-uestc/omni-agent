"""Independent synthetic native row blocks; no official answers or API calls."""
import hashlib

import fitz
import pytest

from backend.pdf_native_context import extract_native_page_context
from backend.answer_contract import native_source_only_contract_valid


def row_block_pdf(*, header=True, closed=True, wrong_lane=False, sidebar=False,
                  competing=False, rows=3, annotation=False, scope=True):
    with fitz.open() as document:
        page = document.new_page(width=650, height=720)
        if scope:
            page.insert_text((40, 45), 'Specimen R-17. Collected 2026-07-02.', fontsize=10)
        lanes = [40, 220, 365, 510]
        if header:
            for x, value in zip(lanes, ['Parameter', 'Reported result', 'Operator', 'Protocol']):
                page.insert_text((x, 90), value, fontsize=10)
        for index in range(rows):
            y = 112 + index * 24 + (24 if annotation and index >= 1 else 0)
            if annotation and index == 1:
                page.insert_text((40, y - 24), 'Provisional qualifier', fontsize=8)
            for cell, (x, value) in enumerate(zip(lanes, [f'Analyte-{index + 1}',
                     f'<{12 + index}.00 mg/L', ['Avery', 'Blair', 'Casey'][index % 3], 'Procedure Q'])):
                page.insert_text((x + (18 if wrong_lane and index == 1 and cell == 2 else 0), y),
                                 value, fontsize=10)
            if sidebar and index == 1:
                page.insert_text((600, y), 'Other', fontsize=8)
        y = 112 + rows * 24 + (24 if annotation else 0)
        if competing:
            for x, value in zip(lanes, ['Category', 'Value', 'Owner', 'Method']):
                page.insert_text((x, y), value, fontsize=10)
            y += 24
        if closed:
            page.insert_text((40, y), 'These results apply only to the identified specimen and remain provisional.',
                             fontsize=10)
        return document.tobytes()


def test_header_and_every_row_reconstruct_one_complete_region_with_scope():
    raw = row_block_pdf(annotation=True)
    contexts = [extract_native_page_context(raw, 1, anchor, 3200) for anchor in
                ['Parameter Reported result Operator Protocol', 'Analyte-1', 'Analyte-2', 'Analyte-3']]
    assert all(contexts)
    result = contexts[0]
    assert all(context['text'] == result['text'] for context in contexts)
    assert result == extract_native_page_context(raw, 1, 'Parameter Reported result Operator Protocol', 3200)
    assert result['mode'] == 'original_native_complete_uncaptioned_table_region'
    assert result['extraction_version'] == 'original-native-bounded-page-region-v4'
    assert result['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert 'Specimen R-17. Collected 2026-07-02.' in result['text']
    assert 'Provisional qualifier' in result['text']
    assert 'identified specimen and remain provisional.' in result['text']
    for index, actor in enumerate(['Avery', 'Blair', 'Casey'], 1):
        assert f'Analyte-{index} <{11 + index}.00 mg/L {actor} Procedure Q' in result['text']
    witness = result['uncaptioned_native_region']
    assert len(witness['row_block_ids']) == 3
    assert len(witness['annotation_block_ids']) == 1
    assert len(witness['scope_block_ids']) == 2
    assert witness['page_local_only'] is True
    assert witness['semantic_row_column_binding_verified'] is False
    assert result['calculator_input_eligible'] is False
    assert native_source_only_contract_valid(result)
    assert result['native_row_layout']
    assert all(member['lines'] for member in result['members'])


def test_two_closed_rows_use_the_same_detector_and_source_contract():
    result = extract_native_page_context(row_block_pdf(rows=2), 1, 'Analyte-1', 3200)
    assert result and native_source_only_contract_valid(result)
    assert len(result['uncaptioned_native_region']['row_block_ids']) == 2


@pytest.mark.parametrize('kwargs', [
    {'header': False}, {'closed': False}, {'wrong_lane': True},
    {'sidebar': True}, {'competing': True}, {'rows': 20},
])
def test_ambiguous_missing_open_or_oversized_regions_have_no_small_row_fallback(kwargs):
    raw = row_block_pdf(**kwargs)
    assert extract_native_page_context(raw, 1, 'Analyte-1', 3200) is None
    if kwargs.get('header', True):
        assert extract_native_page_context(raw, 1, 'Parameter Reported result Operator Protocol', 3200) is None


def test_budget_refusal_preserves_full_units_signs_scope_and_last_row():
    raw = row_block_pdf()
    result = extract_native_page_context(raw, 1, 'Analyte-1', 3200)
    assert result
    assert extract_native_page_context(raw, 1, 'Analyte-1', len(result['text']) - 1) is None
    assert extract_native_page_context(raw, 1, 'Analyte-3', len(result['text']) - 1) is None


def test_missing_scope_is_not_fabricated_and_page_local_witness_stays_explicit():
    raw = row_block_pdf(scope=False)
    result = extract_native_page_context(raw, 1, 'Analyte-2', 3200)
    assert result and len(result['uncaptioned_native_region']['scope_block_ids']) == 1
    assert 'Specimen R-17' not in result['text']
    assert result['uncaptioned_native_region']['page_local_only'] is True


def test_duplicate_anchor_and_numeric_qualifier_cannot_be_dropped():
    raw = row_block_pdf(annotation=True)
    with fitz.open(stream=raw, filetype='pdf') as document:
        document[0].insert_text((40, 310), 'Analyte-1', fontsize=10)
        repeated = document.tobytes()
    assert extract_native_page_context(repeated, 1, 'Analyte-1', 3200) is None
    with fitz.open(stream=raw, filetype='pdf') as document:
        # An extra same-baseline run changes the annotation geometry; do not
        # discard the run simply because it could look like a numeric cell.
        document[0].insert_text((220, 136), '999 mg/L', fontsize=8)
        altered = document.tobytes()
    assert extract_native_page_context(altered, 1, 'Analyte-1', 3200) is None


def test_wrapped_row_does_not_fall_back_to_isolated_native_block():
    raw = row_block_pdf()
    with fitz.open(stream=raw, filetype='pdf') as document:
        document[0].insert_text((40, 124), 'extra wrapped label', fontsize=10)
        altered = document.tobytes()
    assert extract_native_page_context(altered, 1, 'Analyte-1', 3200) is None
    assert extract_native_page_context(altered, 1, 'Parameter Reported result Operator Protocol', 3200) is None


def test_page_footer_cannot_close_an_open_table():
    raw = row_block_pdf(closed=False)
    with fitz.open(stream=raw, filetype='pdf') as document:
        document[0].insert_text((510, 700), '1 of 2', fontsize=10)
        altered = document.tobytes()
    assert extract_native_page_context(altered, 1, 'Analyte-1', 3200) is None
