"""A native lane transition needs complete geometry and unchanged literal scope."""
from copy import deepcopy
import hashlib
import pytest

from backend.native_continuation import choose_continuation


def block(identifier, text, box):
    return {'block_id': identifier, 'normalized_text': text,
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'bbox_fitz_unrotated_pt': box,
            'native_lines': [{'text': text, 'bbox': box, 'native_style': (('Body', 10.0, 0),)}]}


def setup():
    leading = block(1, 'Only approved orders. The policy does not cover', [30, 285, 230, 297])
    following = block(2, 'unapproved returns. An unrelated fact follows.', [270, 10, 470, 22])
    items = [leading, following]
    order = {'mode': 'native_columns', 'column_count': 2, 'block_ids': [1, 2],
             'source_sha256': 'a' * 64,
             'column_bands_pt': [{'x0': 20, 'x1': 240, 'y0': 10, 'y1': 300},
                                 {'x0': 260, 'x1': 480, 'y0': 10, 'y1': 300}]}
    return items, order


def test_verified_lane_wrap_keeps_whole_anchor_and_only_first_closed_successor():
    items, order = setup()
    result = choose_continuation(items[0], items, order)
    assert result['text'] == items[0]['normalized_text'] + '\nunapproved returns.'
    assert result['column_transition']['to_column'] == 1
    assert result['column_transition']['same_boundary_font_style']
    assert len(result['members']) == 2
    assert result['members'][1]['source_range'] == [0, len('unapproved returns.')]
    assert result['calculator_input_eligible'] is False


@pytest.mark.parametrize('change', ['missing_bands', 'wrong_mode', 'nonadjacent', 'short_column',
                                   'not_bottom', 'not_top', 'font_change', 'unknown_style', 'heading'])
def test_unproven_lane_flow_stays_rejected(change):
    items, order = setup()
    if change == 'missing_bands':
        del order['column_bands_pt'][0]['y1']
    elif change == 'wrong_mode':
        order['mode'] = 'unknown'
    elif change == 'nonadjacent':
        order['column_bands_pt'].insert(1, {'x0': 245, 'x1': 255, 'y0': 10, 'y1': 300})
    elif change == 'short_column':
        order['column_bands_pt'][0]['y0'] = 275
    elif change == 'not_bottom':
        order['column_bands_pt'][0]['y1'] = 320
    elif change == 'not_top':
        order['column_bands_pt'][1]['y0'] = 0
        items[1]['bbox_fitz_unrotated_pt'][1] = 28
    elif change == 'font_change':
        items[1]['native_lines'][0]['native_style'] = (('Heading', 10.0, 0),)
    elif change == 'unknown_style':
        items[1]['native_lines'][0]['native_style'] = None
    elif change == 'heading':
        items[1]['is_heading'] = True
    assert choose_continuation(items[0], items, order)['text'] is None


def test_intervening_lane_block_cannot_be_skipped_by_order():
    items, order = setup()
    competitor = block(3, 'Other text.', [30, 287, 230, 299])
    items.append(competitor)
    order['block_ids'] = [1, 2, 3]
    assert choose_continuation(items[0], items, order)['text'] is None


def test_column_wrap_replays_native_hashes_and_refuses_partial_budget():
    items, order = setup()
    assert choose_continuation(items[0], items, order, max_chars=30)['text'] is None
    changed = deepcopy(items)
    changed[1]['normalized_text'] = 'different returns.'
    assert choose_continuation(changed[0], changed, order)['reason'] == 'text_pin_invalid'
