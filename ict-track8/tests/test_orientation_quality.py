import math

import pytest

from backend.orientation_quality import assess_text_orientation, orientation_warnings, upright_reading_order


def box(angle=0, vertical=False):
    points = [(0,0),(20 if vertical else 180,0),(20 if vertical else 180,180 if vertical else 20),(0,180 if vertical else 20)]
    radians = math.radians(angle)
    return [[100+x*math.cos(radians)-y*math.sin(radians),100+x*math.sin(radians)+y*math.cos(radians)] for x,y in points]


def assess(boxes, labels, confidence=.99):
    return assess_text_orientation(boxes,[[label,confidence] for label in labels],[['真实文字证据',.95]]*len(boxes))


@pytest.mark.parametrize('vertical,label,rotation',[(False,'0',0),(False,'180',180),(True,'180',90),(True,'0',270)])
def test_page_rotation_uses_crop_axis_and_local_classifier(vertical,label,rotation):
    result = assess([box(vertical=vertical)]*3,[label]*3)
    assert result['status']=='estimated' and result['rotation_ccw_degrees']==rotation
    assert result['source_image_modified'] is False
    assert ('page_orientation_detected' in orientation_warnings(result))==bool(rotation)


@pytest.mark.parametrize('angle',[-7,7])
def test_skew_sign_matches_counterclockwise_image_convention(angle):
    result = assess([box(angle=angle)]*3,['0']*3)
    assert result['skew_ccw_degrees']==-angle and result['correction_ccw_degrees']==angle
    assert 'page_skew_detected' in orientation_warnings(result)


def test_conflicting_and_sparse_direction_evidence_is_undetermined():
    for boxes,labels,confidence in (([box()]*4,['0','0','180','180'],.99),
                                    ([box()],['180'],.99),([box()]*3,['0']*3,.7),
                                    ([box(-8),box(8)],['0','0'],.99)):
        result = assess(boxes,labels,confidence)
        assert result['status']=='undetermined' and result['correction_ccw_degrees'] is None
        assert orientation_warnings(result)==('page_orientation_undetermined',)


def test_invalid_geometry_and_blank_or_low_recognition_do_not_estimate_page_direction():
    for boxes,recognized in (([[[float('nan'),0]]*4]*3,[['真实文字证据',.99]]*3),
                              ([box()]*3,[['',.99]]*3),([box()]*3,[['错误文字证据',.2]]*3)):
        assert assess_text_orientation(boxes,[['180',.99]]*3,recognized)['status']=='undetermined'


def test_upright_reading_order_preserves_source_boxes_and_unknown_order():
    regions = [{'text':'bottom','bbox':[[10,10],[100,10],[100,30],[10,30]]},
               {'text':'top','bbox':[[10,70],[100,70],[100,90],[10,90]]}]
    result = upright_reading_order(regions,[120,100],{'status':'estimated','correction_ccw_degrees':-180})
    assert [row['text'] for row in result]==['top','bottom']
    assert result[0] is regions[1] and regions[0]['bbox'][0]==[10,10]
    assert upright_reading_order(regions,[120,100],{'status':'undetermined'}) is regions
