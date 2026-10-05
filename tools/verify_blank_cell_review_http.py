"""Check unresolved public skewed-page blanks honestly, preserving original OCR."""
import base64,hashlib,json,uuid
from pathlib import Path
from io import BytesIO
from urllib.request import Request,urlopen
import fitz
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]


def main():
    source=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')
    with fitz.open(source) as document:
        png=document[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
    with Image.open(BytesIO(png)) as image:
        output=BytesIO();image.rotate(5,expand=True,fillcolor='white').save(output,format='PNG');raw=output.getvalue()
    names=['backend/blank_cell_review.py','backend/amount_cell_review.py','backend/app.py','frontend/knowledge.js']
    hashes={name:hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest() for name in names}
    payload={'image_base64':base64.b64encode(raw).decode(),'language':'eng','max_attempts':3,'table_header_rows':0,'table_index':0}
    request=Request('http://127.0.0.1:8030/api/v1/documents/ocr',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=300) as response:result=json.load(response)
    structure=result.get('metadata',{}).get('table_structure',{});review=structure.get('blank_cell_review',{})
    cells=[cell for row in structure.get('body_cells',[]) for cell in row];identities={(cell['row'],cell['column']):cell for cell in cells}
    checks=[]
    for item in review.get('reviews',[]):
        check=identities[(item['row'],item['column'])]['text']==''
        if item.get('accepted'):
            check=check and item['source_sha256']==hashlib.sha256(raw).hexdigest() and all(attempt.get('status')=='observed' for attempt in item['attempts'])
        checks.append(check)
    stable=all(hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
    passed=structure.get('row_count')==11 and len(checks)==2 and all(checks) and review.get('original_observations_replaced') is False
    report=ROOT/'runtime'/f'blank-cell-review-http-{uuid.uuid4().hex}.json'
    report.write_text(json.dumps({'not_official_benchmark':True,'behavior_verification_passed':passed,'full_table_success':False,
        'single_public_page_controlled_rotation_degrees':5,'source_hashes':hashes,'source_stable':stable,'result':result},ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'behavior_verification_passed':passed,'full_table_success':False,'accepted_candidates':review.get('accepted_count'),
        'arithmetic_status':structure.get('arithmetic_check',{}).get('status'),'source_stable':stable}));print(report)
    return 0 if passed and stable else 1


if __name__=='__main__':raise SystemExit(main())
