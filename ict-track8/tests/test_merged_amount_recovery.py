from io import BytesIO
from copy import deepcopy
import pytest
from PIL import Image,ImageDraw
from backend.scanned_table_layout import recover_merged_amount_pairs


def image_and_region(gap=True):
    image=Image.new('RGB',(240,60),'white');draw=ImageDraw.Draw(image)
    draw.rectangle((20,20,100,39),fill='black')
    draw.rectangle((140 if gap else 101,20,210,39),fill='black')
    output=BytesIO();image.save(output,format='PNG')
    return output.getvalue(),[{'text':'TOTAL COST $22.56','confidence':.9,'bbox':[[20,20],[211,20],[211,40],[20,40]]}]


def recognizer(label='TOTAL COST',amount='$22.56',outside=False):
    calls=[]
    def recognize(data):
        with Image.open(BytesIO(data)) as image:w,h=image.size
        text=label if len(calls)%2==0 else amount;calls.append(data)
        return [{'text':text,'confidence':.95,'bbox':[[3,3],[w+1 if outside else w-3,3],[w-3,h-3],[3,h-3]]}]
    return recognize,calls


def test_split_uses_real_whitespace_and_keeps_crops_bound_to_original_pixels():
    data,regions=image_and_region();before=deepcopy(regions);recognize,calls=recognizer()
    pairs,audit=recover_merged_amount_pairs(data,regions,recognize)
    assert audit['calls']==2 and audit['recovered_pairs']==1 and regions==before
    pair=pairs[0]
    assert pair['is_table'] is False and pair['cell_values_verified'] is False
    left,right=pair['rows'][0]
    assert left['crop_bbox_px'][2]==right['crop_bbox_px'][0]==120
    assert left['bbox'][2]<=120 and right['bbox'][0]>=120
    assert len(left['crop_sha256'])==64 and len(right['crop_sha256'])==64
    assert left['original_merged_text']=='TOTAL COST $22.56'


def test_observed_crops_may_share_an_edge_without_overlapping_pixels():
    data,regions=image_and_region();calls=[]
    def edge_boxes(crop):
        with Image.open(BytesIO(crop)) as image:w,h=image.size
        text='TOTAL COST' if not calls else '$22.56';calls.append(crop)
        return [{'text':text,'confidence':.99,'bbox':[[0,3],[w,3],[w,h-3],[0,h-3]]}]
    pairs,audit=recover_merged_amount_pairs(data,regions,edge_boxes,max_calls=2)
    assert len(pairs)==1
    left,right=pairs[0]['rows'][0]
    assert left['bbox'][2]==right['bbox'][0]
    assert audit['crop_attempts'][0]['label_bbox_px'][2]==audit['crop_attempts'][0]['amount_bbox_px'][0]
    assert pairs[0]['cell_values_verified'] is False


def test_currency_confusion_requires_currency_actually_observed_in_crop():
    data,regions=image_and_region()
    regions[0]['text']='TOTAL COST S22.56'
    original=deepcopy(regions)
    recognize,_=recognizer()
    pairs,_=recover_merged_amount_pairs(data,regions,recognize)
    assert len(pairs)==1 and pairs[0]['currency_reobserved_from_crop'] is True
    assert pairs[0]['rows'][0][1]['text']=='$22.56'
    assert pairs[0]['original_merged_text']=='TOTAL COST S22.56'
    assert regions==original


@pytest.mark.parametrize('budget,expected_pairs',[(2,0),(3,1)])
def test_duplicate_local_detections_retry_scale_with_exact_coordinate_mapping_and_budget(budget,expected_pairs):
    data,regions=image_and_region();calls=[]
    def recognize(crop):
        with Image.open(BytesIO(crop)) as image:w,h=image.size
        calls.append((w,h))
        if w==300:
            return [{'text':text,'confidence':.95,'bbox':[[3,3],[w-3,3],[w-3,h-3],[3,h-3]]}
                    for text in ['TOTAL COST','COST']]
        return [{'text':'TOTAL COST' if w==200 else '$22.56','confidence':.95,
            'bbox':[[3,3],[w-3,3],[w-3,h-3],[3,h-3]]}]
    pairs,audit=recover_merged_amount_pairs(data,regions,recognize,max_calls=budget)
    assert len(pairs)==expected_pairs and audit['calls']<=budget
    if pairs:
        label=pairs[0]['rows'][0][0]
        assert label['crop_scale']==2
        assert label['bbox'][0]==pytest.approx(21.5)
        assert label['bbox'][2]==pytest.approx(118.5)


