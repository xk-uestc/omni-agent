from io import BytesIO
import hashlib
import numpy as np
import pytest
from PIL import Image,ImageDraw
from backend.perspective import PageBoundaryDetectionAgent
from backend.ocr import OcrPipeline,OcrResponse


QUAD=[[70,40],[520,75],[495,650],[95,620]]
def photograph():
    image=Image.new('RGB',(600,700),'#333333')
    draw=ImageDraw.Draw(image);draw.polygon([tuple(p) for p in QUAD],fill='white')
    draw.rectangle((250,280,280,300),fill='red')
    stream=BytesIO();image.save(stream,format='PNG');return stream.getvalue()


def test_detected_corners_follow_actual_page_outline_without_supplied_coordinates():
    with Image.open(BytesIO(photograph())) as image:result=PageBoundaryDetectionAgent().detect(image)
    assert result['status']=='candidate'
    assert np.array(result['quad_px'])==pytest.approx(np.array(QUAD),abs=4)
    assert result['minimum_edge_contrast']>=.12
    assert result['semantic_values_verified'] is False


def test_borderless_full_scan_or_internal_table_frame_is_not_a_page_boundary():
    image=Image.new('RGB',(600,700),'white')
    draw=ImageDraw.Draw(image);draw.rectangle((50,50,550,650),outline='black',width=4)
    assert PageBoundaryDetectionAgent().detect(image)['status']=='unavailable'


def test_two_separate_pages_are_ambiguous_and_neither_is_automatically_selected():
    image=Image.new('RGB',(1000,700),'#333333');draw=ImageDraw.Draw(image)
    draw.rectangle((30,40,460,660),fill='white');draw.rectangle((530,40,960,660),fill='white')
    assert PageBoundaryDetectionAgent().detect(image)['status']=='ambiguous'


def test_colored_paper_is_not_limited_to_bright_white_on_dark_background():
    image=Image.new('RGB',(600,700),'#a19a72');draw=ImageDraw.Draw(image)
    draw.polygon([tuple(p) for p in QUAD],fill='#699ac8')
    result=PageBoundaryDetectionAgent().detect(image)
    assert result['status']=='candidate'
    assert result['contrast_basis']=='distance_from_border_median_lab'
    assert np.array(result['quad_px'])==pytest.approx(np.array(QUAD),abs=4)


def test_large_camera_page_is_rectified_with_bounded_output_and_reversible_mapping():
    from backend.perspective import PerspectiveRectificationAgent
    from backend.image_geometry import point,inverse
    image=Image.new('RGB',(3200,3200),'white')
    corrected,matrix,evidence=PerspectiveRectificationAgent().rectify(image,
        [[10,10],[3190,10],[3190,3190],[10,3190]])
    assert corrected.width*corrected.height<=6_000_000
    assert evidence['output_resolution_scale']<1
    assert point(inverse(matrix),point(matrix,[1500,1400]))==pytest.approx([1500,1400],abs=1e-6)


class PixelExecutor:
    name='observed-pixels-not-ocr-benchmark'
    def __init__(self, outside=False, corrected_confidence=.99):
        self.outside=outside;self.calls=0;self.corrected_confidence=corrected_confidence
    def execute(self,data,*,language):
        self.calls+=1
        with Image.open(BytesIO(data)) as image:
            pixels=np.asarray(image);size=list(image.size)
        ys,xs=np.where((pixels[:,:,0]>200)&(pixels[:,:,1]<50)&(pixels[:,:,2]<50))
        regions=[{'text':'marker','confidence':.99,'bbox':[int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)]}]
        if self.outside and self.calls==1:regions.append({'text':'outside annotation','confidence':.99,'bbox':[5,5,30,20]})
        return OcrResponse('marker',.9 if self.calls==1 else self.corrected_confidence,
            {'coordinate_frame':{'scope':'ocr_executor_input','sha256':hashlib.sha256(data).hexdigest(),'size_px':size},'regions':regions})


