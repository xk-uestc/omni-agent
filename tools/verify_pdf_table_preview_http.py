"""Live explicit PDF table selection with source/page/cell coordinate checks."""
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import uuid
from urllib.request import Request,urlopen
import fitz
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.chunk_cleaning import DocumentChunker


def send(raw,selections,docid):
    request=Request('http://127.0.0.1:8030/api/v1/documents/chunks-preview',data=json.dumps({
        'document_id':docid,'modality':'pdf','file_base64':base64.b64encode(raw).decode(),'language':'eng',
        'pdf_table_headers':selections}).encode(),headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=300) as response:return json.load(response)


def check_structures(result,raw,selections):
    structures=result.get('table_structures',[])
    if len(structures)!=len(selections):return False
    sha=hashlib.sha256(raw).hexdigest()
    for selection,structure in zip(selections,structures):
        if (structure.get('status')!='structure_observed' or structure.get('row_count')!=11
                or len(structure.get('body_cells',[]))!=11 or structure.get('column_count')!=2
                or structure.get('source_pdf_sha256')!=sha or structure.get('page_no')!=selection['page_no']
                or structure.get('cell_values_verified') is not False or structure.get('native_text_replaced') is not False):return False
        if structure['body_cells'][0][0]['text']!='Personnel' or structure['body_cells'][0][1]['text']!='$47,000':return False
        for row in structure['body_cells']:
            for cell in row:
                location=cell.get('original_geometry',{}).get('pdf_geometry',{})
                if (not location.get('pdf_highlight_eligible') or location.get('source_pdf_sha256')!=sha
                        or location.get('page_no')!=selection['page_no']):return False
    return True


def main():
    source=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')
    original=source.read_bytes()
    names=['backend/pdf_table_preview.py','backend/scanned_table_structure.py','backend/app.py','backend/chunk_cleaning.py',
           'backend/ocr.py','backend/image_geometry.py','backend/scanned_grid_selection.py','frontend/knowledge.js']
    hashes={name:hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest() for name in names}
    records=[];report=ROOT/'runtime'/f'pdf-table-preview-http-{uuid.uuid4().hex}.json'
    with fitz.open(stream=original,filetype='pdf') as document:
        png=document[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
        with fitz.open() as doubled:
            doubled.insert_pdf(document,from_page=0,to_page=0);doubled.insert_pdf(document,from_page=0,to_page=0)
            duplicate=doubled.tobytes()
    with Image.open(BytesIO(png)) as image:rotated=image.rotate(90,expand=True,fillcolor='white')
    stream=BytesIO();rotated.save(stream,format='PNG')
    with fitz.open() as scan:
        page=scan.new_page(width=rotated.width/2,height=rotated.height/2);page.insert_image(page.rect,stream=stream.getvalue())
        scan_raw=scan.tobytes()
    selection=lambda page:{'page_no':page,'table_index':0,'header_rows':0}
    samples=[('public_original_native',original,[selection(1)]),
             ('derived_duplicate_native_pages',duplicate,[selection(1),selection(2)]),
             ('derived_rotated_scan_without_text_layer',scan_raw,[selection(1)])]
    try:
        for name,raw,selections in samples:
            docid='pdf-table-'+name+'-'+uuid.uuid4().hex
            expected=DocumentChunker().parse_pdf(raw,document_id=docid).to_dict()['chunks'] if 'scan' not in name else None
            result=send(raw,selections,docid)
            passed=check_structures(result,raw,selections)
            native_preserved=result['chunks']==expected if expected is not None else None
            if expected is not None:passed=passed and native_preserved
            if 'scan' in name:passed=passed and result['table_structures'][0]['ocr_origin']=='reused_page_ocr'
            records.append({'name':name,'passed':passed,'source_pdf_sha256':hashlib.sha256(raw).hexdigest(),
                'native_chunks_equal_baseline':native_preserved,'selections':selections,'result':result})
            print(json.dumps({'name':name,'passed':passed,'native_preserved':native_preserved},ensure_ascii=False),flush=True)
    finally:
        stable=all(hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
        report.write_text(json.dumps({'not_official_benchmark':True,'public_source_sha256':hashlib.sha256(original).hexdigest(),
            'source_hashes':hashes,'source_stable':stable,'samples':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(report),flush=True)
    return 0 if stable and len(records)==3 and all(record['passed'] for record in records) else 1


if __name__=='__main__':raise SystemExit(main())
