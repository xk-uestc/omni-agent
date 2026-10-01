"""Original PDF context tests independent of official benchmark answers."""
import fitz
import pytest

from backend.pdf_native_context import extract_native_context
from backend.evidence_context import text_sha256


def pdf(entries, *, rotation=0, crop=False):
    document = fitz.open()
    page = document.new_page(width=612, height=792)
    for x, y, text in entries:
        page.insert_text((x, y), text, fontsize=10)
    if crop:
        page.set_cropbox(fitz.Rect(20, 30, 592, 762))
    page.set_rotation(rotation)
    raw = document.tobytes()
    document.close()
    return raw


def test_containing_block_recovers_label_and_name_without_trusting_chunk_metadata():
    raw = pdf([(45, 100, 'LEAD ENGINEER'), (45, 112, 'Jane A. Smith')])
    result = extract_native_context(raw, 1, 'Jane A. Smith')
    assert result and result['text'] == 'LEAD ENGINEER\nJane A. Smith'
    assert len(result['members']) == 1
    assert result['text_sha256'] == text_sha256(result['text'])
    assert not result['calculator_input_eligible']


def test_unrelated_column_is_excluded():
    raw = pdf([(45, 100, 'Left column contains the unique requested fact.'),
               (330, 100, 'Right column describes an unrelated independent fact.'),
               (45, 120, 'Left column continues here with another statement.'),
               (330, 120, 'Right column continues with a separate statement.')])
    result = extract_native_context(raw, 1, 'unique requested fact')
    assert result and 'Right column' not in result['text']


def test_repeated_anchor_across_blocks_is_not_unique():
    raw = pdf([(45, 100, 'Budget 30.83'), (45, 300, 'Budget 30.83')])
    assert extract_native_context(raw, 1, '30.83') is None


def test_complete_block_budget_never_returns_a_prefix():
    raw = pdf([(45, 100, 'LEAD ENGINEER'), (45, 112, 'Jane A. Smith')])
    assert extract_native_context(raw, 1, 'Jane A. Smith', max_chars=15) is None


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
@pytest.mark.parametrize('crop', [False, True])
def test_original_unrotated_native_coordinates_and_context(rotation, crop):
    raw = pdf([(45, 100, 'LEAD ENGINEER'), (45, 112, 'Jane A. Smith')], rotation=rotation, crop=crop)
    result = extract_native_context(raw, 1, 'Jane A. Smith')
    assert result and result['text'] == 'LEAD ENGINEER\nJane A. Smith'
    assert result['members'][0]['bbox_fitz_unrotated_pt'][0] == (25 if crop else 45)
    assert result == extract_native_context(raw, 1, 'Jane A. Smith')


def test_separate_forecast_heading_is_preserved():
    raw = pdf([(45, 60, 'Forecast data.'), (45, 150, 'Revenue is 30.83 dollars.')])
    result = extract_native_context(raw, 1, 'Revenue is 30.83 dollars.')
    assert result and result['text'].startswith('Forecast data.\n')
    assert len(result['members']) == 2


def test_actual_heading_resets_forecast_scope():
    raw = pdf([(45, 60, 'Forecast data.'), (45, 100, 'Actual results.'),
               (45, 150, 'Revenue is 30.83 dollars.')])
    result = extract_native_context(raw, 1, 'Revenue is 30.83 dollars.')
    assert result and result['text'] == 'Revenue is 30.83 dollars.'


def test_forecast_in_other_column_does_not_taint_anchor():
    raw = pdf([(330, 60, 'Forecast data.'), (45, 150, 'Revenue is 30.83 dollars.')])
    result = extract_native_context(raw, 1, 'Revenue is 30.83 dollars.')
    assert result and result['text'] == 'Revenue is 30.83 dollars.'


