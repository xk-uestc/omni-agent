"""Selected-cell pixel/native agreement and disagreement cannot invent values."""
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from backend.visual_cell_verification import verify_visible_grid_fact, _comparison_literal
from backend.visual_table_reader import VisualTableReader
from tests.test_visual_table_reader import setup, Client


class Pipeline:
    def __init__(self, values=('West', '2025 Actual', '3578'), confidence=0.99, status='ok'):
        self.values = iter(values)
        self.calls = []
        self.confidence, self.status = confidence, status

    def run(self, raw, **kwargs):
        with Image.open(BytesIO(raw)) as image:
            assert image.format == 'PNG'
            self.calls.append(image.size)
        payload = {'text': next(self.values), 'confidence': self.confidence, 'status': self.status}
        return SimpleNamespace(to_dict=lambda: payload)


def selected(tables):
    return next(f for f in tables['tables'][0]['facts'] if f['raw_value'] == '3578')


def test_all_key_and_value_crops_must_agree():
    asset, tables = setup()
    pipeline = Pipeline()
    result = verify_visible_grid_fact(asset, tables, selected(tables), pipeline)
    assert result['status'] == 'corroborated'
    assert [c['role'] for c in result['checks']] == ['row_header', 'column_header', 'value']
    assert len(pipeline.calls) == 3 and not result['calculator_input_eligible']


@pytest.mark.parametrize('values', [('East',), ('West', '2026 Forecast'), ('West', '2025 Actual', '3579')])
def test_wrong_header_year_or_digit_is_conflict(values):
    asset, tables = setup()
    result = verify_visible_grid_fact(asset, tables, selected(tables), Pipeline(values))
    assert result['status'] == 'conflict'
    assert result['reason'] == 'visible_native_literal_mismatch'


@pytest.mark.parametrize('confidence', [None, True, float('nan'), 0.84, 1.1])
def test_unknown_or_low_confidence_cannot_pass(confidence):
    asset, tables = setup()
    result = verify_visible_grid_fact(asset, tables, selected(tables), Pipeline(confidence=confidence))
    assert result['status'] == 'unverified' and result['reason'] == 'ocr_not_confident'


def test_missing_ocr_explicitly_unverified():
    asset, tables = setup()
    assert verify_visible_grid_fact(asset, tables, selected(tables), None)['reason'] == 'ocr_not_configured'


def test_crops_outside_render_are_not_clipped_into_other_values():
    asset, tables = setup()
    tables = deepcopy(tables)
    tables['tables'][0]['cells'][1][0]['bbox_display_pt'][0] = -100
    assert verify_visible_grid_fact(asset, tables, selected(tables), Pipeline())['reason'] == 'selected_cell_outside_render_or_too_small'


def test_pinned_image_tampering_is_rejected():
    asset, tables = setup()
    asset.manifest['render_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='source mismatch'):
        verify_visible_grid_fact(asset, tables, selected(tables), Pipeline())


def test_conflict_does_not_replace_answer_with_ocr_guess():
    asset, tables = setup()
    result = VisualTableReader(Client(), Pipeline(('West', '2025 Actual', '9999'))).answer('West 2025 Actual', asset, tables)
    assert result['status'] == 'incomplete' and result['answer'] is None
    assert result['clarification_code'] == 'visible_cell_ocr_conflict'


def test_corroborated_literal_remains_server_value():
    asset, tables = setup()
    result = VisualTableReader(Client(), Pipeline()).answer('West 2025 Actual', asset, tables)
    assert result['status'] == 'ok' and result['fact']['raw_value'] == '3578'
    assert result['visible_cell_verification']['status'] == 'corroborated'
    assert not result['calculator_input_eligible']


def test_ocr_year_label_spacing_is_bounded_not_fuzzy_digit_matching():
    asset, tables = setup()
    result = verify_visible_grid_fact(asset, tables, selected(tables), Pipeline(('West', '2025Actual', '3578')))
    assert result['status'] == 'corroborated'
    assert _comparison_literal('35 78', 'value') != _comparison_literal('3578', 'value')
    assert _comparison_literal('New York', 'row_header') != _comparison_literal('NewYork', 'row_header')
    assert _comparison_literal('2026Actual', 'column_header') != _comparison_literal('2025 Actual', 'column_header')
