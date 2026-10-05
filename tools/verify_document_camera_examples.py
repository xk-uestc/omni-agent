"""Selected author-released camera examples, not full benchmark accuracy."""
from pathlib import Path
import argparse,hashlib,json,sys,time,re

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.ocr import OcrPipeline,RapidOcrExecutor

REFERENCES={
    '25.jpg':['Letters','The Economist','Disruption and competition','Debt and private equity',
              'The coronavirus epidemic','Love will tear us apart'],
    '48_2 copy.png':['Quantitative Evaluation Metrics','Comparison with Existing Methods',
                   'CONCLUSION AND FUTURE WORK','Global distortion evaluation metric'],
    '51_1 copy.png':['An Example of Latent Variable Architecture','handwriting recognition',
                   'face detection','Latent variables can be viewed as intermediate results'],
}
normalize=lambda text:re.sub(r'[^a-z0-9]','',text.lower())

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--report',default='camera-page-boundary-20261005.json')
    parser.add_argument('--filename',choices=list(REFERENCES))
    args=parser.parse_args()
    if Path(args.report).name!=args.report:parser.error('Report must be a filename.')
    report=ROOT/'runtime'/args.report
    if report.exists():parser.error('Preserve existing evidence.')
    inputs=Path(r'D:\ICT8-OfficialDatasets\document-camera-examples')
    manifest=json.loads((inputs/'SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    sources={item['filename']:item for item in manifest['files']}
    pipeline=OcrPipeline(RapidOcrExecutor());cases=[]
    for filename,anchors in REFERENCES.items():
        if args.filename and filename!=args.filename:continue
        raw=(inputs/filename).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=sources[filename]['sha256']:raise ValueError('camera source changed')
        for mode,automatic in [('without_page_detection',False),('automatic_page_detection',True)]:
            start=time.monotonic()
            result=pipeline.run(raw,language='eng',max_attempts=3,auto_perspective=automatic).to_dict()
            text=normalize(result['text'])
            checks=[{'reference':reference,'matched':normalize(reference) in text} for reference in anchors]
            case={'filename':filename,'mode':mode,'source':sources[filename],
                'reference_scope':'selected_manual_visible_text_anchors_not_full_page_transcription',
                'checks':checks,'matched_reference_count':sum(check['matched'] for check in checks),
                'elapsed_seconds':time.monotonic()-start,'ocr':result}
            cases.append(case)
            report.write_text(json.dumps({'not_official_contest_benchmark':True,
                'author_released_camera_examples':True,'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({key:value for key,value in case.items() if key not in {'ocr','source'}},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
