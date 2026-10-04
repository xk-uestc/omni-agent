"""Raster-only authored documents through production QA, references scoring-only."""
from datetime import datetime,timezone
from io import BytesIO
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

import fitz
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from model_runtime import enable_local_model,local_model_headers
from backend.responses_client import StructuredResponses
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore


def hashes():
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}


def raster_document(title,pages):
    """Native layer has navigation title only; facts exist only in page images."""
    with fitz.open() as doc:
        for lines in pages:
            image=Image.new('RGB',(1000,600),'white')
            draw=ImageDraw.Draw(image);font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',30)
            for index,line in enumerate(lines):draw.text((40,35+index*65),line,fill='black',font=font)
            png=BytesIO();image.save(png,format='PNG')
            page=doc.new_page(width=500,height=350)
            page.insert_text((20,20),title,fontsize=10)
            page.insert_image(fitz.Rect(0,35,500,335),stream=png.getvalue())
        raw=doc.tobytes()
    # No reference values or response hints in the native layer.
    with fitz.open(stream=raw,filetype='pdf') as doc:
        assert all(page.get_text().strip()==title for page in doc)
    return raw


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().parent!=(ROOT/'docs').resolve() or args.output.exists():parser.error('New docs report required')
    enable_local_model();before=hashes();rng=secrets.SystemRandom()
    numbers=rng.sample(range(10000,99999),3)
    suffix=secrets.token_hex(3).upper()
    tests=['Spectrum-'+suffix,'Redox-'+suffix]
    fixtures=[('borderless','Budget bulletin',[[
        'Region       2033 allocation (USD thousand)',
        f'East              {numbers[0]}',f'West              {numbers[1]}',f'North             {numbers[2]}']],
        'In the budget bulletin, what is the East region allocation in 2033?',
        {'required':[str(numbers[0]),'USD','thousand'],'excluded':[str(n) for n in numbers[1:]]}),
        ('multi','Assay bulletin',[[
            'Components assayed: Iron, Manganese.',
            'Tests used: '+', '.join(tests)+'.']],
            'In the assay bulletin, which components are assayed and which two tests are used?',
            {'required':['Iron','Manganese',*tests],'excluded':[]}),
        ('formula','Mechanism bulletin',[[
            'Efficiency = useful /'],['(input + losses)',
            'useful: Joule; input: Joule; losses: Joule.']],
            'In the mechanism bulletin, give the complete printed Efficiency formula and the units of useful, input and losses.',
            {'required':['Efficiency','useful','/','input','+','losses','Joule'],'excluded':[]}),
        ('missing','Assay bulletin',[[
            'Components assayed: Iron, Manganese.',
            'Tests used: '+', '.join(tests)+'.']],
            'In the assay bulletin, what is the Neon concentration in percent?',
            {'expected_abstention':True})]
    directory=ROOT/'runtime'/('round8-visual-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.mkdir(parents=True);cases=[]
    for identifier,title,pages,question,reference in fixtures:
        if hashes()!=before:raise RuntimeError('implementation_changed')
        client=StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna',reasoning='medium',http_headers=local_model_headers())
        store=KnowledgeStore(directory/identifier,generator=GroundedGenerator(client))
        raw=raster_document(title,pages)
        store.ingest(raw,document_id=identifier,title=title,modality='pdf',filename=identifier+'.pdf')
        result=store.answer(question,document_id=identifier,top_k=8)
        answer=result.get('answer') or ''
        if reference.get('expected_abstention'):
            passed=result['status']!='ok'
        else:
            passed=(result['status']=='ok' and result.get('answer_mode')=='visual_source_model_reviewed'
                and all(token.casefold() in answer.casefold() for token in reference['required'])
                and all(token not in answer for token in reference['excluded'])
                and len(result['visual_source_proof']['parts'])>0)
        cases.append({'id':identifier,'question':question,'source_sha256':hashlib.sha256(raw).hexdigest(),
            'pass':passed,'reference_scoring_only':reference,'result':result,'api_audits':client.audit_history})
        print(json.dumps({'id':identifier,'pass':passed,'mode':result.get('answer_mode'),'status':result['status']}),flush=True)
        if any(a.get('http_status') in (401,403) for a in client.audit_history):break
    report={'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'authored_raster_only_production_QA_probe_not_official_or_blind_accuracy',
        'reference_used_as_model_input':False,'native_answer_text_present':False,
        'model':'gpt-6-luna','reasoning':'medium','total':4,'executed':len(cases),
        'passed':sum(c['pass'] for c in cases),'cases':cases,
        'implementation_file_sha256':before,'implementation_file_sha256_end':hashes(),
        'implementation_stable':before==hashes(),
        'limitations':['Required-token scoring is not formal semantics or general formula correctness.',
            'Visual quotes and boxes are independently model reviewed, never deterministic typed facts.']}
    with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'passed':report['passed'],'total':4,'stable':report['implementation_stable']}))


if __name__=='__main__':main()
