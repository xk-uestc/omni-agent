"""Actual PDF outline audit against frozen, manually authored paths and pages.

All samples are synthetic CC0. First results survive fixes; this is not a blind
holdout, a public benchmark, or evidence about an external model.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.chunk_cleaning import DocumentChunker


def make_pdf(case, output):
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    pdf=canvas.Canvas(str(output),pagesize=(595,842),invariant=1)
    pdf.setTitle('CC0 synthetic outline audit: '+case['id'])
    pdf.setAuthor('')
    for page in case['pages']:
        pdf.setFont('STSong-Light',12)
        for i,line in enumerate(page):
            pdf.drawString(48,790-i*28,line)
        pdf.showPage()
    pdf.save()


def run_case(case, path):
    result=DocumentChunker().parse_pdf(path.read_bytes(),document_id=case['id']).to_dict()
    headings=[c['text'] for c in result['chunks'] if c['content_type']=='heading']
    facts=[]
    for gold in case['facts']:
        candidates=[c for c in result['chunks'] if gold['text'] in c['text'] and c['content_type']!='heading']
        passed=any(c['page_no']==gold['page'] and c['title_path']==gold['path']
                   and c['source_locator'].startswith(f"page:{gold['page']}:") for c in candidates)
        facts.append({'gold':gold,'pass':passed,'observed':[{
            'text':c['text'],'path':c['title_path'],'page':c['page_no'],'locator':c['source_locator']} for c in candidates]})
    warnings=list(result['warnings'])
    warnings_ok=all(any(expected in actual for actual in warnings) for expected in case.get('warnings',[]))
    return {'id':case['id'],'pdf_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'pass':headings==case['headings'] and all(f['pass'] for f in facts) and warnings_ok,
            'expected_headings':case['headings'],'observed_headings':headings,'facts':facts,
            'expected_warnings':case.get('warnings',[]),'observed_warnings':warnings}


def main():
    case_path=ROOT/'ict-track8/eval/outline_cases.json'
    suite=json.loads(case_path.read_text(encoding='utf-8'))
    output=ROOT/'samples/outline';output.mkdir(parents=True,exist_ok=True)
    records=[]
    for case in suite['cases']:
        path=output/(case['id']+'.pdf')
        if not path.exists():
            make_pdf(case,path)
        record=run_case(case,path);records.append(record)
        print(json.dumps({'id':record['id'],'pass':record['pass']},ensure_ascii=False))
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':suite['scope'],
            'model':None,'case_sha256':hashlib.sha256(case_path.read_bytes()).hexdigest(),
            'passed':sum(r['pass'] for r in records),'total':len(records),'cases':records}
    first=ROOT/'docs/PDF_OUTLINE_FIRST_RUN.json'
    if first.exists():
        initial=json.loads(first.read_text(encoding='utf-8'))
        original_hashes={case['id']:case['pdf_sha256'] for case in initial['cases']}
        current_hashes={case['id']:case['pdf_sha256'] for case in records}
        if initial['case_sha256']!=report['case_sha256'] or original_hashes!=current_hashes:
            raise ValueError('冻结审计输入或金标已变化，不能与首次结果直接对比')
    else:
        first.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'docs/PDF_OUTLINE_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
