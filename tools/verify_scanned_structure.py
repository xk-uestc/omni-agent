"""Observe public original OCR grids; no automatic semantic score."""
import hashlib
import json
from pathlib import Path
import sys
import uuid
import fitz

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.ocr import OcrPipeline,RapidOcrExecutor
from backend.scanned_table_structure import ScannedTableStructureAgent


def main():
    source=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\administration\DUDE_a77d8e23b8ff302d04dd6254e4a67159.pdf')
    raw=source.read_bytes()
    with fitz.open(stream=raw,filetype='pdf') as doc:
        png=doc[0].get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
    (ROOT/'runtime/scanned-structure-public-page.png').write_bytes(png)
    files=[ROOT/'ict-track8/backend'/name for name in ['scanned_table_structure.py','ocr.py','scanned_table_layout.py']]
    hashes={str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    ocr=OcrPipeline(RapidOcrExecutor()).run(png,language='eng').to_dict()
    metadata=ocr['metadata'];evidence=metadata.get('scanned_table_evidence') or metadata
    grids=evidence.get('scanned_grids',{}).get('tables',[])
    observations=[]
    for index,grid in enumerate(grids):
        observations.append({'table_index':index,'row_count':grid['row_count'],'column_count':grid['column_count'],
            'top_rows':[[cell['text'] for cell in row] for row in grid['cells'][:5]],
            'structure':ScannedTableStructureAgent().run(metadata,table_index=index,header_rows=0)})
    stable=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==value for name,value in hashes.items())
    report=ROOT/'runtime'/f'scanned-structure-public-{uuid.uuid4().hex}.json'
    report.write_text(json.dumps({'not_official_benchmark':True,'semantic_accuracy_not_scored':True,
        'source_pdf_sha256':hashlib.sha256(raw).hexdigest(),'source_page':1,'render_sha256':hashlib.sha256(png).hexdigest(),
        'source_hashes':hashes,'source_stable':stable,'ocr':ocr,'observations':observations},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(report),'stable':stable,'grids':observations},ensure_ascii=False),flush=True)
    return 0 if stable else 1


if __name__=='__main__':raise SystemExit(main())
