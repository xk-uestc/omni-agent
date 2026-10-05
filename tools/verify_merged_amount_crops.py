"""Debug real crop observations without replacing prior reports."""
from pathlib import Path
from io import BytesIO
import sys,json,hashlib
from PIL import Image
import fitz
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.ocr import RapidOcrExecutor
from backend.scanned_table_layout import recover_merged_amount_pairs
executor=RapidOcrExecutor();model=executor._load()
source=ROOT/'runtime/public-sparse-pages-20261005.json'
case=json.loads(source.read_text(encoding='utf-8'))['cases'][1]
output=ROOT/'runtime/merged-amount-crop-debug-20261005.json'
if output.exists():raise SystemExit('Preserve prior evidence.')
with fitz.open(case['source_pdf']) as pdf:data=pdf[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
assert hashlib.sha256(data).hexdigest()==case['render_sha256']
calls=[]
def recognize(crop):
    found,_=model(crop,use_cls=True)
    result=[{'text':str(row[1]),'confidence':float(row[2]),'bbox':row[0].tolist() if hasattr(row[0],'tolist') else row[0]} for row in (found or [])]
    with Image.open(BytesIO(crop)) as image:size=list(image.size)
    calls.append({'size':size,'observations':result})
    print(json.dumps({'size':size,'texts':[item['text'] for item in result]},ensure_ascii=False),flush=True)
    return result
pairs,audit=recover_merged_amount_pairs(data,case['ocr']['metadata']['regions'],recognize,max_seconds=45)
output.write_text(json.dumps({'source_pdf_sha256':case['source_pdf_sha256'],'pairs':pairs,'audit':audit,'calls':calls},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(audit),flush=True)
