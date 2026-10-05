"""Offline original-source crop experiments; does not change production OCR."""
import hashlib
import json
import math
import sys
import uuid
from io import BytesIO
from pathlib import Path
import cv2
import fitz
import numpy as np
from PIL import Image, ImageOps, ImageEnhance

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.ocr import RapidOcrExecutor
from backend.cell_crop_quality import suppress_disconnected_top_edge
SOURCE=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')


def main():
    evidence=json.loads((ROOT/'runtime/amount-cell-review-http-2eacf972ef224d7182b45a0ff7af5e5e.json').read_text(encoding='utf-8'))
    case=next(item for item in evidence['cases'] if item['name']=='controlled_rotate_5')
    with fitz.open(SOURCE) as document:
        raw=document[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
    with Image.open(BytesIO(raw)) as original:
        source=original.rotate(5,expand=True,fillcolor='white').convert('RGB')
    stream=BytesIO();source.save(stream,format='PNG')
    assert hashlib.sha256(stream.getvalue()).hexdigest()==case['source_sha256']
    directory=ROOT/'runtime'/('cell-grid-diagnostic-'+uuid.uuid4().hex)
    directory.mkdir()
    executor=RapidOcrExecutor()
    records=[]
    cells=case['result']['metadata']['table_structure']['body_cells']
    for row,column in [(5,1),(9,1)]:
        cell=next(cell for cells_row in cells for cell in cells_row if cell['row']==row and cell['column']==column)
        points=cell['original_geometry']['polygon_px']
        width=round((math.dist(points[0],points[1])+math.dist(points[2],points[3]))/2)
        height=round((math.dist(points[0],points[3])+math.dist(points[1],points[2]))/2)
        quad=tuple(v for p in (points[0],points[3],points[2],points[1]) for v in p)
        crop=source.transform((width,height),Image.Transform.QUAD,quad,Image.Resampling.BICUBIC)
        crop=crop.crop((2,2,width-2,height-2))
        gray=np.array(ImageOps.grayscale(crop));binary=(gray<200).astype('uint8')*255
        mask=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((1,max(20,round(crop.width*.6))),np.uint8))
        mask=cv2.dilate(mask,np.ones((3,1),np.uint8))
        cleaned=np.array(crop);cleaned[mask>0]=255
        alternatives=[('original',crop),('long_horizontal_removed',Image.fromarray(cleaned)),
                      ('validated_edge_suppression',suppress_disconnected_top_edge(crop)[0]),
                      ('top_edge1_removed',crop.crop((0,1,crop.width,crop.height))),
                      ('top_edge2_removed',crop.crop((0,2,crop.width,crop.height)))]
        for name,image in alternatives:
            ink=ImageOps.grayscale(image).point(lambda value:255 if value<230 else 0).getbbox()
            if not ink:continue
            content=(max(0,ink[0]-3),max(0,ink[1]-3),min(image.width,ink[2]+3),min(image.height,ink[3]+3))
            image=image.crop(content)
            for variant,output in [('rgb',image),('upscale2',image.resize((image.width*2,image.height*2),Image.Resampling.LANCZOS)),
                                   ('contrast',ImageEnhance.Contrast(ImageOps.grayscale(image)).enhance(1.8))]:
                path=directory/f'row{row}-{name}-{variant}.png';output.save(path)
                observations=executor.recognize_line(path.read_bytes())
                record={'row':row,'original_text':cell['text'],'preprocessing':name+'-'+variant,
                        'removed_pixels':suppress_disconnected_top_edge(crop)[1]['removed_pixels'] if name=='validated_edge_suppression' else int((mask>0).sum()) if name=='long_horizontal_removed' else 0,
                        'content_bbox':content,'path':str(path),'observations':observations}
                records.append(record);print(json.dumps(record,ensure_ascii=False),flush=True)
    (directory/'report.json').write_text(json.dumps({'not_official_benchmark':True,'source_sha256':case['source_sha256'],
        'public_pdf_sha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'observations':records},ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(directory),flush=True)


if __name__=='__main__':main()
