"""Real OCR on a controlled borderless image; not a public benchmark."""
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
    image=Image.new('RGB',(1200,700),'white')
    draw=ImageDraw.Draw(image)
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',40)
    expected=['1','123456','123456789','42']
    for row,(label,number) in enumerate(zip(['Personnel','Supplies','Equipment','Total'],expected)):
        y=90+row*130
        draw.text((80,y),label,font=font,fill='black')
        draw.text((1080-draw.textlength(number,font=font),y),number,font=font,fill='black')
    stream=BytesIO();image.save(stream,format='PNG');raw=stream.getvalue()
    result=OcrPipeline(RapidOcrExecutor()).run(raw,language='eng').to_dict()
    layout=result['metadata'].get('table_layout',{})
    rows=layout.get('rows',[])
    passed=layout.get('status')=='candidate' and len(rows)==4 and [row[-1]['text'] for row in rows]==expected
    report={'fixture_kind':'controlled_borderless_printed_image_real_rapidocr',
        'not_public_benchmark':True,'source_sha256':hashlib.sha256(raw).hexdigest(),
        'passed':passed,'expected_numeric_observations':expected,'ocr':result}
    (ROOT/'runtime/borderless-scan-real-ocr-20261005.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':passed,'status':layout.get('status'),'alignment_method':layout.get('alignment_method'),
        'texts':[[cell['text'] for cell in row] for row in rows]},ensure_ascii=False))
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
