"""Geometry is tested against actual image pixels, not just its own inverse."""
from copy import deepcopy
import hashlib
from io import BytesIO

import pytest
from PIL import Image,ImageDraw,ImageOps

from backend.image_geometry import point,map_region,attach_original_coordinates,bind_pdf_coordinates
from backend.image_quality import ImageEnhancer
from backend.ocr import OcrPipeline,OcrResponse


def encode(image,orientation=None):
    output=BytesIO()
    exif=Image.Exif()
    if orientation is not None:exif[274]=orientation
    image.save(output,format='PNG',exif=exif)
    return output.getvalue()


@pytest.mark.parametrize('orientation',range(1,9))
def test_all_exif_orientations_match_asymmetric_stored_pixel_locations(orientation):
    image=Image.new('RGB',(3,2))
    colors=[(255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255),(0,255,255)]
    image.putdata(colors)
    result=ImageEnhancer().enhance(encode(image,orientation))
    with Image.open(BytesIO(result.image_bytes)) as transformed:
        for index,color in enumerate(colors):
            target=point(result.geometry['source_to_output'],[index%3+.5,index//3+.5])
            assert transformed.getpixel((int(target[0]),int(target[1])))==color
        assert transformed.getexif().get(274) is None
    if orientation!=1:assert 'exif_transpose' in result.applied_transforms


@pytest.mark.parametrize('degrees',[90,180,270,-90,360])
def test_quarter_turn_transform_matches_every_actual_pixel(degrees):
    image=Image.new('RGB',(3,2))
    colors=[(255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255),(0,255,255)]
    image.putdata(colors)
    result=ImageEnhancer().enhance(encode(image),transforms=('rotate_to_upright',),rotation_degrees=degrees)
    with Image.open(BytesIO(result.image_bytes)) as transformed:
        for index,color in enumerate(colors):
            target=point(result.geometry['source_to_output'],[index%3+.5,index//3+.5])
            assert transformed.getpixel((int(target[0]),int(target[1])))==color


@pytest.mark.parametrize('orientation',[1,2,6,7])
@pytest.mark.parametrize('angle',[5,-7,31])
def test_exif_rotation_crop_and_nonuniform_rounded_resize_locate_actual_marker(orientation,angle):
    image=Image.new('RGB',(121,79),'white')
    ImageDraw.Draw(image).rectangle((26,22,44,40),fill='red')
    data=encode(image,orientation)
    with Image.open(BytesIO(data)) as stored:
        rotated=ImageOps.exif_transpose(stored).rotate(-angle,expand=True)
        crop=(7,9,rotated.width-8,rotated.height-6)
    result=ImageEnhancer().enhance(data,
        transforms=('rotate_to_upright','crop','upscale'),rotation_degrees=angle,
        crop_box=crop,max_dimension=251)
    target=point(result.geometry['source_to_output'],[35.5,31.5])
    # The crop removes margins but retains the real marker in every direction.
    if 0<=target[0]<result.width and 0<=target[1]<result.height:
        with Image.open(BytesIO(result.image_bytes)) as transformed:
            color=transformed.getpixel((int(target[0]),int(target[1])))
            assert color[0]>220 and color[1]<50 and color[2]<50
    else:
        pytest.fail('marker fixture outside tested crop')
    back=point(result.geometry['output_to_source'],target)
    assert back==pytest.approx([35.5,31.5],abs=1e-9)


def frame_metadata(result):
    return {'coordinate_frame':{'scope':'ocr_executor_input','size_px':[result.width,result.height],
                               'sha256':hashlib.sha256(result.image_bytes).hexdigest()},
            'regions':[{'bbox':[[10,10],[20,10],[20,20],[10,20]],'text':'Budget','confidence':.9}]}


def test_padding_and_invalid_boxes_never_become_original_highlight_candidates():
    result=ImageEnhancer().enhance(encode(Image.new('RGB',(120,80),'white')),
        transforms=('rotate_to_upright',),rotation_degrees=25)
    for box in ([0,0,5,5],[-1,-1,3,3],[1,1,1,2],[0,0,float('nan'),3]):
        assert map_region(box,result.geometry)['original_bbox_eligible'] is False
    region=map_region([result.width/2-5,result.height/2-5,result.width/2+5,result.height/2+5],result.geometry)
    assert region['original_bbox_eligible'] is True
    assert len(region['polygon_px'])==4
    assert region['polygon_px'][0][1]!=region['polygon_px'][1][1]


@pytest.mark.parametrize('mutation',['frame_hash','frame_size','source_hash','inverse'])
def test_tampered_frame_cannot_claim_original_coordinates(mutation):
    data=encode(Image.new('RGB',(60,40),'white'))
    result=ImageEnhancer().enhance(data)
    metadata=frame_metadata(result)
    geometry=deepcopy(result.geometry)
    if mutation=='frame_hash':metadata['coordinate_frame']['sha256']='0'*64
    if mutation=='frame_size':metadata['coordinate_frame']['size_px']=[40,60]
    if mutation=='source_hash':geometry['source']['sha256']='0'*64
    if mutation=='inverse':geometry['output_to_source'][2]=1
    mapped=attach_original_coordinates(metadata,geometry,data,result.image_bytes)
    assert mapped['original_pixel_mapping']['status']=='unavailable'
    assert 'original_geometry' not in mapped['regions'][0]


def test_table_and_recovered_crop_mapping_preserve_ocr_fact_status():
    data=encode(Image.new('RGB',(60,40),'white'))
    result=ImageEnhancer().enhance(data,transforms=('rotate_to_upright',),rotation_degrees=90)
    metadata=frame_metadata(result)
    metadata['scanned_grids']={'tables':[{'bbox_px':[1,1,30,50], 'cell_values_verified':False,
        'cells':[[{'bbox_px':[2,2,28,48],'text':'47000','status':'cell_crop_ocr_observed',
            'crop_observations':[{'crop_bbox_px':[5,5,25,45],'text':'47000'}]}]]}]}
    mapped=attach_original_coordinates(metadata,result.geometry,data,result.image_bytes)
    cell=mapped['scanned_grids']['tables'][0]['cells'][0][0]
    assert cell['original_geometry']['original_bbox_eligible']
    assert cell['crop_observations'][0]['original_crop_geometry']['original_bbox_eligible']
    assert mapped['scanned_grids']['tables'][0]['cell_values_verified'] is False
    assert cell['text']=='47000' and cell['status']=='cell_crop_ocr_observed'
    assert 'original_geometry' not in metadata['regions'][0]


def test_borderless_recovered_text_and_crop_have_separate_original_polygons():
    data=encode(Image.new('RGB',(100,80),'white'))
    transformed=ImageEnhancer().enhance(data,transforms=('rotate_to_upright',),rotation_degrees=90)
    metadata=frame_metadata(transformed)
    metadata['table_layout']={'status':'candidate','cell_values_verified':False,'rows':[[{
        'text':'1','bbox':[20,30,30,40],'crop_bbox_px':[15,25,40,50],
        'observation_origin':'borderless_cell_crop_ocr','crop_sha256':'a'*64}]],
        'continuity_evidence':[{'semantic_assignment_verified':False,
            'observations':[{'text':'continuation','bbox':[20,30,30,40]}]}]}
    metadata['table_layout']['regional_candidates']=[{'rows':[[{'text':'second area','bbox':[20,30,30,40]}]]}]
    metadata['table_layout']['sparse_observations']=[{'is_table':False,'rows':[[{'text':'short item','bbox':[20,30,30,40]}]]}]
    mapped=attach_original_coordinates(metadata,transformed.geometry,data,transformed.image_bytes)
    cell=mapped['table_layout']['rows'][0][0]
    # Enhancer rotation_degrees=90 performs a clockwise correction. Thus
    # output (x,y) maps to source (y,80-x), with the original image height.
    assert cell['original_geometry']['bbox_px']==pytest.approx([30,50,40,60])
    assert cell['original_crop_geometry']['bbox_px']==pytest.approx([25,40,50,65])
    assert cell['crop_sha256']=='a'*64
    assert mapped['table_layout']['cell_values_verified'] is False
    bridge=mapped['table_layout']['continuity_evidence'][0]
    assert bridge['semantic_assignment_verified'] is False
    assert bridge['observations'][0]['original_geometry']['bbox_px']==pytest.approx([30,50,40,60])
    assert 'original_geometry' not in metadata['table_layout']['continuity_evidence'][0]['observations'][0]
    assert mapped['table_layout']['regional_candidates'][0]['rows'][0][0]['original_geometry']['bbox_px']==pytest.approx([30,50,40,60])
    assert mapped['table_layout']['sparse_observations'][0]['rows'][0][0]['original_geometry']['bbox_px']==pytest.approx([30,50,40,60])


@pytest.mark.parametrize('regional',[None,{},[{}]*17])
def test_invalid_regional_layout_type_or_budget_never_claims_mapped_original_frames(regional):
    data=encode(Image.new('RGB',(60,40),'white'))
    transformed=ImageEnhancer().enhance(data)
    metadata=frame_metadata(transformed)
    metadata['table_layout']={'regional_candidates':regional}
    mapped=attach_original_coordinates(metadata,transformed.geometry,data,transformed.image_bytes)
    assert mapped['original_pixel_mapping']['status']=='unavailable'
    assert mapped['original_pixel_mapping']['reason']=='regional_layout_budget_or_type_invalid'
    assert 'original_geometry' not in mapped['regions'][0]


@pytest.mark.parametrize('sparse',[None,{},[{}]*33])
def test_invalid_sparse_layout_type_or_budget_refuses_original_mapping(sparse):
    data=encode(Image.new('RGB',(60,40),'white'))
    transformed=ImageEnhancer().enhance(data)
    metadata=frame_metadata(transformed)
    metadata['table_layout']={'sparse_observations':sparse}
    mapped=attach_original_coordinates(metadata,transformed.geometry,data,transformed.image_bytes)
    assert mapped['original_pixel_mapping']['status']=='unavailable'
    assert mapped['original_pixel_mapping']['reason']=='sparse_layout_budget_or_type_invalid'
    assert 'original_geometry' not in mapped['regions'][0]


@pytest.mark.parametrize('baseline_wins',[True,False])
def test_selected_attempt_keeps_its_own_source_geometry(baseline_wins):
    class Quality:
        def analyze(self,_):return type('Q',(),{'recommended_transforms':(),'quality_score':1})()
    class Executor:
        name='geometry-fixture'
        def __init__(self):self.calls=0
        def execute(self,data,*,language):
            self.calls+=1
            with Image.open(BytesIO(data)) as image:size=list(image.size)
            return OcrResponse('observed',(.98 if baseline_wins else .65) if self.calls==1 else .9,
                {'coordinate_frame':{'scope':'ocr_executor_input','size_px':size,'sha256':hashlib.sha256(data).hexdigest()},
                 'regions':[{'bbox':[10,10,20,20],'text':'observed','confidence':.9}],
                 'orientation':{'status':'estimated','correction_ccw_degrees':90 if self.calls==1 else 0}})
    result=OcrPipeline(Executor(),analyzer=Quality()).run(encode(Image.new('RGB',(60,40),'white')),max_attempts=2)
    assert result.metadata['original_pixel_mapping']['status']=='mapped'
    assert result.metadata['image_geometry']['output']['size_px']==([60,40] if baseline_wins else [40,60])
    box=result.metadata['regions'][0]['original_geometry']['bbox_px']
    assert box==pytest.approx([10,10,20,20] if baseline_wins else [40,10,50,20])


@pytest.mark.parametrize('rotation',[0,90,180,270])
@pytest.mark.parametrize('cropped',[True,False])
def test_pdf_rotation_cropbox_and_corrected_raster_map_actual_red_pixels(rotation,cropped):
    import fitz
    import numpy as np
    from backend.chunk_cleaning import DocumentChunker
    with fitz.open() as pdf:
        page=pdf.new_page(width=120,height=100)
        marker=Image.new('RGB',(20,20),'red')
        page.insert_image(fitz.Rect(30,35,50,55),stream=encode(marker))
        if cropped:page.set_cropbox(fitz.Rect(10,15,110,85))
        page.set_rotation(rotation)
        pix=page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
        data=pix.tobytes('png')
        page_geometry=DocumentChunker._pdf_page_geometry(page,render_scale=2,render_size=(pix.width,pix.height))
        pdf_hash=hashlib.sha256(pdf.tobytes()).hexdigest()
    transformed=ImageEnhancer().enhance(data,transforms=('rotate_to_upright','upscale'),
                                       rotation_degrees=7,max_dimension=501)
    with Image.open(BytesIO(transformed.image_bytes)) as image:
        pixels=np.asarray(image)
        ys,xs=np.where((pixels[:,:,0]>240)&(pixels[:,:,1]<20)&(pixels[:,:,2]<20))
    metadata=frame_metadata(transformed)
    metadata['regions']=[{'bbox':[int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)],'text':'marker'}]
    mapped=attach_original_coordinates(metadata,transformed.geometry,data,transformed.image_bytes)
    candidate=deepcopy(mapped)
    candidate['table_layout']={'status':'candidate','rows':[[{
        'text':'marker','original_geometry':deepcopy(mapped['regions'][0]['original_geometry']),
        'original_crop_geometry':deepcopy(mapped['regions'][0]['original_geometry'])}]],
        'continuity_evidence':[{'semantic_assignment_verified':False,'observations':[{
            'text':'observed continuation','original_geometry':deepcopy(mapped['regions'][0]['original_geometry'])}]}]}
    candidate['table_layout']['regional_candidates']=[{'rows':[[{
        'text':'local area','original_geometry':deepcopy(mapped['regions'][0]['original_geometry'])}]]}]
    candidate['table_layout']['sparse_observations']=[{'is_table':False,'rows':[[{
        'text':'short item','original_geometry':deepcopy(mapped['regions'][0]['original_geometry'])}]]}]
    mapped['borderless_table_evidence']=candidate
    extended=bind_pdf_coordinates(mapped,page_geometry,hashlib.sha256(data).hexdigest(),
                                  [pix.width,pix.height],pdf_hash,1)
    location=extended['regions'][0]['original_geometry']['pdf_geometry']
    assert location['pdf_highlight_eligible']
    user_center=[sum(p[i] for p in location['pdf_user_polygon_pt'])/4 for i in range(2)]
    local_center=[sum(p[i] for p in location['fitz_unrotated_polygon_pt'])/4 for i in range(2)]
    assert user_center==pytest.approx([40,55],abs=1)
    assert local_center==pytest.approx([30,30] if cropped else [40,45],abs=1)
    borderless=extended['borderless_table_evidence']['table_layout']['rows'][0][0]
    assert borderless['original_geometry']['pdf_geometry']==location
    assert borderless['original_crop_geometry']['pdf_geometry']==location
    bridge=extended['borderless_table_evidence']['table_layout']['continuity_evidence'][0]
    assert bridge['observations'][0]['original_geometry']['pdf_geometry']==location
    assert bridge['semantic_assignment_verified'] is False
    assert extended['borderless_table_evidence']['table_layout']['regional_candidates'][0]['rows'][0][0]['original_geometry']['pdf_geometry']==location
    assert extended['borderless_table_evidence']['table_layout']['sparse_observations'][0]['rows'][0][0]['original_geometry']['pdf_geometry']==location
    broken=bind_pdf_coordinates(mapped,page_geometry,'0'*64,[pix.width,pix.height],pdf_hash,1)
    assert broken['pdf_coordinate_mapping']['status']=='unavailable'
    assert broken['borderless_table_evidence']['pdf_coordinate_mapping']['status']=='unavailable'
    # Rebind an already-mapped record, rather than a fresh input. No old PDF
    # polygon may survive a failed replacement binding, even in candidates.
    for bad_hash,bad_page in [('0'*64,1),(hashlib.sha256(data).hexdigest(),0)]:
        stale=bind_pdf_coordinates(extended,page_geometry,bad_hash,[pix.width,pix.height],pdf_hash,bad_page)
        assert stale['pdf_coordinate_mapping']['status']=='unavailable'
        assert 'pdf_geometry' not in stale['regions'][0]['original_geometry']
        assert 'pdf_geometry' not in stale['borderless_table_evidence']['table_layout']['rows'][0][0]['original_crop_geometry']
        assert 'pdf_geometry' not in stale['borderless_table_evidence']['table_layout']['continuity_evidence'][0]['observations'][0]['original_geometry']
        assert 'pdf_geometry' not in stale['borderless_table_evidence']['table_layout']['regional_candidates'][0]['rows'][0][0]['original_geometry']
        assert 'pdf_geometry' not in stale['borderless_table_evidence']['table_layout']['sparse_observations'][0]['rows'][0][0]['original_geometry']
    assert extended['regions'][0]['original_geometry']['pdf_geometry']==location


def test_unknown_enhancer_or_executor_geometry_remains_explicitly_unavailable():
    assert attach_original_coordinates({},None,b'original',b'input')['original_pixel_mapping']=={
        'status':'unavailable','original_bbox_eligible':False,'reason':'enhancer_geometry_missing'}
    result=ImageEnhancer().enhance(encode(Image.new('RGB',(30,20),'white')))
    for frame in (None,{},'unknown'):
        attached=attach_original_coordinates({'coordinate_frame':frame},result.geometry,b'wrong-source',result.image_bytes)
        assert attached['original_pixel_mapping']['status']=='unavailable'


def test_implicit_exif_correction_is_reported_in_selected_attempt():
    class Executor:
        name='exif-frame'
        def execute(self,data,*,language):
            with Image.open(BytesIO(data)) as image:size=list(image.size)
            return OcrResponse('observed',.95,{'coordinate_frame':{'scope':'ocr_executor_input',
                'sha256':hashlib.sha256(data).hexdigest(),'size_px':size}})
    class Quality:
        def analyze(self,_):return type('Q',(),{'recommended_transforms':(),'quality_score':1})()
    result=OcrPipeline(Executor(),analyzer=Quality()).run(encode(Image.new('RGB',(60,40),'white'),6))
    assert result.selected_transforms==('exif_transpose',)
    assert result.attempts[0].transforms==('exif_transpose',)


def test_better_baseline_text_does_not_discard_corrected_grid_evidence():
    class Quality:
        def analyze(self,_):return type('Q',(),{'recommended_transforms':(),'quality_score':1})()
    class Executor:
        name='separate-text-and-grid'
        def __init__(self):self.calls=0
        def execute(self,data,*,language):
            self.calls+=1
            with Image.open(BytesIO(data)) as image:size=list(image.size)
            metadata={'coordinate_frame':{'scope':'ocr_executor_input','size_px':size,
                        'sha256':hashlib.sha256(data).hexdigest()},
                      'orientation':{'status':'estimated','correction_ccw_degrees':90 if self.calls==1 else 0}}
            if self.calls==2:
                metadata['scanned_grids']={'status':'observed','tables':[{'bbox_px':[2,2,35,55],
                    'cell_values_verified':False,'cells':[[{'bbox_px':[10,10,20,20],
                        'status':'ocr_observed','text':'47000'}]]}]}
            return OcrResponse('better baseline' if self.calls==1 else 'corrected text',.98 if self.calls==1 else .9,metadata)
    result=OcrPipeline(Executor(),analyzer=Quality()).run(encode(Image.new('RGB',(60,40),'white')),max_attempts=2)
    assert result.text=='better baseline' and result.selected_transforms==()
    assert result.metadata['coordinate_frame']['size_px']==[60,40]
    candidate=result.metadata['scanned_table_evidence']
    assert candidate['source_attempt']==2 and candidate['coordinate_frame']['size_px']==[40,60]
    location=candidate['scanned_grids']['tables'][0]['cells'][0][0]['original_geometry']
    assert location['bbox_px']==pytest.approx([40,10,50,20])
    assert candidate['ocr_values_independently_verified'] is False


def test_baseline_text_and_corrected_borderless_candidate_keep_separate_frames():
    class Quality:
        def analyze(self,_):return type('Q',(),{'recommended_transforms':(),'quality_score':1})()
    class Executor:
        name='separate-borderless'
        def __init__(self):self.calls=0
        def execute(self,data,*,language):
            self.calls+=1
            with Image.open(BytesIO(data)) as image:size=list(image.size)
            metadata={'coordinate_frame':{'scope':'ocr_executor_input','size_px':size,'sha256':hashlib.sha256(data).hexdigest()},
                'orientation':{'status':'estimated','correction_ccw_degrees':90 if self.calls==1 else 0}}
            if self.calls==2:
                metadata['table_layout']={'status':'candidate','row_count':1,'ragged_row_count':0,
                    'rows':[[{'text':'1','bbox':[10,10,20,20],'crop_bbox_px':[5,5,25,25]}]]}
            return OcrResponse('baseline' if self.calls==1 else 'corrected',.99 if self.calls==1 else .9,metadata)
    result=OcrPipeline(Executor(),analyzer=Quality()).run(encode(Image.new('RGB',(60,40),'white')),max_attempts=2)
    assert result.text=='baseline'
    candidate=result.metadata['borderless_table_evidence']
    assert candidate['source_attempt']==2
    assert candidate['coordinate_frame']['size_px']==[40,60]
    cell=candidate['table_layout']['rows'][0][0]
    assert cell['original_geometry']['bbox_px']==pytest.approx([40,10,50,20])
    assert cell['original_crop_geometry']['bbox_px']==pytest.approx([35,5,55,25])


@pytest.mark.parametrize('fault',['foreign_region_hash','nan_polygon','outside_polygon','foreign_scope'])
def test_pdf_region_requires_its_own_valid_source_identity(fault):
    data=encode(Image.new('RGB',(60,40),'white'))
    transformed=ImageEnhancer().enhance(data)
    mapped=attach_original_coordinates(frame_metadata(transformed),transformed.geometry,data,transformed.image_bytes)
    location=mapped['regions'][0]['original_geometry']
    location['pdf_geometry']={'pdf_highlight_eligible':True,'page_no':99}
    if fault=='foreign_region_hash':location['source_sha256']='0'*64
    elif fault=='nan_polygon':location['polygon_px'][0][0]=float('nan')
    elif fault=='outside_polygon':location['polygon_px'][0][0]=100
    else:location['coordinate_scope']='unknown'
    identity=[1,0,0,1,0,0]
    page={'mapping_status':'complete','display_rect_fitz_pt':[0,0,60,40],
        'mappings':{key:identity for key in ['pdf_user_to_fitz_unrotated','fitz_unrotated_to_display','fitz_display_to_render_pixel']}}
    result=bind_pdf_coordinates(mapped,page,hashlib.sha256(data).hexdigest(),[60,40],'a'*64,1)
    assert result['pdf_coordinate_mapping']['eligible_region_count']==0
    assert 'pdf_geometry' not in result['regions'][0]['original_geometry']
