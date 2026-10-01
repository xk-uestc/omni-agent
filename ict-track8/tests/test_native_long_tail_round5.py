"""Literal tail eligibility and bounded native recovery, independent of gold."""
import hashlib

import fitz
import pytest

from backend.native_continuation import (CONTINUATION_POLICY_VERSION,
    choose_continuation, continuation_state)
from backend.pdf_native_context import extract_native_context


@pytest.mark.parametrize('tail', [
    'The regional water conservation and recycling programme',
    'This mandatory component certification and testing requirement',
    'These cross-border purchase and delivery restrictions',
    'Those independently verified annual expenditure records',
])
def test_long_unclosed_noun_phrase_never_becomes_complete_evidence(tail):
    assert continuation_state('Only approved components are included. ' + tail) == 'unknown'


@pytest.mark.parametrize('text', [
    'The regional programme is restricted to approved components.',
    'The regional programme is final.\nCompany service unit',
    'The regional programme is final.\nAlice Doe',
    'Budget: 2027',
    'Native table row 120 140',
])
def test_closed_sentences_names_and_numeric_rows_keep_native_contract(text):
    assert continuation_state(text) == 'native'


@pytest.mark.parametrize('text', [
    'Only approved components. The U.S. regional expenditure programme',
    'Only approved components. The annual 12.5 percent service allocation',
    'Only approved components. The Dr. Smith component certification process',
])
def test_abbreviation_decimal_and_acronym_do_not_reset_the_tail(text):
    assert continuation_state(text) == 'unknown'


@pytest.mark.parametrize('text', [
    'Only approved components. The programme uses approx. annual allowance',
    'Only approved components. The allowance is 12.\n5 percent for approved orders',
    'Only approved components. These dates include Sept. regional allocation',
    'Only approved components. This section follows Ref. regional allowance',
    'Only approved components. The programme uses avg. annual allowance',
    'Only approved components. The programme uses est. annual allowance',
    'Only approved components. The programme uses min. annual allowance',
    'Only approved components. The programme uses xyzabbr. annual allowance',
])
def test_ambiguous_dot_never_certifies_unclosed_tail_as_native(text):
    assert continuation_state(text) != 'native'
    with fitz.open() as document:
        page = document.new_page(width=600, height=700)
        page.insert_text((45, 80), text, fontsize=8)
        raw = document.tobytes()
    assert extract_native_context(raw, 1, text) is None


def _block(identifier, text, box):
    return {'block_id': identifier, 'normalized_text': text,
        'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'bbox_fitz_unrotated_pt': box,
        'native_lines': [{'text': text, 'bbox': box,
                          'native_style': (('Body', 10.0, 0),)}]}


def _columns():
    prefix = 'Only certified components are included. The regional service allocation policy'
    blocks = [_block(1, prefix, [30, 285, 230, 297]),
              _block(2, 'applies only to approved orders. Other terms follow.', [270, 10, 470, 22])]
    order = {'mode': 'native_columns', 'column_count': 2, 'block_ids': [1, 2],
        'source_sha256': 'a' * 64,
        'column_bands_pt': [{'x0': 20, 'x1': 240, 'y0': 10, 'y1': 300},
                            {'x0': 260, 'x1': 480, 'y0': 10, 'y1': 300}]}
    return blocks, order


def test_long_tail_crosscolumn_recovery_keeps_scope_and_literal_ranges():
    blocks, order = _columns()
    result = choose_continuation(blocks[0], blocks, order)
    assert result['text'] == blocks[0]['normalized_text'] + '\napplies only to approved orders.'
    assert result['members'][0]['source_range'] == [0, len(blocks[0]['normalized_text'])]
    assert result['members'][1]['source_range'] == [0, len('applies only to approved orders.')]
    assert result['column_transition']['same_boundary_font_style']
    assert result['continuation_policy_version'] == CONTINUATION_POLICY_VERSION
    assert not result['calculator_input_eligible']
    assert choose_continuation(blocks[0], blocks, order,
                               max_chars=len(result['text']) - 1)['text'] is None


@pytest.mark.parametrize('failure', ['footer', 'font', 'source', 'heading', 'no_closure'])
def test_long_tail_does_not_bypass_any_existing_successor_proof(failure):
    blocks, order = _columns()
    if failure == 'footer':
        blocks.insert(1, _block(3, 'Page 9', [30, 300, 230, 312]))
        order['block_ids'] = [1, 3, 2]
    elif failure == 'font':
        blocks[1]['native_lines'][0]['native_style'] = (('Heading', 14.0, 0),)
    elif failure == 'source':
        blocks[1]['source_sha256'] = 'b' * 64
    elif failure == 'heading':
        blocks[1]['is_heading'] = True
    else:
        blocks[1] = _block(2, 'applies only to approved orders', [270, 10, 470, 22])
    assert choose_continuation(blocks[0], blocks, order)['text'] is None


def _pdf(*, successor=True):
    prefix = 'Only approved orders.\nThe regional service allocation and certification policy'
    with fitz.open() as document:
        page = document.new_page(width=600, height=700)
        page.insert_text((45, 80), prefix, fontsize=10)
        if successor:
            page.insert_text((45, 127), 'applies only to certified components. Other details follow.', fontsize=10)
        raw = document.tobytes()
    return prefix, raw


def test_original_pdf_long_tail_has_complete_literal_evidence_or_nothing():
    prefix, raw = _pdf()
    result = extract_native_context(raw, 1, prefix)
    assert result and result['mode'] == 'original_native_complete_continuation'
    assert result['text'] == prefix + '\napplies only to certified components.'
    assert result['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['text_sha256'] == hashlib.sha256(result['text'].encode()).hexdigest()
    assert 'Other details' not in result['text']
    assert extract_native_context(raw, 1, prefix, max_chars=len(result['text']) - 1) is None
    prefix, incomplete = _pdf(successor=False)
    assert extract_native_context(incomplete, 1, prefix) is None