def test_original_units_are_not_inferred_or_expanded(monkeypatch):
    # Controlled native block boundary fixture: actual PDF engines can merge
    # physically touching runs. Exercise only the geometric join decision.
    from backend.chunk_cleaning import DocumentChunker
    def block(index, text, bbox):
        return {'kind': 'text', 'block_id': index, 'native_block_id': index,
                'normalized_text': text, 'char_count': len(text), 'text_sha256': text_sha256(text),
                'bbox_fitz_unrotated_pt': bbox, 'native_lines': [{'text': text, 'bbox': bbox}]}
    blocks = [block(0, '$30.83', [45, 90, 80, 103]), block(1, 'm', [82, 90, 90, 103]),
              block(2, 'dollars', [200, 90, 235, 103])]
    monkeypatch.setattr(DocumentChunker, '_pdf_text_blocks', staticmethod(lambda page: blocks))
    result = extract_native_context(pdf([(45, 100, '$30.83')]), 1, '$30.83')
    assert result['text'] == '$30.83 m' and 'million' not in result['text']
    assert len(result['members']) == 2 and result['unit_insertions']
    blocks[1]['bbox_fitz_unrotated_pt'] = [82, 120, 90, 133]
    assert extract_native_context(pdf([(45, 100, '$30.83')]), 1, '$30.83')['text'] == '$30.83'
    blocks[1]['bbox_fitz_unrotated_pt'] = [82, 90, 90, 103]
    blocks.append(block(3, 'bn', [82, 90, 94, 103]))
    assert extract_native_context(pdf([(45, 100, '$30.83')]), 1, '$30.83')['text'] == '$30.83'


def test_actual_pdf_touching_amount_unit_preserves_literal_unit():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((45, 100), '$30.83', fontsize=10)
    x = 45 + fitz.get_text_length('$30.83', fontsize=10) + 2
    page.insert_text((x, 100), 'm', fontsize=10)
    raw = document.tobytes()
    document.close()
    result = extract_native_context(raw, 1, '$30.83')
    assert result and result['text'] == '$30.83 m'


def test_grouped_label_and_numeric_native_runs_join_separate_unit(monkeypatch):
    from backend.chunk_cleaning import DocumentChunker
    label = 'TOTAL EXAMPLE ASSISTANCE:'
    text = label + ' $30.83'
    blocks = [
        {'kind': 'text', 'block_id': 0, 'native_block_id': 0,
         'normalized_text': text, 'char_count': len(text), 'text_sha256': text_sha256(text),
         'bbox_fitz_unrotated_pt': [45, 90, 280, 103],
         'native_lines': [{'text': label, 'bbox': [45, 90, 200, 103]},
                          {'text': '$30.83', 'bbox': [245, 90, 280, 103]}]},
        {'kind': 'text', 'block_id': 1, 'native_block_id': 1,
         'normalized_text': 'm', 'char_count': 1, 'text_sha256': text_sha256('m'),
         'bbox_fitz_unrotated_pt': [282, 90, 290, 103],
         'native_lines': [{'text': 'm', 'bbox': [282, 90, 290, 103]}]},
    ]
    monkeypatch.setattr(DocumentChunker, '_pdf_text_blocks', staticmethod(lambda page: blocks))
    result = extract_native_context(pdf([(45, 100, '$30.83')]), 1, '$30.83')
    assert result and result['text'] == text + ' m'
    assert len(result['members']) == 2 and len(result['members'][0]['lines']) == 2
    assert result['unit_insertions']
    # A later native run on that baseline means the amount isn't the
    # rightmost suffix; no unit may be spliced into the middle of its fact.
    blocks[0]['native_lines'].append({'text': 'conditional', 'bbox': [281, 90, 300, 103]})
    blocks[0]['normalized_text'] += ' conditional'
    result = extract_native_context(pdf([(45, 100, '$30.83')]), 1, '$30.83')
    assert not result['unit_insertions']


def test_actual_pdf_separate_label_and_amount_runs_keep_full_physical_line():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((45, 100), 'TOTAL EXAMPLE ASSISTANCE:', fontsize=10)
    page.insert_text((245, 100), '$30.83', fontsize=10)
    unit_x = 245 + fitz.get_text_length('$30.83', fontsize=10) + 2
    page.insert_text((unit_x, 100), 'm', fontsize=10)
    raw = document.tobytes()
    document.close()
    result = extract_native_context(raw, 1, '$30.83')
    assert result and result['text'] == 'TOTAL EXAMPLE ASSISTANCE: $30.83 m'


@pytest.mark.parametrize('page_no, anchor', [(0, 'text'), (2, 'text'), (1, ''), (1, 'missing')])
def test_invalid_or_missing_anchor_fails_closed(page_no, anchor):
    assert extract_native_context(pdf([(45, 100, 'text')]), page_no, anchor) is None
