"""Ensure reference scoring cannot hide coordinate failures or order errors."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from verify_camera_prose import score,distance


def region(text,polygon,sha='a'*64):
    return {'text':text,'original_geometry':{'polygon_px':polygon,'original_bbox_eligible':True,
        'source_sha256':sha,'coordinate_scope':'source_image_stored_pixel_edges'}}


def test_score_measures_global_reading_order_separately_from_block_characters():
    reference={'size_px':[100,100],'blocks':[
        {'id':'first','bounds_normalized':[0,0,1,.4],'text':'abc'},
        {'id':'second','bounds_normalized':[0,.6,1,1],'text':'def'}]}
    result={'metadata':{'regions':[
        region('def',[[10,70],[50,70],[50,80],[10,80]]),
        region('abc',[[10,10],[50,10],[50,20],[10,20]])]}}
    evaluated=score(result,reference,[1,0,0,0,1,0],'a'*64)
    assert evaluated['normalized_prose_cer']==0
    assert evaluated['prose_reading_order_cer']>0


def test_invalid_source_and_executor_only_boxes_cannot_pass_prose_evaluation():
    reference={'size_px':[100,100],'blocks':[{'id':'all','bounds_normalized':[0,0,1,1],'text':'abc'}]}
    result={'metadata':{'regions':[region('abc',[[10,10],[50,10],[50,20],[10,20]],sha='b'*64),
        {'text':'abc','bbox':[[10,10],[50,10],[50,20],[10,20]]}]}}
    evaluated=score(result,reference,[1,0,0,0,1,0],'a'*64)
    assert evaluated['mapped_region_count']==0
    assert evaluated['normalized_prose_cer']==1


def test_edit_distance_counts_insertions_deletions_and_substitutions():
    assert distance('kitten','sitting')==3
    assert distance('abc','')==3
    assert distance('','abc')==3
