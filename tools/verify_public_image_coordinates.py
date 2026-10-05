"""Real public-page OCR and source polygon receipts; not a contest benchmark."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys
import argparse

import fitz
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.ocr import OcrPipeline,RapidOcrExecutor
from backend.image_geometry import point


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',choices=['all','finance','budget'],default='all')
    parser.add_argument('--variants',nargs='+',choices=['upright','rotated90','skew5'],default=['upright','rotated90','skew5'])
    parser.add_argument('--report',default='image-coordinate-public-20261004.json')
    args=parser.parse_args()
    if Path(args.report).name!=args.report:raise ValueError('report must be a filename')
    pipeline=OcrPipeline(RapidOcrExecutor())
    records=[]
    sources=[('finance',Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\finance\DUDE_073c5e237cfc190effc8ff2a3b0f7fe5.pdf')),
             ('budget',Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf'))]
    for name,path in sources:
        if args.source!='all' and name!=args.source:continue
        raw=path.read_bytes()
        with fitz.open(stream=raw,filetype='pdf') as pdf:
            rendered=pdf[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
        with Image.open(BytesIO(rendered)) as image:base=image.convert('RGB')
        for variant,angle in [('upright',0),('rotated90',90),('skew5',5)]:
            if variant not in args.variants:continue
            source=base.rotate(angle,expand=True,fillcolor='white')
            buffer=BytesIO()
            source.save(buffer,format='PNG')
            image_bytes=buffer.getvalue()
            result=pipeline.run(image_bytes,language='chi_sim+eng').to_dict()
            metadata=result['metadata']
            mapping=metadata.get('original_pixel_mapping',{})
            geometry=metadata.get('image_geometry',{})
            regions=metadata.get('regions',[])
            maximum_error=0.
            eligible=0
            for region in regions:
                mapped=region.get('original_geometry',{})
                if mapped.get('original_bbox_eligible'):
                    eligible+=1
                    for pixel in mapped['polygon_px']:
                        output=point(geometry['source_to_output'],pixel)
                        back=point(geometry['output_to_source'],output)
                        maximum_error=max(maximum_error,*(abs(x-y) for x,y in zip(pixel,back)))
            passed=(result['status'] in {'ok','low_confidence'} and bool(result['text'])
                    and mapping.get('status')=='mapped' and eligible>0 and maximum_error<1e-6
                    and geometry['source']['sha256']==hashlib.sha256(image_bytes).hexdigest())
            overlay=source.copy()
            draw=ImageDraw.Draw(overlay)
            for index,region in enumerate(regions[:80]):
                mapped=region.get('original_geometry',{})
                if mapped.get('original_bbox_eligible'):
                    points=[tuple(p) for p in mapped['polygon_px']]
                    draw.line(points+[points[0]],fill='#d43a30',width=2)
                    draw.text(points[0],str(index),fill='#d43a30')
            overlay.thumbnail((1500,1500))
            overlay_path=ROOT/f'runtime/{Path(args.report).stem}-{name}-{variant}.png'
            overlay.save(overlay_path)
            record={'source_pdf':str(path),'source_pdf_sha256':hashlib.sha256(raw).hexdigest(),
                'page_no':1,'variant':variant,'generated_input_rotation_ccw':angle,'passed':passed,
                'eligible_ocr_region_count':eligible,'max_inverse_roundtrip_error_px':maximum_error,
                'text_frame_grid_count':len(metadata.get('scanned_grids',{}).get('tables',[])),
                'independent_table_frame_grid_count':len(metadata.get('scanned_table_evidence',{}).get('scanned_grids',{}).get('tables',[])),
                'overlay':str(overlay_path),'ocr':result}
            records.append(record)
            print(json.dumps({k:v for k,v in record.items() if k!='ocr'},ensure_ascii=False),flush=True)
    destination=ROOT/'runtime'/args.report
    destination.write_text(json.dumps({'not_official_benchmark':True,'cases':records,
        'passed':all(r['passed'] for r in records)},ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(r['passed'] for r in records) else 1


if __name__=='__main__':raise SystemExit(main())
