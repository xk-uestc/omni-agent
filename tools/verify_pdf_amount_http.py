"""Actual OCR/source-text amount checks; controlled corruptions separately labelled."""
import base64,hashlib,json,sys,uuid
from pathlib import Path
from copy import deepcopy
from urllib.request import Request,urlopen
import fitz
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.pdf_amount_verification import PdfAmountVerificationAgent
SOURCE=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')


def main():
    original=SOURCE.read_bytes();records=[]
    names=['backend/pdf_amount_verification.py','backend/pdf_table_preview.py','backend/scanned_table_layout.py',
        'frontend/knowledge.js','frontend/pdf-amount-check.js','frontend/knowledge.html']
    hashes={name:hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest() for name in names}
    report=ROOT/'runtime'/f'pdf-amount-http-{uuid.uuid4().hex}.json'
    def post(raw):
        payload={'document_id':'pdf-amount-'+uuid.uuid4().hex,'modality':'pdf','language':'eng',
            'file_base64':base64.b64encode(raw).decode(),'pdf_table_headers':[{'page_no':1,'table_index':0,'header_rows':0}]}
        request=Request('http://127.0.0.1:8030/api/v1/documents/chunks-preview',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:return json.load(response)
    def record(name,result,passed,mode):
        records.append({'name':name,'passed':bool(passed),'mode':mode,'result':result})
        print(json.dumps({'name':name,'passed':bool(passed)},ensure_ascii=False),flush=True)
    try:
        native=post(original);table=native['table_structures'][0]
        checks=[row[1].get('amount_check',{}) for row in table.get('body_cells',[])]
        passed=table.get('row_count')==11 and len(checks)==11 and all(check.get('status')=='matched' for check in checks)
        passed=passed and table.get('cell_values_verified') is False and all(check.get('native_words') for check in checks)
        record('public_original_native_amount_agreement',native,passed,'real_http_original')
        altered=deepcopy(table);altered['body_cells'][0][1]['text']='$47'
        altered['body_cells'][2][1]['text']='$2.000'
        compared=PdfAmountVerificationAgent().run(original,[altered])[0]
        record('controlled_ocr_value_and_punctuation_corruption',compared,
            compared['body_cells'][0][1]['amount_check']['reason']=='amount_value_mismatch'
            and compared['body_cells'][2][1]['amount_check']['status']=='needs_review'
            and compared['body_cells'][2][1]['amount_check']['native_text']=='$2,000'
            and compared['body_cells'][2][1]['text']=='$2.000','direct_agent_on_actual_http_geometry_controlled_ocr_edits')
        with fitz.open(stream=original,filetype='pdf') as document,fitz.open() as scan:
            page=document[0];png=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
            created=scan.new_page(width=page.rect.width,height=page.rect.height);created.insert_image(created.rect,stream=png)
            raw=scan.tobytes()
        result=post(raw);table=result['table_structures'][0]
        checks=[row[1].get('amount_check',{}) for row in table.get('body_cells',[])]
        record('controlled_public_scan_has_no_independent_native_reference',result,
            table.get('row_count')==11 and len(checks)==11 and all(check.get('status')=='unavailable'
                and check.get('reason')=='native_reference_unavailable' for check in checks),'real_http_derived_scan')
    finally:
        stable=all(hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
        report.write_text(json.dumps({'not_official_benchmark':True,'source_hashes':hashes,'source_stable':stable,
            'public_original_sha256':hashlib.sha256(original).hexdigest(),'cases':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(report),flush=True)
    return 0 if stable and len(records)==3 and all(record['passed'] for record in records) else 1


if __name__=='__main__':raise SystemExit(main())
