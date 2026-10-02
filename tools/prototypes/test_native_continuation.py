"""Synthetic fresh native geometry tests; no backend import or model calls."""
import hashlib

import pytest

from native_continuation import choose_continuation


SHA = 'a' * 64


def block(identifier, text, box):
    return {'block_id': identifier, 'native_block_id': identifier,
            'normalized_text': text, 'bbox_fitz_unrotated_pt': box,
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'native_lines': [{'text': text, 'bbox': box}], 'source_sha256': SHA}


def order(blocks, bands=None):
    return {'source_sha256': SHA, 'block_ids': [b['block_id'] for b in blocks],
            'column_count': 1 if bands is None else len(bands),
            'column_bands_pt': bands or []}


def sample(prefix='Only approved orders. The policy does not cover'):
    leading = block(1, prefix, [30, 20, 230, 32])
    following = block(2, 'water damage.\n' + 'Additional unrelated content ' * 100, [30, 37, 230, 250])
    return leading, following


def test_complete_first_continuation_survives_large_successor_with_literal_pins():
    leading, following = sample()
    blocks = [leading, following]
    result = choose_continuation(leading, blocks, order(blocks))
    assert result['text'] == leading['normalized_text'] + '\nwater damage.'
    assert len(following['normalized_text']) > 1800
    assert result['source_sha256'] == SHA
    assert result['members'][1]['source_range'] == [0, len('water damage.')]
    assert result['members'][1]['lines'][0]['bbox_fitz_unrotated_pt'] == following['native_lines'][0]['bbox']
    assert result['calculator_input_eligible'] is False


@pytest.mark.parametrize('prefix', [
    'Only approved orders. The policy does not cover',
    'Forecast:\nProjected expenditure may reach',
    '仅限有效订单。实际保修不覆盖',
])
def test_negative_condition_and_forecast_prefix_are_never_trimmed(prefix):
    leading, following = sample(prefix)
    result = choose_continuation(leading, [leading, following], order([leading, following]))
    assert result['text'].startswith(prefix + '\n')
    assert result['members'][0]['source_range'] == [0, len(prefix)]


def test_crosscolumn_and_overlapping_band_ambiguity_reject():
    leading, following = sample()
    following['bbox_fitz_unrotated_pt'] = [270, 37, 470, 49]
    blocks = [leading, following]
    assert choose_continuation(leading, blocks, order(blocks, [{'x0': 20, 'x1': 240}, {'x0': 260, 'x1': 480}]))['text'] is None
    following['bbox_fitz_unrotated_pt'] = [30, 37, 230, 49]
    assert choose_continuation(leading, blocks, order(blocks, [{'x0': 20, 'x1': 240}, {'x0': 20, 'x1': 240}]))['text'] is None


@pytest.mark.parametrize('heading', ['New results:\nRevenue is 100.', '# New results\nRevenue is 100.', 'NEW RESULTS\nRevenue is 100.'])
def test_new_heading_blocks_continuation(heading):
    leading, following = sample()
    following = block(2, heading, [30, 37, 230, 60])
    assert choose_continuation(leading, [leading, following], order([leading, following]))['reason'] == 'new_heading'


def test_geometric_competitor_prevents_uniqueness_even_with_list_order():
    leading, following = sample()
    competitor = block(3, 'another value.', [31, 38, 230, 50])
    blocks = [leading, following, competitor]
    assert choose_continuation(leading, blocks, order(blocks))['reason'] == 'ambiguous_geometric_successor'


def test_budget_rejects_whole_sentence_instead_of_shortening_or_choosing_later():
    leading, following = sample()
    blocks = [leading, following]
    result = choose_continuation(leading, blocks, order(blocks), len(leading['normalized_text']) + 4)
    assert result['reason'] == 'complete_continuation_over_budget'


@pytest.mark.parametrize('text', ['water damage without closure', 'USD 12.50', 'Dr. '])
def test_missing_closure_decimal_and_abbreviation_are_not_sentence_boundaries(text):
    leading, _ = sample()
    following = block(2, text, [30, 37, 230, 49])
    assert choose_continuation(leading, [leading, following], order([leading, following]))['text'] is None


def test_closed_anchor_and_hash_or_line_mapping_forgery_reject():
    leading, following = sample('The policy covers damage.')
    blocks = [leading, following]
    assert choose_continuation(leading, blocks, order(blocks))['text'] is None
    leading, following = sample()
    blocks = [leading, following]
    following['text_sha256'] = 'b' * 64
    assert choose_continuation(leading, blocks, order(blocks))['reason'] == 'text_pin_invalid'
    following['text_sha256'] = hashlib.sha256(following['normalized_text'].encode()).hexdigest()
    following['native_lines'][0]['text'] = 'forged line'
    assert choose_continuation(leading, blocks, order(blocks))['reason'] == 'line_ranges_unverified'
