"""Inspect actual public mixed-layout financial pages with local OCR.

Keep failures and observed outputs; this is not an official contest benchmark.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import fitz

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.ocr import OcrPipeline,RapidOcrExecutor


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--report',default='public-borderless-pages-20261005.json')
    args=parser.parse_args()
    report=ROOT/'runtime'/args.report
    if report.parent.resolve()!=(ROOT/'runtime').resolve() or report.exists():
        parser.error('report must be a new filename in runtime; prior evidence cannot be overwritten')
    pipeline=OcrPipeline(RapidOcrExecutor())
    cases=[]
    for name in ['DUDE_4f3030e6432f07af55229eae15f63174.pdf','DUDE_4ead21606785b8a12a5382c99c98e38c.pdf']:
        path=Path(r'D:\ICT8-OfficialDatasets\ohr-bench\pdfs\finance')/name
        raw=path.read_bytes()
        with fitz.open(stream=raw,filetype='pdf') as pdf:
            page=pdf[0]
            rendered=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False).tobytes('png')
        result=pipeline.run(rendered,language='eng',max_attempts=3).to_dict()
        metadata=result['metadata'];candidate=metadata.get('borderless_table_evidence',{})
        layout=candidate.get('table_layout',metadata.get('table_layout',{}))
        case={'source_pdf':str(path),'source_pdf_sha256':hashlib.sha256(raw).hexdigest(),'page_no':1,
            'render_sha256':hashlib.sha256(rendered).hexdigest(),'candidate_status':layout.get('status'),
            'row_count':layout.get('row_count'),'column_count':layout.get('column_count'),
            'texts':[[c['text'] for c in row] for row in layout.get('rows',[])],
            'regional_texts':[[[c['text'] for c in row] for row in candidate.get('rows',[])]
                              for candidate in layout.get('regional_candidates',[])],
            'sparse_texts':[[[c['text'] for c in row] for row in candidate.get('rows',[])]
                              for candidate in layout.get('sparse_observations',[])],
            'ocr':result}
        if name=='DUDE_4f3030e6432f07af55229eae15f63174.pdf':
            # Transcribed from the rendered original financial-results block;
            # not from model output. Multi-line label semantics remain untested.
            expected=['5835.31','6440.96','12276.27','600.00','11676.27','3613.25','8063.02']
            observed=[row[-1]['text'] for row in layout.get('rows',[]) if row]
            case['numeric_observation_check']={'reference_kind':'manual_original_page_transcription',
                'expected_values':expected,'observed_values':observed,
                'matched_count':sum(value in observed for value in expected),
                'missing_values':[value for value in expected if value not in observed],
                'extra_values':[value for value in observed if value not in expected],
                'complete':observed==expected,'labels_semantically_verified':False}
        cases.append(case)
        report.write_text(json.dumps(
            {'not_official_benchmark':True,'cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in case.items() if k!='ocr'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
