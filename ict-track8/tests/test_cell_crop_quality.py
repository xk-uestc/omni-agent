import numpy as np
from PIL import Image
from backend.cell_crop_quality import suppress_disconnected_top_edge


def test_only_disconnected_top_pixels_removed_preserving_signs_and_bottom_punctuation():
    pixels=np.full((24,240,3),255,dtype=np.uint8)
    pixels[0,::3]=40
    pixels[3:18,100:140]=0
    pixels[21:24,145:148]=0  # comma or decimal mark: never remove
    pixels[0:8,97:99]=0  # glyph touching top: preserve entire column
    before=pixels.copy()
    result,evidence=suppress_disconnected_top_edge(Image.fromarray(pixels))
    actual=np.array(result)
    assert evidence['status']=='disconnected_top_edge_suppressed'
    assert evidence['removed_pixels']>8
    assert np.array_equal(actual[1:],before[1:])
    assert np.array_equal(actual[:,94:151],before[:,94:151])
    assert np.array_equal(pixels,before)


def test_wide_text_or_full_height_border_is_never_suppressed():
    pixels=np.full((24,240,3),255,dtype=np.uint8)
    pixels[0,::3]=0
    pixels[5:20,10:230]=0
    result,evidence=suppress_disconnected_top_edge(Image.fromarray(pixels))
    assert evidence['status']=='unchanged'
    assert np.array_equal(np.array(result),pixels)
    pixels[:,0]=0;pixels[:,-1]=0
    result,evidence=suppress_disconnected_top_edge(Image.fromarray(pixels))
    assert evidence['status']=='unchanged'


def test_narrow_top_punctuation_blank_and_small_cells_are_not_removed():
    for width,height in [(240,24),(60,24),(240,8)]:
        pixels=np.full((height,width,3),255,dtype=np.uint8)
        pixels[0,20:30]=0
        pixels[2:height-1,20:35]=0
        result,evidence=suppress_disconnected_top_edge(Image.fromarray(pixels))
        assert evidence['status']=='unchanged'
        assert np.array_equal(np.array(result),pixels)
