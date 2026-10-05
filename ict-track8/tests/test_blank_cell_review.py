from copy import deepcopy
import pytest
from tests.test_amount_cell_review import fixture
from backend.blank_cell_review import BlankCellReviewAgent


def source():
    raw,structure=fixture();structure['body_cells'][1][1]['text']='';return raw,structure


def test_consistent_visible_text_preserves_original_blank_and_coordinates():
    raw,structure=source();before=deepcopy(structure)
    result=BlankCellReviewAgent().run(raw,structure,lambda data:[{'text':'$2,000','confidence':.99}])
    assert result['accepted_count']==1 and result['calls']==3
    assert result['revised_preview']['body_cells'][1][1]['text']=='$2,000'
    assert result['revised_preview']['arithmetic_check']['status']=='arithmetic_consistent'
    assert result['reviews'][0]['original_geometry']==structure['body_cells'][1][1]['original_geometry']
    assert structure==before and result['original_observations_replaced'] is False


def test_ambiguous_amount_candidate_remains_ambiguous_and_arithmetic_refuses():
    raw,structure=source()
    result=BlankCellReviewAgent().run(raw,structure,lambda data:[{'text':'$2.000','confidence':.99}])
    assert result['reviews'][0]['candidate_kind']=='ambiguous_amount_text'
    assert result['revised_preview']['arithmetic_check']['status']=='unavailable'


def test_disagreement_cannot_be_selected_by_majority():
    raw,structure=source();values=iter(['Other:Diapers','Other: Diapers','Other: Diapers'])
    result=BlankCellReviewAgent().run(raw,structure,lambda data:[{'text':next(values),'confidence':.99}])
    assert result['accepted_count']==0


@pytest.mark.parametrize('observation',[[],None,[{'text':'$2,000','confidence':.4}],[{'text':'0','confidence':float('nan')}],
    [{'text':'A','confidence':.99},{'text':'B','confidence':.99}]])
def test_invalid_or_low_quality_results_do_not_fill_blank(observation):
    raw,structure=source();result=BlankCellReviewAgent().run(raw,structure,lambda data:observation)
    assert result['accepted_count']==0 and 'revised_preview' not in result


def test_foreign_coordinates_and_no_visible_ink_never_call_ocr():
    raw,structure=source();structure['body_cells'][1][1]['original_geometry']['source_sha256']='b'*64
    result=BlankCellReviewAgent().run(raw,structure,lambda data:pytest.fail('invalid source'))
    assert result['reviews'][0]['reason']=='source_or_geometry_invalid'
    raw,structure=source();structure['body_cells'][1][1]['original_geometry']['polygon_px']=[[0,0],[100,0],[100,25],[0,25]]
    result=BlankCellReviewAgent().run(raw,structure,lambda data:pytest.fail('empty pixels must stay blank'))
    assert result['reviews'][0]['reason']=='no_visible_ink'
