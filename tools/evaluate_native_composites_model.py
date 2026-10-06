"""Six new randomized production questions; references are scoring-only."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import random
import sys
import tempfile

import fitz

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.grounded_generation import GroundedGenerator
from backend.responses_client import StructuredResponses
from backend.native_text_tables import extract_native_text_tables
from backend.native_table_question import annotation_arithmetic
from evaluate_ohr_bench import implementation_snapshot, safe_audits
from model_runtime import enable_local_model,local_model_headers


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,help='Replay the identical six retained questions and original PDFs')
    args=parser.parse_args()
    if args.output.exists() or args.output.resolve().parent!=(ROOT/'docs').resolve():parser.error('New docs report required')
    enable_local_model('gpt-6-luna');before=implementation_snapshot()
    previous=None
    if args.baseline:
        if args.baseline.resolve().parent!=(ROOT/'docs').resolve():parser.error('Retained docs baseline required')
        previous=json.loads(args.baseline.read_text(encoding='utf-8'))
        folder=Path(previous['run_directory']).resolve()
        if (not folder.is_relative_to((ROOT/'runtime').resolve()) or previous.get('implementation_stable') is not True
                or previous.get('gold_or_document_hint_sent_to_production') is not False or len(previous.get('records',[]))!=6):
            parser.error('Stable reference-isolated six-case baseline required')
        cases=[{key:c[key] for key in ('id','question','source_sha256','expected_scoring_only')} for c in previous['records']]
    else:
        folder=Path(tempfile.mkdtemp(prefix='native-composites-',dir=ROOT/'runtime'));cases=[]
    store=KnowledgeStore(folder/'knowledge');rng=random.SystemRandom()
    for index in range(0 if previous else 6):
        title='Ledger'+''.join(rng.choice('abcdefghijklmnpqrstuvwxyz') for _ in range(12))
        count=4 if index==5 else 20
        labels=['Unit'+''.join(rng.choice('abcdefghijklmnpqrstuvwxyz') for _ in range(8)) for _ in range(count)]
        values=rng.sample(range(100,9000),count)
        winner=max(range(count),key=lambda n:values[n]);addend=(winner+3)%count
        with fitz.open() as doc:
            p=doc.new_page(height=1000)
            p.insert_text((40,40),title+' equipment budget FY 2045')
            if index==5:
                for n,(label,value) in enumerate(zip(labels,values)):
                    x,y=40+250*(n%2),100+180*(n//2)
                    p.insert_text((x,y),label+',');p.insert_text((x+5,y+14),'$'+str(value))
            else:
                for n,(label,value) in enumerate(zip([*labels,'Total'],[*values,sum(values)])):
                    p.insert_text((40,100+n*25),label)
                    literal='$'+str(value);p.insert_text((340-fitz.get_text_length(literal,fontsize=11),100+n*25),literal)
            raw=doc.tobytes()
        did='composite-'+str(index);store.ingest(raw,document_id=did,title=title,modality='pdf',filename=did+'.pdf')
        if index in (0,5):
            question=f'In {title}, which unit has the highest equipment budget for FY 2045 among {", ".join(labels)}?'
            operation='argmax';expected=labels[winner];fraction=None
        else:
            if index==4:
                selected=(addend,(addend+1)%count)
                question=f'In {title}, what percentage of the total equipment budget for FY 2045 is the combined budget of {labels[selected[0]]} and {labels[selected[1]]}? Use two decimal places.'
                operation='sum_percentage';numerator=sum(values[n] for n in selected)
            else:
                question=f'In {title}, what percentage of the total equipment budget for FY 2045 is the combined budget of the unit with the highest budget and {labels[addend]}? Use two decimal places.'
                operation='max_plus_percentage';numerator=values[winner]+values[addend]
            fraction=Fraction(numerator,sum(values))*100
            digits,remainder=divmod(fraction.numerator*100,fraction.denominator)
            digits+=int(remainder*2>=fraction.denominator)
            expected=('≈' if Fraction(digits,100)!=fraction else '')+format(Decimal(digits)/100,'.2f')+'%'
        cases.append({'id':index,'question':question,'source_sha256':hashlib.sha256(raw).hexdigest(),
            'expected_scoring_only':{'document_id':did,'operation':operation,'answer':expected,
                'exact_fraction':{'numerator':str(fraction.numerator),'denominator':str(fraction.denominator)} if fraction is not None else None}})
    for case in cases:store.verify_source(case['expected_scoring_only']['document_id'],expected_sha256=case['source_sha256'])
    def run(case):
        client=StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna',reasoning='medium',http_headers=local_model_headers())
        s=KnowledgeStore(folder/'knowledge',generator=GroundedGenerator(client))
        # Only the original user question reaches the production entry.
        result=s.answer(case['question'],top_k=4);replay=False;computation=result.get('computation',{})
        if result.get('status')=='ok' and result.get('answer_mode')=='native_table_model_reviewed':
            selected=[]
            for citation in result['citations']:
                meta=citation['metadata'];raw=s.verify_source(citation['document_id'],expected_sha256=meta['source_sha256']).read_bytes()
                parsed=extract_native_text_tables(raw,page_no=meta['page_no'],expected_source_sha256=meta['source_sha256'])
                if [f for f in parsed['facts'] if f['fact_id']==meta['fact']['fact_id']]!=[meta['fact']]:break
                selected.append(meta['fact'])
            else:replay=annotation_arithmetic(selected,computation['operation'])==computation
        expected=case['expected_scoring_only']
        passed=bool(replay and result.get('answer')==expected['answer'] and computation.get('operation')==expected['operation']
            and (expected['exact_fraction'] is None or computation.get('exact_fraction')==expected['exact_fraction'])
            and {c['document_id'] for c in result.get('citations',[])}=={expected['document_id']})
        print(json.dumps({'id':case['id'],'passed':passed,'status':result['status']}),flush=True)
        return {**case,'result':result,'literal_replay_passed':replay,'passed':passed,'api_audits':safe_audits(client)}
    with ThreadPoolExecutor(max_workers=2) as pool:records=list(pool.map(run,cases))
    after=implementation_snapshot()
    for case in cases:store.verify_source(case['expected_scoring_only']['document_id'],expected_sha256=case['source_sha256'])
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'six_randomized_development_cases_not_official_or_blind_accuracy',
        'baseline':{'path':str(args.baseline),'sha256':hashlib.sha256(args.baseline.read_bytes()).hexdigest()} if args.baseline else None,
        'run_directory':str(folder),'gold_or_document_hint_sent_to_production':False,'model':'gpt-6-luna','reasoning':'medium',
        'implementation_file_sha256_start':before,'implementation_file_sha256_end':after,'implementation_stable':before==after,
        'records':records,'passed':sum(r['passed'] for r in records),'total':len(records)}
    with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    return 0 if before==after and all(r['passed'] for r in records) else 1


if __name__=='__main__':raise SystemExit(main())
