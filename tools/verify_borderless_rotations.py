"""Controlled borderless table variants; actual OCR and original-coordinate gates."""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import sys
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.ocr import RapidOcrExecutor,OcrPipeline


def main():
    original=Image.new('RGB',(1200,700),'white')
    draw=ImageDraw.Draw(original);font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',40)
    expected=['1','123456','123456789','42']
    for row,(label,value) in enumerate(zip(['Personnel','Supplies','Equipment','Total'],expected)):
        y=90+row*130
        draw.text((80,y),label,font=font,fill='black')
        draw.text((1080-draw.textlength(value,font=font),y),value,font=font,fill='black')
    pipeline=OcrPipeline(RapidOcrExecutor());cases=[]
    for angle in (0,90,5):
        image=original.rotate(angle,expand=True,fillcolor='white')
        buffer=BytesIO();image.save(buffer,format='PNG');raw=buffer.getvalue()
        result=pipeline.run(raw,language='eng',max_attempts=3).to_dict()
        table_evidence=result['metadata'].get('borderless_table_evidence',{})
        layout=table_evidence.get('table_layout',result['metadata'].get('table_layout',{}))
        rows=layout.get('rows',[])
        values=[row[-1]['text'] for row in rows if row]
        eligible=all(cell.get('original_geometry',{}).get('original_bbox_eligible') for row in rows for cell in row)
        recovered=[cell for row in rows for cell in row if cell.get('observation_origin')=='borderless_cell_crop_ocr']
        crop_eligible=all(cell.get('original_crop_geometry',{}).get('original_bbox_eligible') for cell in recovered)
        passed=layout.get('status')=='candidate' and len(rows)==4 and values==expected and eligible and crop_eligible
        case={'rotation_ccw_degrees':angle,'source_sha256':hashlib.sha256(raw).hexdigest(),
            'passed':passed,'texts':[[cell['text'] for cell in row] for row in rows],
            'text_polygons_eligible':eligible,'crop_polygons_eligible':crop_eligible,
            'recovered_cell_count':len(recovered),'ocr':result}
        cases.append(case)
        (ROOT/'runtime/borderless-rotations-20261005.json').write_text(json.dumps(
            {'fixture_kind':'controlled_printed_image_real_ocr','not_public_benchmark':True,'cases':cases},
            ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({key:value for key,value in case.items() if key!='ocr'},ensure_ascii=False),flush=True)
    return 0 if all(case['passed'] for case in cases) else 1


if __name__=='__main__':raise SystemExit(main())
