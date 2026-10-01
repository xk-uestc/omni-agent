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


def test_real_pdf_open_prose_continues_only_to_first_closed_sentence():
    raw = pdf([(45, 80, 'Only approved orders.\nThe policy does not cover'),
               (45, 127, 'water damage. Other unrelated details are omitted.')])
    result = extract_native_context(raw, 1, 'The policy does not cover')
    assert result and result['text'] == 'Only approved orders.\nThe policy does not cover\nwater damage.'
    assert result['mode'] == 'original_native_complete_continuation'
    assert len(result['members']) == 2
    assert result['members'][1]['source_range'] == [0, len('water damage.')]
    assert result['members'][1]['lines'][0]['selected_range'] == [0, len('water damage.')]
    assert result['source_sha256'] and not result['calculator_input_eligible']


def test_real_pdf_large_continuation_block_does_not_require_whole_block_in_budget():
    suffix = 'water damage.\n' + '\n'.join(f'Unrelated supplementary information number {i} is recorded separately.' for i in range(40))
    raw = pdf([(45, 80, 'Only approved orders.\nThe policy does not cover'), (45, 127, suffix)])
    result = extract_native_context(raw, 1, 'The policy does not cover', max_chars=120)
    assert result and result['text'].endswith('\nwater damage.')
    assert 'supplementary' not in result['text']
    assert extract_native_context(raw, 1, 'The policy does not cover', max_chars=60) is None


@pytest.mark.parametrize('following', [
    'New results:\nwater damage.',
    'water damage without a closed sentence',
])
def test_real_pdf_open_prose_with_unverified_continuation_returns_no_fragment(following):
    raw = pdf([(45, 80, 'Only approved orders.\nThe policy does not cover'), (45, 127, following)])
    assert extract_native_context(raw, 1, 'The policy does not cover') is None


def test_real_pdf_open_prose_without_successor_is_not_a_complete_answer():
    raw = pdf([(45, 100, 'Only approved orders.\nThe policy does not cover')])
    assert extract_native_context(raw, 1, 'The policy does not cover') is None


def test_real_pdf_forecast_heading_and_whole_condition_survive_continuation():
    raw = pdf([(45, 40, 'Forecast data.'),
               (45, 100, 'Only approved orders.\nProjected revenue may reach'),
               (45, 147, '100 dollars. The later amount is unrelated.')])
    result = extract_native_context(raw, 1, 'Projected revenue may reach')
    assert result and result['text'] == 'Forecast data.\nOnly approved orders.\nProjected revenue may reach\n100 dollars.'
    assert len(result['members']) == 3


def test_real_pdf_cross_column_successor_cannot_complete_open_prose():
    raw = pdf([(45, 80, 'Only approved orders.\nThe policy does not cover'),
               (330, 127, 'water damage. Other unrelated details are omitted.')])
    assert extract_native_context(raw, 1, 'The policy does not cover') is None


def test_real_pdf_closed_prose_and_numeric_year_row_keep_native_contract():
    for source in ('The policy does not cover water damage.', 'Budget: 2026'):
        raw = pdf([(45, 100, source)])
        result = extract_native_context(raw, 1, source)
        assert result and result['text'] == source


def test_real_pdf_condition_unit_prefix_and_literal_claim_validation_survive():
    from backend.grounded_generation import GroundedGenerator
    from backend.responses_client import GenerationError
    raw = pdf([(45, 80, 'Applicable conditions: approved orders.\nUnits: USD.\nRevenue may reach'),
               (45, 140, '100 dollars. The later amount is unrelated.')])
    result = extract_native_context(raw, 1, 'Revenue may reach')
    assert result and result['text'].startswith('Applicable conditions: approved orders.\nUnits: USD.')
    full = result['text']
    assert GroundedGenerator.validate({'abstain': False, 'claims': [
        {'text': full, 'support': [{'citation_id': 1, 'quote': full}]}]}, {1: full})
    with pytest.raises(GenerationError):
        GroundedGenerator.validate({'abstain': False, 'claims': [
            {'text': '100 dollars', 'support': [{'citation_id': 1, 'quote': '100 dollars'}]}]}, {1: full})


@pytest.mark.parametrize('source', ['Orders are not', 'Refunds exclude', 'Revenue may reach'])
def test_short_open_fragment_cannot_be_returned_as_native_complete_block(source):
    raw = pdf([(45, 80, source)])
    assert extract_native_context(raw, 1, source) is None


@pytest.mark.parametrize('following', [
    'Refunds are handled separately.',
    'new exclusions\nWater damage is excluded.',
    'new exclusions\nwater damage is excluded.',
])
def test_independent_sentence_and_unstyled_lowercase_heading_do_not_join(following):
    raw = pdf([(45, 80, 'The policy does not cover'), (45, 125, following)])
    assert extract_native_context(raw, 1, 'The policy does not cover') is None


@pytest.mark.parametrize('following', ['100 USD.', '100.\nUSD', '100. USD'])
def test_numeric_continuation_cannot_drop_explicit_currency_or_be_mistaken_for_heading(following):
    raw = pdf([(45, 80, 'Projected revenue may reach'), (45, 125, following)])
    result = extract_native_context(raw, 1, 'Projected revenue may reach')
    assert result and result['text'].endswith(following)
    assert 'USD' in result['text'] and not result['calculator_input_eligible']
    assert result['members'][-1]['source_range'][1] == len(following)


def test_numeric_unit_extension_is_complete_or_over_budget_never_cropped():
    raw = pdf([(45, 80, 'Projected revenue may reach'), (45, 125, '100.\nUSD')])
    prefix_budget = len('Projected revenue may reach\n100.')
    assert extract_native_context(raw, 1, 'Projected revenue may reach', max_chars=prefix_budget) is None


def test_fresh_native_font_boundary_prevents_lowercase_heading_join():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((45, 80), 'The policy does not cover', fontsize=10)
    page.insert_text((45, 125), 'new exclusions.', fontsize=14)
    raw = document.tobytes()
    document.close()
    assert extract_native_context(raw, 1, 'The policy does not cover') is None


def test_immediately_adjacent_separate_condition_heading_is_not_dropped():
    raw = pdf([(45, 40, 'Applicable conditions: approved orders.'),
               (45, 80, 'The policy does not cover'), (45, 125, 'water damage.')])
    result = extract_native_context(raw, 1, 'The policy does not cover')
    assert result and result['text'].startswith('Applicable conditions: approved orders.\n')
    assert len(result['members']) == 3
    assert extract_native_context(raw, 1, 'The policy does not cover', max_chars=65) is None


def test_separate_declared_scope_with_unproved_paragraph_gap_returns_no_context():
    raw = pdf([(45, 40, 'Applicable conditions: approved orders.'),
               (45, 150, 'The policy does not cover'), (45, 190, 'water damage.')])
    assert extract_native_context(raw, 1, 'The policy does not cover') is None


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
