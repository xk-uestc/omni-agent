"""Real photo embedded in a scan-only PDF, using actual production ingestion."""
from io import BytesIO
from pathlib import Path
import argparse
import base64
import hashlib
import json
import sys
import time
import uuid
from urllib.request import Request,urlopen
import fitz

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.chunk_cleaning import DocumentChunker
from backend.ocr import OcrPipeline,RapidOcrExecutor
from verify_camera_prose import score


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--http',action='store_true');args=parser.parse_args()
    files=['ict-track8/backend/chunk_cleaning.py','ict-track8/backend/ocr.py',
        'ict-track8/backend/orientation_quality.py','ict-track8/backend/image_geometry.py']
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    reference=json.loads((ROOT/'ict-track8/evaluation/camera_prose_51_1.json').read_text(encoding='utf-8'))
    raw=(Path(r'D:\ICT8-OfficialDatasets\document-camera-examples')/reference['filename']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=reference['sha256']:raise ValueError('Camera identity changed')
    width,height=reference['size_px']
    with fitz.open() as document:
        page=document.new_page(width=width/2,height=height/2)
        page.insert_image(page.rect,stream=raw)
        pdf=document.tobytes()
    pdf_sha=hashlib.sha256(pdf).hexdigest()
    with fitz.open(stream=pdf,filetype='pdf') as document:
        assert document[0].get_text()==''
        render=document[0].get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
        render_sha=hashlib.sha256(render.tobytes('png')).hexdigest()
    start=time.monotonic()
    if args.http:
        request=Request('http://127.0.0.1:8030/api/v1/documents/chunks-preview',
            data=json.dumps({'document_id':'actual-camera-prose-'+uuid.uuid4().hex,'modality':'pdf',
                'language':'eng','file_base64':base64.b64encode(pdf).decode()}).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:parsed=json.load(response)
    else:
        parsed=DocumentChunker().parse_pdf(pdf,document_id='actual-camera-prose',
            ocr_pipeline=OcrPipeline(RapidOcrExecutor()),language='eng').to_dict()
    metadata=next(chunk['metadata']['ocr_metadata'] for chunk in parsed['chunks']
        if chunk.get('metadata',{}).get('ocr_metadata',{}).get('regions'))
    evaluation=score({'metadata':metadata},reference,[1,0,0,0,1,0],render_sha)
    locations=[region.get('original_geometry',{}).get('pdf_geometry',{}) for region in metadata['regions']]
    passed=(parsed['stats'].get('ocr_succeeded_pages')==[1]
        and evaluation['mapped_region_count']==len(metadata['regions'])
        and metadata.get('pdf_coordinate_mapping',{}).get('status')=='mapped'
        and all(location.get('source_pdf_sha256')==pdf_sha and location.get('page_no')==1
            and location.get('pdf_highlight_eligible') is True for location in locations))
    stable=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
    report=ROOT/'runtime'/f'camera-pdf-prose-{uuid.uuid4().hex}.json'
    report.write_text(json.dumps({'not_official_benchmark':True,
        'input_kind':'actual_camera_image_embedded_in_generated_scan_only_pdf_not_original_public_pdf',
        'transport':'actual_http_api' if args.http else 'local_production_parser',
        'source_code_sha256':hashes,'source_stable':stable,
        'source_image_sha256':reference['sha256'],'pdf_sha256':pdf_sha,'render_sha256':render_sha,
        'elapsed_seconds':time.monotonic()-start,'chain_passed':passed,'evaluation':evaluation,
        'stats':parsed['stats'],'parsed':parsed},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'chain_passed':passed,'evaluation':{k:v for k,v in evaluation.items() if k not in
        {'blocks','prose_in_observed_reading_order'}},'pdf_coordinate_mapping':metadata.get('pdf_coordinate_mapping'),
        'report':str(report)}),flush=True)
    return 0 if passed and stable else 1


if __name__=='__main__':raise SystemExit(main())
