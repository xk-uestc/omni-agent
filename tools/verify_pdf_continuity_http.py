"""Actual OCR assembly of controlled public-page splits, plus original refusal."""
import base64,hashlib,json,sys,uuid
from pathlib import Path
from io import BytesIO
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import fitz
from PIL import Image,ImageOps

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')


def split_scan(raw,rotate_second=False):
    with fitz.open(stream=raw,filetype='pdf') as original,fitz.open() as result:
        for index,rect in enumerate([(55,112,545,205.9),(55,205.0,545,317)]):
            png=original[0].get_pixmap(matrix=fitz.Matrix(3,3),clip=fitz.Rect(rect),alpha=False).tobytes('png')
            with Image.open(BytesIO(png)) as image:
                image=ImageOps.expand(image,border=16,fill='white')
                if index==1 and rotate_second:image=image.rotate(90,expand=True,fillcolor='white')
                stream=BytesIO();image.save(stream,format='PNG')
                page=result.new_page(width=image.width/2,height=image.height/2)
                page.insert_image(page.rect,stream=stream.getvalue())
        return result.tobytes()


def main():
    raw=SOURCE.read_bytes();records=[]
    names=['backend/pdf_table_continuity.py','backend/pdf_table_preview.py','backend/scanned_table_layout.py','backend/app.py','frontend/knowledge.js','frontend/pdf-table-continuity.js']
    hashes={name:hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest() for name in names}
    report=ROOT/'runtime'/f'pdf-continuity-http-{uuid.uuid4().hex}.json'
    samples=[('public_original_page1_table_page2_prose',raw,False),
        ('controlled_split_public_table',split_scan(raw),True),
        ('controlled_split_public_table_second_page_rotated90',split_scan(raw,True),True)]
    def check(result,pdf_bytes,accepted):
        assemblies=result.get('table_continuity',{}).get('assemblies',[])
        if len(assemblies)!=1:return False
        joined=assemblies[0]
        if not accepted:return joined.get('status')=='rejected' and joined.get('rows')==[]
        sha=hashlib.sha256(pdf_bytes).hexdigest()
        if joined.get('status')!='assembled_preview' or joined.get('row_count')!=11 or joined.get('column_count')!=2:return False
        if joined.get('source_pdf_sha256')!=sha or joined.get('cell_values_verified') is not False:return False
        rows=joined['rows']
        if [row['page_no'] for row in rows]!=[1]*5+[2]*6:return False
        if rows[0]['cells'][0]['text']!='Personnel' or rows[0]['cells'][1]['text']!='$47,000':return False
        if rows[-1]['cells'][0]['text']!='TOTAL:' or rows[-1]['cells'][1]['text']!='$100,000':return False
        for row in rows:
            for cell in row['cells']:
                mapping=cell.get('original_geometry',{}).get('pdf_geometry',{})
                if not mapping.get('pdf_highlight_eligible') or mapping.get('source_pdf_sha256')!=sha or mapping.get('page_no')!=row['page_no']:return False
        original=[row for table in result['table_structures'] for row in table['body_cells']]
        return [row['cells'] for row in rows]==original
    try:
        for name,pdf_bytes,accepted in samples:
            payload={'document_id':'continuity-'+uuid.uuid4().hex,'modality':'pdf','language':'eng',
                'file_base64':base64.b64encode(pdf_bytes).decode(),
                'pdf_table_headers':[{'page_no':p,'table_index':0,'header_rows':0} for p in (1,2)],
                'pdf_table_links':[{'from_page':1,'from_table':0,'to_page':2,'to_table':0}]}
            request=Request('http://127.0.0.1:8030/api/v1/documents/chunks-preview',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
            try:
                with urlopen(request,timeout=300) as response:result=json.load(response)
            except HTTPError as error:
                records.append({'name':name,'is_derived':accepted,'passed':False,'http_status':error.code,
                    'response_preview':error.read(200).decode('utf-8',errors='replace')})
                print(json.dumps({'name':name,'passed':False,'http_status':error.code}),flush=True)
                continue
            passed=check(result,pdf_bytes,accepted)
            records.append({'name':name,'is_derived':accepted,'passed':passed,'source_pdf_sha256':hashlib.sha256(pdf_bytes).hexdigest(),
                'full_numeric_accuracy_tested':False,'result':result})
            print(json.dumps({'name':name,'passed':passed,'statuses':[s['status'] for s in result.get('table_structures',[])]}),flush=True)
    finally:
        stable=all(hashlib.sha256((ROOT/'ict-track8'/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
        report.write_text(json.dumps({'not_official_benchmark':True,'original_public_sha256':hashlib.sha256(raw).hexdigest(),
            'source_hashes':hashes,'source_stable':stable,'samples':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(report),flush=True)
    return 0 if stable and len(records)==3 and all(item['passed'] for item in records) else 1


if __name__=='__main__':raise SystemExit(main())
