"""Live OCR header interpretation on a public page and rotated controlled grid."""
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import uuid
from urllib.request import Request,urlopen
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]


def fixture():
    image=Image.new('RGB',(1200,650),'white');draw=ImageDraw.Draw(image)
    for x in (100,400,1100):draw.line((x,100,x,500),fill='black',width=4)
    draw.line((750,200,750,500),fill='black',width=4)
    for y in (100,300,400,500):draw.line((100,y,1100,y),fill='black',width=4)
    draw.line((400,200,1100,200),fill='black',width=4)
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',34)
    for x,y,text in [(150,180,'Region'),(680,130,'Sales'),(520,230,'2024'),(870,230,'2025'),
                     (170,330,'East'),(550,330,'10'),(900,330,'20'),(170,430,'West'),(550,430,'30'),(900,430,'40')]:
        draw.text((x,y),text,font=font,fill='black')
    return image


def main():
    paths=[ROOT/'ict-track8/backend'/name for name in ['app.py','scanned_table_structure.py','scanned_grid_selection.py','ocr.py','scanned_table_layout.py']]
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    records=[];report=ROOT/'runtime'/f'scanned-structure-http-{uuid.uuid4().hex}.json'
    samples=[('public_headerless', (ROOT/'runtime/scanned-structure-public-page.png').read_bytes(),0)]
    for angle in (0,90):
        output=BytesIO();fixture().rotate(angle,expand=True,fillcolor='white').save(output,format='PNG')
        samples.append((f'controlled_multiheader_rotation{angle}',output.getvalue(),2))
    try:
        for name,raw,count in samples:
            request=Request('http://127.0.0.1:8030/api/v1/documents/ocr',
                data=json.dumps({'image_base64':base64.b64encode(raw).decode(),'language':'eng','table_header_rows':count}).encode(),
                headers={'Content-Type':'application/json'})
            with urlopen(request,timeout=300) as response:result=json.load(response)
            structure=result['metadata'].get('table_structure',{})
            passed=(structure.get('status')=='structure_observed' and structure.get('cell_values_verified') is False
                    and structure.get('ready_for_sql_import') is False)
            if name=='public_headerless':
                passed=passed and structure.get('row_count')==11 and len(structure.get('body_cells',[]))==11
                passed=passed and structure['body_cells'][0][0]['text']=='Personnel' and structure['body_cells'][0][1]['text']=='$47,000'
                passed=passed and all(column['header_path']==[] for column in structure.get('columns',[]))
            else:
                passed=passed and [c['header_path'] for c in structure.get('columns',[])]==[['Region'],['Sales','2024'],['Sales','2025']]
                passed=passed and [[c['text'] for c in row] for row in structure.get('body_cells',[])]==[['East','10','20'],['West','30','40']]
            records.append({'name':name,'passed':passed,'source_image_sha256':hashlib.sha256(raw).hexdigest(),'ocr':result})
            print(json.dumps({'name':name,'passed':passed,'status':structure.get('status')},ensure_ascii=False),flush=True)
    finally:
        stable=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
        report.write_text(json.dumps({'not_official_benchmark':True,'public_page_semantic_accuracy_not_scored':True,
            'source_hashes':hashes,'source_stable':stable,'samples':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(report),flush=True)
    return 0 if stable and len(records)==3 and all(record['passed'] for record in records) else 1


if __name__=='__main__':raise SystemExit(main())
