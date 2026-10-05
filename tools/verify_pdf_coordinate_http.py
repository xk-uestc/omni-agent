"""Scan-only PDF upload through the running API, with original-page polygons."""
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
from urllib.request import Request,urlopen

import fitz
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]


def main():
    source=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')
    raw=source.read_bytes()
    with fitz.open(stream=raw,filetype='pdf') as original:
        png=original[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
    records=[]
    for angle in (90,5):
        with Image.open(BytesIO(png)) as image:
            rotated=image.rotate(angle,expand=True,fillcolor='white')
            out=BytesIO()
            rotated.save(out,format='PNG')
        with fitz.open() as document:
            page=document.new_page(width=rotated.width/2,height=rotated.height/2)
            page.insert_image(page.rect,stream=out.getvalue())
            pdf=document.tobytes()
        pdf_hash=hashlib.sha256(pdf).hexdigest()
        request=Request('http://127.0.0.1:8030/api/v1/documents/chunks-preview',
            data=json.dumps({'document_id':f'coordinate-chain-public-scan-{angle}', 'modality':'pdf',
                'language':'chi_sim+eng','file_base64':base64.b64encode(pdf).decode()}).encode(),
            headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:result=json.load(response)
        evidence=[chunk.get('metadata',{}).get('ocr_metadata',{}) for chunk in result['chunks']]
        metadata=next((item for item in evidence if item.get('pdf_coordinate_mapping',{}).get('status')=='mapped'),{})
        mapping=metadata.get('pdf_coordinate_mapping',{})
        table_evidence=metadata.get('scanned_table_evidence',metadata)
        tables=table_evidence.get('scanned_grids',{}).get('tables',[])
        table_mapping=table_evidence.get('pdf_coordinate_mapping',{})
        eligible_cells=sum(bool(cell.get('original_geometry',{}).get('pdf_geometry',{}).get('pdf_highlight_eligible'))
            for table in tables for row in table['cells'] for cell in row)
        passed=(result['stats'].get('ocr_succeeded_pages')==[1] and mapping.get('source_pdf_sha256')==pdf_hash
            and mapping.get('page_no')==1 and mapping.get('eligible_region_count',0)>0
            and table_mapping.get('source_pdf_sha256')==pdf_hash and eligible_cells==22)
        record={'rotation_ccw':angle,'passed':passed,'source_public_pdf_sha256':hashlib.sha256(raw).hexdigest(),
            'derived_scan_pdf_sha256':pdf_hash,'stats':result['stats'],'mapping':mapping,
            'eligible_grid_cells':eligible_cells,'result':result}
        record['independent_table_mapping']=table_mapping
        records.append(record)
        print(json.dumps({k:v for k,v in record.items() if k!='result'},ensure_ascii=False),flush=True)
    (ROOT/'runtime/pdf-coordinate-http-20261004.json').write_text(json.dumps(
        {'not_official_benchmark':True,'passed':all(item['passed'] for item in records),'cases':records},
        ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(item['passed'] for item in records) else 1


if __name__=='__main__':raise SystemExit(main())
