"""Observed arithmetic on actual public OCR, with explicit controlled conflicts."""
import base64,hashlib,json,sys,uuid
from pathlib import Path
from copy import deepcopy
from urllib.request import Request,urlopen
import fitz
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.table_arithmetic import TableArithmeticAgent
SOURCE=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')


def main():
    original=SOURCE.read_bytes();records=[];report=ROOT/'runtime'/f'table-arithmetic-http-{uuid.uuid4().hex}.json'
    names=['backend/table_arithmetic.py','backend/scanned_table_structure.py','backend/pdf_amount_verification.py',
        'backend/pdf_table_preview.py','backend/pdf_table_continuity.py','frontend/knowledge.js']
    hashes={name:hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest() for name in names}
    def call(path,payload):
        request=Request('http://127.0.0.1:8030/api/v1/documents/'+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:return json.load(response)
    def check(table):
        result=table.get('arithmetic_check',{});checks=result.get('checks',[])
        return (result.get('status')=='arithmetic_consistent' and len(checks)==1 and checks[0]['computed_total']=='100000'
            and checks[0]['observed_total']=='100000' and checks[0]['difference']=='0' and checks[0]['detail_count']==10
            and result.get('cell_values_verified') is False and result.get('table_completeness_verified') is False)
    def record(name,result,passed,mode):
        records.append({'name':name,'passed':bool(passed),'mode':mode,'result':result})
        print(json.dumps({'name':name,'passed':bool(passed)}),flush=True)
    try:
        result=call('chunks-preview',{'document_id':'arithmetic-'+uuid.uuid4().hex,'modality':'pdf','language':'eng',
            'file_base64':base64.b64encode(original).decode(),'pdf_table_headers':[{'page_no':1,'table_index':0,'header_rows':0}]})
        table=result['table_structures'][0];record('public_native_pdf_observed_total',result,check(table),'real_http_original')
        with fitz.open(stream=original,filetype='pdf') as document:
            image=document[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
        result=call('ocr',{'image_base64':base64.b64encode(image).decode(),'language':'eng','max_attempts':3,'table_header_rows':0,'table_index':0})
        structure=result.get('metadata',{}).get('table_structure',{})
        arithmetic=structure.get('arithmetic_check',{})
        problem=arithmetic.get('checks',[{}])[0]
        candidate=problem.get('review_candidate',{})
        safe_refusal=(arithmetic.get('status')=='unavailable' and problem.get('reason')=='amount_missing_or_ambiguous'
            and candidate.get('candidate_amount')=='2000' and candidate.get('confirmed') is False
            and candidate.get('cell',{}).get('text')=='$2.000')
        record('controlled_public_raster_image_total_or_explicit_ambiguity',result,check(structure) or safe_refusal,'real_http_derived_image')
        damaged=deepcopy(table);damaged['body_cells'][0][1]['text']='$46,000'
        report_conflict=TableArithmeticAgent().run(damaged)
        record('controlled_wrong_detail_amount',report_conflict,report_conflict['status']=='conflict'
            and report_conflict['checks'][0]['difference']=='1000','direct_agent_actual_http_table_controlled_mutation')
        damaged=deepcopy(table);damaged['body_cells'][0][1]['text']=''
        unavailable=TableArithmeticAgent().run(damaged)
        record('controlled_blank_never_zero',unavailable,unavailable['status']=='unavailable'
            and unavailable['checks'][0]['reason']=='amount_missing_or_ambiguous','direct_agent_actual_http_table_controlled_mutation')
    finally:
        stable=all(hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
        report.write_text(json.dumps({'not_official_benchmark':True,'source_hashes':hashes,'source_stable':stable,
            'public_original_sha256':hashlib.sha256(original).hexdigest(),'cases':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(report),flush=True)
    return 0 if stable and len(records)==4 and all(row['passed'] for row in records) else 1


if __name__=='__main__':raise SystemExit(main())
