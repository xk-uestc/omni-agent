"""Real local crop OCR on public raster variants; source observations stay intact."""
import base64,hashlib,json,sys,uuid
from io import BytesIO
from pathlib import Path
from urllib.request import Request,urlopen
import fitz
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')


def main():
    original=SOURCE.read_bytes();records=[];report=ROOT/'runtime'/f'amount-cell-review-http-{uuid.uuid4().hex}.json'
    names=['backend/amount_cell_review.py','backend/ocr.py','backend/app.py','backend/pdf_table_preview.py','frontend/knowledge.js']
    hashes={name:hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest() for name in names}
    with fitz.open(stream=original,filetype='pdf') as document:
        png=document[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
    samples=[('public_page_raster',png)]
    for angle in (90,5):
        with Image.open(BytesIO(png)) as source:
            image=source.rotate(angle,expand=True,fillcolor='white');stream=BytesIO();image.save(stream,format='PNG')
            samples.append((f'controlled_rotate_{angle}',stream.getvalue()))
    try:
        for name,raw in samples:
            payload={'image_base64':base64.b64encode(raw).decode(),'language':'eng','max_attempts':3,'table_header_rows':0,'table_index':0}
            request=Request('http://127.0.0.1:8030/api/v1/documents/ocr',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
            with urlopen(request,timeout=300) as response:result=json.load(response)
            structure=result.get('metadata',{}).get('table_structure',{});review=structure.get('amount_cell_review',{})
            previews=review.get('revised_preview',{}).get('body_cells',structure.get('body_cells',[]))
            targets=[row[1] for row in previews if len(row)==2 and 'officesupplies' in row[0].get('text','').replace(' ','').casefold()]
            valid=(structure.get('row_count')==11 and len(targets)==1 and targets[0].get('text')=='$2,000'
                and review.get('ocr_values_replaced') is False)
            if review.get('accepted_count',0):
                valid=valid and review.get('revised_preview',{}).get('arithmetic_check',{}).get('status')=='arithmetic_consistent'
                for item in review['reviews']:
                    if item['accepted']:
                        original_cell=next(cell for row in structure['body_cells'] for cell in row if (cell['row'],cell['column'])==(item['row'],item['column']))
                        valid=valid and original_cell['text']==item['original_text'] and item['source_sha256']==hashlib.sha256(raw).hexdigest()
                        valid=valid and len(item['attempts'])>=2 and all(attempt.get('status')=='observed' for attempt in item['attempts'])
            else:valid=valid and structure.get('arithmetic_check',{}).get('status')=='arithmetic_consistent'
            records.append({'name':name,'derived_from_single_public_page':True,'passed':bool(valid),
                'source_sha256':hashlib.sha256(raw).hexdigest(),'crop_review_status':review.get('status'),
                'accepted_candidate_count':review.get('accepted_count',0),'result':result})
            print(json.dumps({k:v for k,v in records[-1].items() if k!='result'}),flush=True)
    finally:
        stable=all(hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
        report.write_text(json.dumps({'not_official_benchmark':True,'source_hashes':hashes,'source_stable':stable,
            'public_pdf_sha256':hashlib.sha256(original).hexdigest(),'cases':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(report),flush=True)
    return 0 if stable and len(records)==3 and all(row['passed'] for row in records) else 1


if __name__=='__main__':raise SystemExit(main())
