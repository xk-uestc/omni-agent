from io import BytesIO
import hashlib
import base64
import numpy as np
import pytest
from PIL import Image,ImageDraw
from backend.image_quality import ImageEnhancer
from backend.image_geometry import point,inverse,compose,map_region,attach_original_coordinates


QUAD=[[20,10],[185,25],[170,145],[35,130]]
def source():
    image=Image.new('RGB',(200,160),'#444444');draw=ImageDraw.Draw(image)
    draw.polygon([tuple(p) for p in QUAD],fill='white');draw.ellipse((76,66,84,74),fill='red')
    stream=BytesIO();image.save(stream,format='PNG');return stream.getvalue()


def test_rectification_maps_actual_red_pixels_and_retains_hash_bound_original_polygon():
    data=source();result=ImageEnhancer().enhance(data,transforms=('perspective_rectify',),perspective_quad=QUAD)
    geometry=result.geometry
    assert geometry['version']=='image-projective-chain-v1'
    with Image.open(BytesIO(result.image_bytes)) as image:
        pixels=np.asarray(image);ys,xs=np.where((pixels[:,:,0]>200)&(pixels[:,:,1]<30)&(pixels[:,:,2]<30))
    expected=point(geometry['source_to_output'],[80.5,70.5])
    assert [xs.mean()+.5,ys.mean()+.5]==pytest.approx(expected,abs=1.2)
    box=[float(xs.min()),float(ys.min()),float(xs.max()+1),float(ys.max()+1)]
    mapped=map_region(box,geometry)
    assert mapped['original_bbox_eligible']
    assert [(mapped['bbox_px'][0]+mapped['bbox_px'][2])/2,(mapped['bbox_px'][1]+mapped['bbox_px'][3])/2]==pytest.approx([80.5,70.5],abs=1.2)
    metadata={'coordinate_frame':{'scope':'ocr_executor_input','sha256':hashlib.sha256(result.image_bytes).hexdigest(),
        'size_px':[result.width,result.height]},'regions':[{'text':'red marker','bbox':box}]}
    attached=attach_original_coordinates(metadata,geometry,data,result.image_bytes)
    assert attached['original_pixel_mapping']['status']=='mapped'
    assert attached['regions'][0]['original_geometry']==mapped
    changed=attach_original_coordinates(metadata,geometry,b'wrong source',result.image_bytes)
    assert changed['original_pixel_mapping']['status']=='unavailable'


def test_perspective_chain_composes_with_rotation_crop_and_resize():
    result=ImageEnhancer().enhance(source(),transforms=('perspective_rectify','rotate_to_upright','upscale'),
        perspective_quad=QUAD,rotation_degrees=90,max_dimension=300)
    transform=result.geometry['source_to_output'];reverse=result.geometry['output_to_source']
    assert len(transform)==9
    for original in [[80,70],[50,40],[150,100]]:
        assert point(reverse,point(transform,original))==pytest.approx(original,abs=1e-8)
    identity=compose(reverse,transform)
    assert point(identity,[50,40])==pytest.approx([50,40],abs=1e-8)


def test_exif_normalized_corners_map_back_to_stored_original_pixels():
    from backend.image_geometry import exif_affine
    with Image.open(BytesIO(source())) as image:
        exif=Image.Exif();exif[274]=6;output=BytesIO();image.save(output,format='PNG',exif=exif)
    normalized=[point(exif_affine(6,200,160),QUAD[i]) for i in [3,0,1,2]]
    result=ImageEnhancer().enhance(output.getvalue(),transforms=('perspective_rectify',),perspective_quad=normalized)
    with Image.open(BytesIO(result.image_bytes)) as image:
        pixels=np.asarray(image);ys,xs=np.where((pixels[:,:,0]>200)&(pixels[:,:,1]<30)&(pixels[:,:,2]<30))
    assert [xs.mean()+.5,ys.mean()+.5]==pytest.approx(point(result.geometry['source_to_output'],[80.5,70.5]),abs=1.2)


def test_ocr_pipeline_keeps_perspective_executor_frame_and_original_source_hash():
    from backend.ocr import OcrPipeline,OcrResponse
    class Executor:
        name='perspective-pixel-fixture'
        def execute(self,data,*,language):
            with Image.open(BytesIO(data)) as image:
                pixels=np.asarray(image);size=list(image.size)
                ys,xs=np.where((pixels[:,:,0]>200)&(pixels[:,:,1]<30)&(pixels[:,:,2]<30))
            return OcrResponse('marker',.99,{'coordinate_frame':{'scope':'ocr_executor_input',
                'sha256':hashlib.sha256(data).hexdigest(),'size_px':size},'regions':[{'text':'marker',
                'bbox':[int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)]}]})
    result=OcrPipeline(Executor()).run(source(),perspective_quad=QUAD,max_attempts=1)
    assert result.status=='ok' and 'perspective_rectify' in result.selected_transforms
    assert result.metadata['original_pixel_mapping']['status']=='mapped'
    assert result.metadata['regions'][0]['original_geometry']['source_sha256']==hashlib.sha256(source()).hexdigest()


@pytest.mark.parametrize('quad',[None,[[0,0],[190,150],[190,0],[0,150]],
    [[-1,10],[185,25],[170,145],[35,130]],[[20,10],[35,130],[170,145],[185,25]],
    [[0,0],[1,0],[1,1],[0,1]]])
def test_invalid_page_corners_are_rejected_without_claiming_rectification(quad):
    with pytest.raises(ValueError):ImageEnhancer().enhance(source(),transforms=('perspective_rectify',),perspective_quad=quad)


def test_projective_horizon_and_singular_transform_do_not_produce_highlights():
    with pytest.raises(ValueError):inverse([1,0,0,0,0,0,0,0,1])
    geometry={'source':{'sha256':'a'*64,'size_px':[200,160]},'output':{'size_px':[200,160]},
        'output_to_source':[1,0,0,0,1,0,.1,0,-1]}
    assert not map_region([0,0,20,20],geometry)['original_bbox_eligible']


def test_image_enhancement_endpoint_returns_projective_chain_and_rejects_missing_corners():
    from fastapi.testclient import TestClient
    from backend.app import app
    with TestClient(app) as client:
        body={'image_base64':base64.b64encode(source()).decode(),'transforms':['perspective_rectify'],'perspective_quad':QUAD}
        response=client.post('/api/v1/documents/image-enhance',json=body)
        assert response.status_code==200
        assert response.json()['geometry']['version']=='image-projective-chain-v1'
        del body['perspective_quad']
        assert client.post('/api/v1/documents/image-enhance',json=body).status_code==400