@pytest.mark.parametrize('original,amount,label',[
    ('TOTAL COST S22.56','S22.56','TOTAL COST'),
    ('TOTAL COST S22.56','$22.58','TOTAL COST'),
    ('TOTAL COST S22.56','$22.56','TOTAL TAX'),
    ('TOTAL COST $22.56','€22.56','TOTAL COST'),
    ('TOTAL COSTS22.56','$22.56','TOTAL COST'),
])
def test_currency_candidate_does_not_invent_symbol_digits_or_trim_label_letters(original,amount,label):
    data,regions=image_and_region();regions[0]['text']=original
    recognize,_=recognizer(label=label,amount=amount)
    assert recover_merged_amount_pairs(data,regions,recognize)[0]==[]


@pytest.mark.parametrize('budget,expected_pairs',[(3,0),(4,1)])
def test_line_recognition_fallback_uses_observed_ink_and_stays_within_shared_budget(budget,expected_pairs):
    data,regions=image_and_region()
    # Text-like strokes rather than a solid bar, which the line extractor
    # correctly treats as a possible horizontal table rule.
    image=Image.new('RGB',(240,60),'white');draw=ImageDraw.Draw(image)
    for start,end in [(20,100),(140,210)]:
        for x in range(start,end+1,10):draw.rectangle((x,20,min(x+3,end),39),fill='black')
    output=BytesIO();image.save(output,format='PNG');data=output.getvalue()
    def overlapping(crop):
        with Image.open(BytesIO(crop)) as image:w,h=image.size
        return [{'text':text,'confidence':.95,'bbox':[[3,3],[w-3,3],[w-3,h-3],[3,h-3]]}
                for text in ['duplicated','detector']]
    def line(crop):
        with Image.open(BytesIO(crop)) as image:w,h=image.size
        return [{'text':'TOTAL COST' if w==300 else '$22.56','confidence':.99}]
    pairs,audit=recover_merged_amount_pairs(data,regions,overlapping,recognize_line=line,max_calls=budget)
    assert len(pairs)==expected_pairs and audit['calls']<=budget
    if pairs:
        left,right=pairs[0]['rows'][0]
        assert left['recognition_mode']=='single_line_from_observed_pixel_envelope'
        assert left['bbox_basis']=='crop_foreground_ink_envelope'
        assert left['bbox'][0]==pytest.approx(20,abs=.5) and left['bbox'][2]==pytest.approx(101,abs=.5)
        assert right['bbox'][0]==pytest.approx(140,abs=.5) and right['bbox'][2]==pytest.approx(211,abs=.5)
        assert pairs[0]['cell_values_verified'] is False


@pytest.mark.parametrize('label,amount,confidence',[
    ('TOTAL TAX','$22.56',.99),('TOTAL COST','$22.58',.99),
    ('TOTAL COST','22.56',.99),('TOTAL COST','$22.56',.7),
])
def test_line_fallback_conflict_missing_currency_or_low_confidence_cannot_create_pair(label,amount,confidence):
    data,regions=image_and_region()
    def absent(crop):return []
    def line(crop):
        with Image.open(BytesIO(crop)) as image:w,h=image.size
        return [{'text':label if w in (300,200) else amount,'confidence':confidence}]
    assert recover_merged_amount_pairs(data,regions,absent,recognize_line=line)[0]==[]


@pytest.mark.parametrize('kwargs',[{'amount':'$22.58'},{'label':'TOTAL TAX'},{'outside':True}])
def test_conflicting_text_or_outside_crop_cannot_create_pair(kwargs):
    data,regions=image_and_region();recognize,_=recognizer(**kwargs)
    assert recover_merged_amount_pairs(data,regions,recognize)[0]==[]


def test_no_pixel_gap_no_calls_and_budget_or_failure_preserve_prose():
    data,regions=image_and_region(gap=False);recognize,calls=recognizer()
    assert recover_merged_amount_pairs(data,regions,recognize)[0]==[] and calls==[]
    data,regions=image_and_region()
    assert recover_merged_amount_pairs(data,regions,recognize,max_calls=1)[1]['calls']==0
    assert recover_merged_amount_pairs(data,regions,recognize,max_seconds=0)[1]['calls']==0
    def failure(data):raise RuntimeError('OCR unavailable')
    assert recover_merged_amount_pairs(data,regions,failure)[0]==[]


@pytest.mark.parametrize('misaligned',[False,True])
def test_label_word_boxes_join_only_when_same_line_and_all_original_words_match(misaligned):
    data,regions=image_and_region();calls=[]
    def recognize(crop):
        with Image.open(BytesIO(crop)) as image:w,h=image.size
        calls.append(crop)
        if len(calls)%2==0:
            return [{'text':'$22.56','confidence':.95,'bbox':[[3,3],[w-3,3],[w-3,21],[3,21]]}]
        return [{'text':text,'confidence':.95,'bbox':[[x,y],[x+50,y],[x+50,y+18],[x,y+18]]}
                for text,x,y in [('TOTAL',3,3),('COST',65,36 if misaligned else 3)]]
    pairs,_=recover_merged_amount_pairs(data,regions,recognize)
    assert bool(pairs) is not misaligned
    if pairs:assert pairs[0]['rows'][0][0]['text']=='TOTAL COST'
