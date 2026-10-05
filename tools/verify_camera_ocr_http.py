"""Author-released camera image through API; selected anchors, not full OCR score."""
import argparse,base64,hashlib,json,uuid
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--filename',default='25.jpg',choices=['25.jpg','48_2 copy.png','51_1 copy.png'])
    args=parser.parse_args()
    inputs=Path(r'D:\ICT8-OfficialDatasets\document-camera-examples')
    manifest=json.loads((inputs/'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    entry=next(item for item in manifest['files'] if item['filename']==args.filename)
    raw=(inputs/args.filename).read_bytes();source=hashlib.sha256(raw).hexdigest()
    if source!=entry['sha256']:raise ValueError('Public source changed')
    payload={'image_base64':base64.b64encode(raw).decode(),'language':'eng','max_attempts':3,'auto_perspective':True}
    request=Request('http://127.0.0.1:8030/api/v1/documents/ocr',
        data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urlopen(request,timeout=300) as response:result=json.load(response)
    metadata=result.get('metadata',{})
    observations=[region for region in metadata.get('regions',[]) if region.get('text')]
    locations=[region.get('original_geometry',{}) for region in observations]
    mapped=bool(locations) and all(location.get('source_sha256')==source
        and location.get('original_bbox_eligible') for location in locations)
    passed=result.get('status')=='ok' and metadata.get('source_image_sha256')==source and mapped
    record={'not_official_contest_benchmark':True,'full_page_accuracy_measured':False,
        'filename':args.filename,'source':entry,'source_bound_observation_count':len(locations),
        'passed':passed,'result':result}
    files=['ict-track8/backend/perspective.py','ict-track8/backend/ocr.py',
           'ict-track8/backend/ocr_evidence_selection.py','ict-track8/backend/scanned_table_layout.py']
    record['source_code_sha256']={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}
    report=ROOT/'runtime'/f'camera-ocr-http-{uuid.uuid4().hex}.json'
    report.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':passed,'observations':len(locations),'boundary':metadata.get('page_boundary_detection'),
                     'report':str(report)},ensure_ascii=False),flush=True)
    return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
