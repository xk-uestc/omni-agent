import hashlib
from io import BytesIO
import fitz
from PIL import Image
import pytest
from backend.pdf_table_preview import PdfTablePreviewAgent
from backend.chunk_cleaning import DocumentChunker
from backend.image_geometry import attach_original_coordinates
from backend.image_quality import ImageEnhancer


def pdf(native=True,pages=2):
    with fitz.open() as document:
        for i in range(pages):
            page=document.new_page(width=300,height=300)
            if native:page.insert_text((30,30),f'Native original evidence on page {i+1} remains unchanged.')
        return document.tobytes()


class FakePipeline:
    def __init__(self):self.calls=[]
    def run(self,png,language='eng'):
        self.calls.append(language)
        with Image.open(BytesIO(png)) as image:size=[image.width,image.height]
        cells=[[{'row':r,'column':c,'rowspan':1,'colspan':1,'bbox_px':[10+c*100,10+r*50,110+c*100,60+r*50],
                 'status':'ocr_observed','text':text,'observations':[]}
                for c,text in enumerate(row)] for r,row in enumerate([['Year','Sales'],['2024','10'],['2025','20']])]
        metadata={'source_image_sha256':hashlib.sha256(png).hexdigest(),
            'coordinate_frame':{'scope':'ocr_executor_input','size_px':size,'sha256':hashlib.sha256(png).hexdigest()},
            'scanned_grids':{'tables':[{'status':'layout_observed','row_count':3,'column_count':2,'cells':cells}]}}
        enhanced=ImageEnhancer().enhance(png,transforms=())
        metadata['coordinate_frame']['sha256']=hashlib.sha256(enhanced.image_bytes).hexdigest()
        metadata=attach_original_coordinates(metadata,enhanced.geometry,png,enhanced.image_bytes)
        return {'status':'ok','text':'OCR candidate text must not replace the native original text.',
                'confidence':.95,'metadata':metadata,'attempts':[],'selected_transforms':[]}


def test_native_pdf_preview_keeps_text_and_binds_exact_selected_page():
    raw=pdf();parsed=DocumentChunker().parse_pdf(raw)
    original=parsed.to_dict();pipeline=FakePipeline()
    selection=[{'page_no':2,'table_index':0,'header_rows':1}]
    result=PdfTablePreviewAgent().run(raw,selection,ocr_pipeline=pipeline,chunks=parsed.chunks,language='chi_sim+eng')
    assert pipeline.calls==['chi_sim+eng']
    assert result[0]['status']=='structure_observed'
    assert result[0]['page_no']==2 and result[0]['native_text_replaced'] is False
    assert result[0]['body_cells'][0][1]['original_geometry']['pdf_geometry']['page_no']==2
    assert result[0]['source_pdf_sha256']==hashlib.sha256(raw).hexdigest()
    assert parsed.to_dict()==original


def test_scanned_pdf_reuses_ocr_without_second_recognition():
    raw=pdf(native=False,pages=1);pipeline=FakePipeline()
    parsed=DocumentChunker().parse_pdf(raw,ocr_pipeline=pipeline)
    assert len(pipeline.calls)==1
    result=PdfTablePreviewAgent().run(raw,[{'page_no':1,'table_index':0,'header_rows':0}],
        ocr_pipeline=pipeline,chunks=parsed.chunks)
    assert len(pipeline.calls)==1
    assert result[0]['ocr_origin']=='reused_page_ocr'
    assert len(result[0]['body_cells'])==3


@pytest.mark.parametrize('selections',[
    [{'page_no':0,'table_index':0,'header_rows':0}],
    [{'page_no':3,'table_index':0,'header_rows':0}],
    [{'page_no':1,'table_index':32,'header_rows':0}],
    [{'page_no':1,'table_index':0,'header_rows':6}],
    [{'page_no':True,'table_index':0,'header_rows':0}],
    [{'page_no':1,'table_index':0,'header_rows':0}]*2,
])
def test_invalid_selection_does_not_ocr(selections):
    pipeline=FakePipeline()
    with pytest.raises(ValueError):PdfTablePreviewAgent().run(pdf(),selections,ocr_pipeline=pipeline)
    assert pipeline.calls==[]


def test_different_tables_on_same_page_share_one_ocr():
    pipeline=FakePipeline()
    result=PdfTablePreviewAgent().run(pdf(),[
        {'page_no':1,'table_index':0,'header_rows':0},{'page_no':1,'table_index':1,'header_rows':1}],ocr_pipeline=pipeline)
    assert len(pipeline.calls)==1
    assert result[0]['status']=='structure_observed'
    assert result[1]['reason']=='selected_grid_unavailable'


def test_unavailable_ocr_does_not_invent_preview():
    result=PdfTablePreviewAgent().run(pdf(),[{'page_no':1,'table_index':0,'header_rows':0}],ocr_pipeline=None)
    assert result[0]['reason']=='ocr_not_configured'


def test_foreign_pdf_coordinates_rejected():
    structure={'body_cells':[[{'status':'ocr_observed','original_geometry':{'pdf_geometry':{
        'pdf_highlight_eligible':True,'source_pdf_sha256':'foreign','page_no':1}}}]],'columns':[]}
    assert not PdfTablePreviewAgent.coordinates_match(structure,'expected',1)


def test_chunks_preview_api_retains_native_text_and_returns_sidecar(monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from backend import app as app_module
    monkeypatch.setattr(app_module,'ocr_pipeline',FakePipeline())
    client=TestClient(app_module.app)
    payload={'document_id':'pdf-header-test','modality':'pdf','file_base64':base64.b64encode(pdf()).decode(),
             'pdf_table_headers':[{'page_no':2,'table_index':0,'header_rows':1}]}
    response=client.post('/api/v1/documents/chunks-preview',json=payload)
    assert response.status_code==200
    data=response.json()
    assert data['table_structures'][0]['status']=='structure_observed'
    assert 'Native original evidence' in '\n'.join(c['text'] for c in data['chunks'])
    assert 'OCR candidate text' not in '\n'.join(c['text'] for c in data['chunks'])
    payload['modality']='image'
    assert client.post('/api/v1/documents/chunks-preview',json=payload).status_code==400
