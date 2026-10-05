"""Actual public PDF page under a controlled perspective degradation.

Not a photograph or official blind benchmark. Page corners are explicitly known.
"""
from pathlib import Path
from io import BytesIO
import sys,json,hashlib,argparse
import fitz,cv2,numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.ocr import OcrPipeline,RapidOcrExecutor
from backend.image_geometry import compose,point
parser=argparse.ArgumentParser()
parser.add_argument('--report',default='public-perspective-explicit-20261005.json')
parser.add_argument('--include-auto',action='store_true')
args=parser.parse_args()
if Path(args.report).name!=args.report:raise SystemExit('Report must be a filename.')
report=ROOT/'runtime'/args.report
if report.exists():raise SystemExit('Preserve previous evidence.')
path=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\finance\DUDE_4ead21606785b8a12a5382c99c98e38c.pdf')
raw=path.read_bytes()
with fitz.open(stream=raw,filetype='pdf') as pdf:
    render=pdf[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
with Image.open(BytesIO(render)) as image:pixels=np.asarray(image.convert('RGB'));width,height=image.size
quad=[[120,80],[1080,155],[1040,1340],[160,1270]]
forward=cv2.getPerspectiveTransform(np.array([[0,0],[width,0],[width,height],[0,height]],np.float32),np.array(quad,np.float32)).reshape(-1).tolist()
raster=compose([1.,0.,-.5,0.,1.,-.5],compose(forward,[1.,0.,.5,0.,1.,.5]))
photo=cv2.warpPerspective(pixels,np.array(raster).reshape(3,3),(1180,1450),flags=cv2.INTER_CUBIC,
    borderMode=cv2.BORDER_CONSTANT,borderValue=(60,60,60))
stream=BytesIO();Image.fromarray(photo).save(stream,format='PNG');data=stream.getvalue()
pipeline=OcrPipeline(RapidOcrExecutor())
cases=[]
cases_to_run=[('uncorrected',None),('explicit_page_corners',quad)]
if args.include_auto:cases_to_run.append(('automatic_page_boundary',None))
for name,corners in cases_to_run:
    result=pipeline.run(data,language='eng',max_attempts=2,perspective_quad=corners,
        auto_perspective=name=='automatic_page_boundary').to_dict()
    evidence=result['metadata'].get('borderless_table_evidence',{})
    layout=evidence.get('table_layout',result['metadata'].get('table_layout',{}))
    regions=[*(layout.get('regional_candidates') or [layout]),*layout.get('sparse_observations',[])]
    amounts=[row[-1]['text'] for region in regions for row in region.get('rows',[]) if len(row)==2]
    # Only seven known amounts in distinct original page locations are checked.
    refs=[('$29.03m',[405,309]),('$43.54m',[595,205]),('$15.67m',[595,230]),
          ('$61.97m',[595,255]),('$22.56',[420,518]),('$5.60',[420,704]),('$10.83',[829,568])]
    checks=[]
    for value,center in refs:
        expected=point(forward,center);matches=[]
        for region in regions:
            for row in region.get('rows',[]):
                if len(row)!=2 or row[-1]['text']!=value:continue
                location=row[-1].get('original_geometry',{});box=location.get('bbox_px',[])
                if location.get('original_bbox_eligible') and location.get('source_sha256')==hashlib.sha256(data).hexdigest() and len(box)==4:
                    actual=[(box[0]+box[2])/2,(box[1]+box[3])/2]
                    if np.linalg.norm(np.array(actual)-expected)<18:matches.append(location)
        checks.append({'expected':value,'expected_photo_center':expected,'matched':len(matches)==1})
    case={'mode':name,'observed_amounts':amounts,'checks':checks,'matched_reference_count':sum(check['matched'] for check in checks),'ocr':result}
    cases.append(case)
    report.write_text(json.dumps({'not_official_benchmark':True,'controlled_degradation_not_camera_capture':True,
        'source_pdf_sha256':hashlib.sha256(raw).hexdigest(),'render_sha256':hashlib.sha256(render).hexdigest(),
        'photo_sha256':hashlib.sha256(data).hexdigest(),'explicit_quad':quad,'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in case.items() if key!='ocr'},ensure_ascii=False),flush=True)
