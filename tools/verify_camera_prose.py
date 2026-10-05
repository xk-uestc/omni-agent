"""Independent prose transcription checks on an actual distorted camera page.

Gold text is used only after OCR. The two displayed formulae are explicitly
excluded from prose CER; input is not a contest benchmark. Reports are unique.
"""
from io import BytesIO
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from PIL import Image
from backend.ocr import OcrPipeline, RapidOcrExecutor
from backend.image_geometry import rotation_affine, inverse, point


def normalized(text):
    return re.sub(r'[^a-z0-9]', '', text.lower())


def distance(reference, observed):
    previous = list(range(len(observed)+1))
    for index, letter in enumerate(reference, 1):
        current = [index]
        for offset, other in enumerate(observed, 1):
            current.append(min(current[-1]+1, previous[offset]+1, previous[offset-1]+(letter!=other)))
        previous = current
    return previous[-1]


def score(result, reference, variant_to_source, expected_input_sha256):
    blocks = []
    regions = result['metadata'].get('regions', [])
    original_size = reference['size_px']
    observations = []
    for region in regions:
        geometry = region.get('original_geometry', {})
        polygon = geometry.get('polygon_px', [])
        if (len(polygon) != 4 or geometry.get('original_bbox_eligible') is not True
                or geometry.get('source_sha256') != expected_input_sha256
                or geometry.get('coordinate_scope') != 'source_image_stored_pixel_edges'):
            continue
        points = [point(variant_to_source, xy) for xy in polygon]
        x = sum(point[0] for point in points)/4/original_size[0]
        y = sum(point[1] for point in points)/4/original_size[1]
        observations.append((x, y, region['text'], geometry))
    prose_observations=[]
    def contains(block,x,y):
        if 'polygon_px' in block:
            px,py=x*original_size[0],y*original_size[1]
            polygon=block['polygon_px']
            cross=[(polygon[(i+1)%4][0]-polygon[i][0])*(py-polygon[i][1])
                   -(polygon[(i+1)%4][1]-polygon[i][1])*(px-polygon[i][0]) for i in range(4)]
            return all(value>=0 for value in cross) or all(value<=0 for value in cross)
        left, top, right, bottom = block['bounds_normalized']
        return left<=x<=right and top<=y<=bottom
    for x,y,text,_ in observations:
        if sum(contains(block,x,y) for block in reference['blocks'])==1:
            prose_observations.append(text)
    for block in reference['blocks']:
        left, top, right, bottom = block['bounds_normalized']
        selected = [(x,y,text) for x,y,text,_ in observations if contains(block,x,y)]
        observed = '\n'.join(item[2] for item in selected)
        gold, actual = normalized(block['text']), normalized(observed)
        edits = distance(gold, actual)
        blocks.append({'id':block['id'],'reference':block['text'],'observed':observed,
            'reference_characters':len(gold),'edit_distance':edits,'normalized_prose_cer':edits/max(1,len(gold)),
            'mapped_observation_count':len(selected)})
    chars = sum(block['reference_characters'] for block in blocks)
    prose='\n'.join(prose_observations)
    whole_reference=normalized(' '.join(block['text'] for block in reference['blocks']))
    return {'blocks':blocks,'reference_characters':chars,
        'normalized_prose_cer':sum(block['edit_distance'] for block in blocks)/max(1,chars),
        'prose_reading_order_cer':distance(whole_reference,normalized(prose))/max(1,len(whole_reference)),
        'prose_in_observed_reading_order':prose,
        'mapped_region_count':len(observations), 'ocr_region_count':len(regions)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--variants',nargs='+',choices=['original','rotated90','skew5'],default=['original'])
    parser.add_argument('--saved-report',type=Path,help='Rescore preserved OCR output with the same current reference and scorer')
    args=parser.parse_args()
    reference=json.loads((ROOT/'ict-track8/evaluation/camera_prose_51_1.json').read_text(encoding='utf-8'))
    raw=(Path(r'D:\ICT8-OfficialDatasets\document-camera-examples')/reference['filename']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=reference['sha256']:
        raise ValueError('Reference image identity changed')
    source=Image.open(BytesIO(raw)).convert('RGB')
    pipeline=OcrPipeline(RapidOcrExecutor())
    saved=json.loads(args.saved_report.read_text(encoding='utf-8')) if args.saved_report else None
    report=ROOT/'runtime'/f'camera-prose-{uuid.uuid4().hex}.json'
    records=[]
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in [
        'ict-track8/backend/ocr.py','ict-track8/backend/image_quality.py','ict-track8/backend/image_geometry.py',
        'ict-track8/backend/orientation_quality.py','tools/verify_camera_prose.py',
        'ict-track8/evaluation/camera_prose_51_1.json']}
    for variant in args.variants:
        angle={'original':0,'rotated90':90,'skew5':5}[variant]
        image=source if not angle else source.rotate(angle,expand=True,resample=Image.Resampling.BICUBIC,fillcolor='white')
        output=BytesIO();image.save(output,format='PNG');data=output.getvalue()
        mapping=inverse(rotation_affine(angle,list(source.size),list(image.size)))
        start=time.monotonic()
        if saved is not None:
            case=next(case for case in saved['cases'] if case['variant']==variant)
            if case['input_sha256']!=hashlib.sha256(data).hexdigest():
                raise ValueError('Saved OCR does not belong to this generated input')
            result=case['ocr']
        else:
            result=pipeline.run(data,language='eng').to_dict()
        evaluation=score(result,reference,mapping,hashlib.sha256(data).hexdigest())
        records.append({'variant':variant,'input_kind':'original_camera_pixels_png_reencoding' if not angle else 'controlled_rotation_of_actual_camera_image',
            'source_image_sha256':reference['sha256'],'input_sha256':hashlib.sha256(data).hexdigest(),
            'elapsed_seconds':time.monotonic()-start,'evaluation':evaluation,'ocr':result})
        report.write_text(json.dumps({'not_official_benchmark':True,'reference_scope':reference['scope'],
            'normalization':'lowercase_ascii_alphanumeric_prose_only_not_math_or_punctuation_accuracy',
            'evaluation_mode':'rescored_preserved_output_not_new_ocr' if saved is not None else 'actual_ocr_execution',
            'preserved_ocr_report':str(args.saved_report) if saved is not None else None,
            'preserved_ocr_source_hashes':saved.get('source_hashes') if saved is not None else None,
            'source_hashes':hashes,'cases':records},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'variant':variant,'evaluation':{k:v for k,v in evaluation.items() if k not in {'blocks','prose_in_observed_reading_order'}},'report':str(report)}),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
