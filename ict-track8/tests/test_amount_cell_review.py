from copy import deepcopy
from io import BytesIO
import hashlib
import pytest
from PIL import Image,ImageDraw
from backend.amount_cell_review import AmountCellReviewAgent


def fixture():
    image=Image.new('RGB',(300,90),'white');draw=ImageDraw.Draw(image)
    draw.text((155,35),'$2,000',fill='black')
    stream=BytesIO();image.save(stream,format='PNG');raw=stream.getvalue();sha=hashlib.sha256(raw).hexdigest()
    cells=[[{'row':r,'column':c,'text':text,'status':'ocr_observed','rowspan':1,'colspan':1,
        'original_geometry':{'source_sha256':sha,'original_bbox_eligible':True,
            'polygon_px':[[c*150,r*30],[(c+1)*150,r*30],[(c+1)*150,(r+1)*30],[c*150,(r+1)*30]]}}
        for c,text in enumerate(row)] for r,row in enumerate([['A','$98,000'],['B','$2.000'],['TOTAL:','$100,000']])]
    return raw,{'status':'structure_observed','column_count':2,'body_cells':cells}


def test_three_distinct_preprocessing_agreement_creates_preview_only():
    raw,structure=fixture();before=deepcopy(structure);calls=[]
    def recognize(data):calls.append(hashlib.sha256(data).hexdigest());return [{'text':'$2,000','confidence':.98}]
    result=AmountCellReviewAgent().run(raw,structure,recognize)
    assert result['status']=='candidates_available' and result['accepted_count']==1
    assert len(calls)==3 and len(set(calls))==3
    assert structure==before and structure['body_cells'][1][1]['text']=='$2.000'
    assert result['revised_preview']['body_cells'][1][1]['text']=='$2,000'
    assert result['revised_preview']['arithmetic_check']['status']=='arithmetic_consistent'
    assert result['ocr_values_replaced'] is False and result['independent_recognizer'] is False
    assert result['reviews'][0]['original_geometry']==structure['body_cells'][1][1]['original_geometry']


@pytest.mark.parametrize('bad_result',[
    [{'text':'$2.000','confidence':.99}],[{'text':'$2,000','confidence':.4}],[],
    [{'text':'$2,000','confidence':.99},{'text':'$1,000','confidence':.99}],
    [{'text':'$2,000','confidence':float('nan')}],None])
def test_unreliable_or_ambiguous_rechecks_never_publish_candidate(bad_result):
    raw,structure=fixture()
    result=AmountCellReviewAgent().run(raw,structure,lambda data:bad_result)
    assert result['accepted_count']==0 and 'revised_preview' not in result


def test_disagreeing_valid_candidates_are_not_selected_by_majority_or_total():
    raw,structure=fixture();values=iter(['$2,000','$2,000','$3,000'])
    result=AmountCellReviewAgent().run(raw,structure,lambda data:[{'text':next(values),'confidence':.99}])
    assert result['reviews'][0]['reason']=='conflicting_candidates' and result['accepted_count']==0


@pytest.mark.parametrize('kind',['hash','outside','bowtie','nonfinite','ineligible'])
def test_invalid_source_geometry_never_calls_recognizer(kind):
    raw,structure=fixture();mapping=structure['body_cells'][1][1]['original_geometry']
    if kind=='hash':mapping['source_sha256']='b'*64
    if kind=='outside':mapping['polygon_px'][0][0]=-1
    if kind=='bowtie':mapping['polygon_px'][1],mapping['polygon_px'][2]=mapping['polygon_px'][2],mapping['polygon_px'][1]
    if kind=='nonfinite':mapping['polygon_px'][0][0]=float('nan')
    if kind=='ineligible':mapping['original_bbox_eligible']=False
    result=AmountCellReviewAgent().run(raw,structure,lambda data:pytest.fail('invalid source cannot recognize'))
    assert result['reviews'][0]['reason']=='source_or_geometry_invalid'


def test_valid_rotated_polygon_is_rectified_for_line_recognition():
    raw,structure=fixture();structure['body_cells'][1][1]['original_geometry']['polygon_px']=[[300,30],[300,60],[150,60],[150,30]]
    result=AmountCellReviewAgent().run(raw,structure,lambda data:[{'text':'$2,000','confidence':.99}])
    assert result['accepted_count']==1


def test_missing_line_recognizer_preserves_suspect_and_existing_clear_amounts_are_not_retried():
    raw,structure=fixture();result=AmountCellReviewAgent().run(raw,structure,None)
    assert result['reason']=='line_recognizer_unavailable'
    structure['body_cells'][1][1]['text']='$2,000'
    result=AmountCellReviewAgent().run(raw,structure,lambda data:pytest.fail('no clear value retries'))
    assert result['status']=='not_needed' and result['calls']==0