def test_default_pipeline_attempts_verified_outline_and_maps_corrected_pixels_back():
    data=photograph();executor=PixelExecutor()
    result=OcrPipeline(executor).run(data,max_attempts=2)
    assert len(result.attempts)==2 and 'perspective_rectify' in result.selected_transforms
    detection=result.metadata['page_boundary_detection']
    assert detection['baseline_text_retained'] and detection['selected_for_text']
    evidence=result.metadata['image_geometry']['steps'][1]['evidence']
    assert evidence['selection']=='observed_page_boundary'
    location=result.metadata['regions'][0]['original_geometry']
    assert location['source_sha256']==hashlib.sha256(data).hexdigest()
    bounds=location['bbox_px']
    assert [(bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2]==pytest.approx([265.5,290.5],abs=2)


def test_outside_annotation_prevents_crop_and_preserves_original_result():
    executor=PixelExecutor(outside=True,corrected_confidence=.85)
    result=OcrPipeline(executor).run(photograph(),max_attempts=2)
    assert result.metadata['page_boundary_detection']['status']=='rejected'
    assert 'perspective_rectify' not in result.selected_transforms
    assert len(result.metadata['regions'])==2


def test_lower_confidence_rectification_does_not_replace_better_original_text():
    result=OcrPipeline(PixelExecutor(corrected_confidence=.85)).run(photograph(),max_attempts=2)
    assert len(result.attempts)==2
    assert 'perspective_rectify' not in result.selected_transforms
    assert result.metadata['page_boundary_detection']['selected_for_text'] is False


def test_failed_rectification_preserves_successful_baseline_status_and_location():
    from backend.ocr import OcrExecutionError
    class FailedCorrection(PixelExecutor):
        def execute(self,data,*,language):
            if self.calls:raise OcrExecutionError('correction executor unavailable')
            return super().execute(data,language=language)
    result=OcrPipeline(FailedCorrection()).run(photograph(),max_attempts=2)
    assert result.status=='ok' and result.text=='marker'
    assert result.attempts[-1].status=='failed'
    assert result.metadata['page_boundary_detection']['selected_for_text'] is False
    assert result.metadata['regions'][0]['original_geometry']['original_bbox_eligible']


def test_single_attempt_or_explicit_opt_out_does_not_crop():
    for options in [{'max_attempts':1},{'max_attempts':2,'auto_perspective':False}]:
        result=OcrPipeline(PixelExecutor()).run(photograph(),**options)
        assert 'perspective_rectify' not in result.selected_transforms
        assert 'page_boundary_detection' not in result.metadata


def test_low_confidence_outside_text_still_prevents_automatic_crop():
    class UncertainOutside(PixelExecutor):
        def execute(self,data,*,language):
            result=super().execute(data,language=language)
            if self.calls==1:result.metadata['regions'].append({'text':'faint note','confidence':.3,'bbox':[5,5,30,20]})
            return result
    result=OcrPipeline(UncertainOutside()).run(photograph(),max_attempts=2)
    assert result.metadata['page_boundary_detection']['status']=='rejected'
    assert 'perspective_rectify' not in result.selected_transforms


@pytest.mark.parametrize('mutation',['missing_polygon','wrong_source','invalid_confidence','singular_normalization'])
def test_corrupt_baseline_geometry_does_not_authorize_crop(mutation):
    from copy import deepcopy
    result=OcrPipeline(PixelExecutor()).run(photograph(),max_attempts=1)
    metadata=deepcopy(result.metadata)
    with Image.open(BytesIO(photograph())) as image:detection=PageBoundaryDetectionAgent().detect(image)
    detection['source_image_sha256']=hashlib.sha256(photograph()).hexdigest()
    if mutation=='missing_polygon':metadata['regions'][0]['original_geometry']['polygon_px']=[]
    if mutation=='wrong_source':metadata['regions'][0]['original_geometry']['source_sha256']='f'*64
    if mutation=='invalid_confidence':metadata['regions'][0]['confidence']=float('nan')
    if mutation=='singular_normalization':metadata['image_geometry']['steps'][0]['forward_affine']=[0]*6
    assert PageBoundaryDetectionAgent().retains_observed_text(detection,metadata) is False
