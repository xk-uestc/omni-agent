"""Fresh grouped-finance development cases through ordinary production retrieval."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import random
import string
import sys
import tempfile
import threading
import fitz

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore
from backend.native_table_question import annotation_arithmetic
from backend.native_text_tables import extract_native_text_tables
from backend.responses_client import StructuredResponses
from evaluate_ohr_bench import implementation_snapshot, safe_audits
from model_runtime import enable_local_model, local_model_headers


def pdf(entity,year,unit,profit):
    with fitz.open() as doc:
        page=doc.new_page(width=650,height=550)
        def write(x,y,text):page.insert_text((x,y),text,fontsize=10)
        write(40,35,entity+' income statement excerpt for FY '+str(year))
        write(40,55,'Selected rows only; other income and expense lines are omitted.')
        for center,label in [(350,'Group'),(510,'Company')]:
            write(center-fitz.get_text_length(label,fontsize=10)/2,85,label)
        write(230,105,'Note')
        for right,current in zip((320,400,480,560),(year,year-1,year,year-1)):
            write(right-fitz.get_text_length(str(current),fontsize=10),105,str(current))
            write(right-fitz.get_text_length(unit,fontsize=10),125,unit)
        data=[('Revenue',[Decimal('450000.00')]*4),('Cost of sales',[Decimal('-80000.00')]*4),
              ('Profit for the year',profit),('Administrative expenses',[Decimal('-13000.00')]*4)]
        for row,(label,values) in enumerate(data):
            write(40,155+row*28,label)
            for right,value in zip((320,400,480,560),values):
                text=format(abs(value),',.2f')
                if value<0:text='('+text+')'
                write(right-fitz.get_text_length(text,fontsize=10),155+row*28,text)
        return doc.tobytes()


def literal_replay(store,result):
    selected=[]
    for citation in result.get('citations',[]):
        metadata=citation['metadata'];fact=metadata['fact']
        raw=store.verify_source(citation['document_id'],expected_sha256=metadata['source_sha256']).read_bytes()
        registry=extract_native_text_tables(raw,page_no=metadata['page_no'],expected_source_sha256=metadata['source_sha256'])
        matches=[f for f in registry['facts'] if f['fact_id']==fact['fact_id']]
        if matches!=[fact]:return False
        selected.append(fact)
    return annotation_arithmetic(selected,result['computation']['operation'])==result['computation']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,help='Reuse all eight prior questions and all original competing PDFs')
    parser.add_argument('--explicit-report-year',action='store_true',
                        help='Separate version-scoped evaluation; keeps original PDFs but changes the visible question')
    args=parser.parse_args()
    if args.output.resolve().parent!=(ROOT/'docs').resolve() or args.output.exists():
        parser.error('New report in docs required')
    if args.explicit_report_year and not args.baseline:
        parser.error('Version-scoped questions require a retained original baseline')
    enable_local_model('gpt-6-luna')
    before=implementation_snapshot()
    directory=Path(tempfile.mkdtemp(prefix='financial-change-',dir=ROOT/'runtime'))
    store=KnowledgeStore(directory/'knowledge');rng=random.SystemRandom();cases=[]
    sources={}
    if args.baseline:
        baseline=json.loads(args.baseline.read_text(encoding='utf-8'))
        prior_dir=Path(baseline['run_directory']).resolve()
        if not prior_dir.is_relative_to((ROOT/'runtime').resolve()):parser.error('Original runtime store required')
        if len(baseline['records'])!=8 or {r['id'] for r in baseline['records']}!=set(range(8)):
            parser.error('All eight original cases required')
        directory=prior_dir;store=KnowledgeStore(directory/'knowledge')
        cases=[{key:r[key] for key in ('id','question','expected_scoring_only','original_sha256')} for r in baseline['records']]
        docs=store.list_documents()
        if {d['document_id'] for d in docs}!={f'{kind}-finance-{i}' for kind in ('fresh','prior') for i in range(8)}:
            parser.error('Original competing source collection differs')
        for d in docs:
            raw=store.verify_source(d['document_id'],expected_sha256=d['sha256']).read_bytes()
            sources[d['document_id']]=hashlib.sha256(raw).hexdigest()
        for case in cases:
            if sources[case['expected_scoring_only']['document_id']]!=case['original_sha256']:
                parser.error('Original PDF SHA differs')
        if args.explicit_report_year:
            for case in cases:
                case['original_unscoped_question']=case['question']
                year=case['expected_scoring_only']['target_period']
                case['question']=f"According to the income statement excerpt for FY {year}, "+case['question']
    definitions=[('Group','increase_check',1),('Group','decrease_check',-1),
        ('Group','increase_check',-1),('Group','decrease_check',1),
        ('Company','increase_check',1),('Company','decrease_check',-1),
        ('Group','increase_check',0),('Group','increase_check',1)]
    for index,(role,operation,sign) in enumerate([] if args.baseline else definitions):
        entity='Issuer'+''.join(rng.choice(string.ascii_lowercase) for _ in range(10))
        year=2038+index;unit=["RM'000","USD'000","MYR'000","SGD'000"][index%4]
        baseline=Decimal(rng.randint(10000,9999999))/100
        delta=Decimal(rng.randint(100,99999))/100
        target=baseline+sign*delta
        if index==7:baseline=-baseline-delta;target=baseline+delta
        if index==5:baseline=-baseline;target=baseline-delta
        opposite=[Decimal('300.00'),Decimal('500.00')] if sign>=0 else [Decimal('700.00'),Decimal('500.00')]
        profit=[target,baseline,*opposite] if role=='Group' else [*opposite,target,baseline]
        source=pdf(entity,year,unit,profit);did=f'fresh-finance-{index}'
        store.ingest(source,document_id=did,title=entity,modality='pdf',filename=did+'.pdf')
        prior=pdf(entity,year-1,unit,[v+Decimal('17.43') for v in profit])
        store.ingest(prior,document_id=f'prior-finance-{index}',title=entity,modality='pdf',filename=f'prior-{index}.pdf')
        question=f"Did the {role}'s profit for the year {'increase' if operation=='increase_check' else 'decrease'} in {year} compared to {year-1} for {entity}, and by how much?"
        cases.append({'id':index,'question':question,'expected_scoring_only':{
            'operation':operation,'signed_change':format(target-baseline,'f'),
            'matched':target>baseline if operation=='increase_check' else target<baseline,
            'role':role,'target_period':str(year),'baseline_period':str(year-1),
            'currency':unit.split("'")[0],'scale':'1000','document_id':did},
            'original_sha256':hashlib.sha256(source).hexdigest()})
    stop=threading.Event()
    def execute(case):
        if stop.is_set():return {**case,'status':'not_run','passed':False,'api_audits':[]}
        client=StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna',reasoning='medium',http_headers=local_model_headers())
        knowledge=KnowledgeStore(directory/'knowledge',generator=GroundedGenerator(client))
        try:
            result=knowledge.answer(case['question'],top_k=4)
            expected=case['expected_scoring_only'];scope=result.get('answer_scope',{});comp=result.get('computation',{})
            comparison=comp.get('comparison',{})
            replayed=result.get('status')=='ok' and result.get('answer_mode')=='native_table_model_reviewed' and literal_replay(knowledge,result)
            passed=(replayed and comp['operation']==expected['operation']
                and Decimal(comp['numeric_result'])==Decimal(expected['signed_change'])
                and comparison.get('matched') is expected['matched']
                and comparison.get('target_period')==expected['target_period']
                and comparison.get('baseline_period')==expected['baseline_period']
                and scope.get('currency')==expected['currency'] and scope.get('scale')==expected['scale']
                and all(p[0]==expected['role'] for p in scope.get('column_header_paths',[]))
                and {c['document_id'] for c in result['citations']}=={expected['document_id']})
            record={**case,'result':result,'literal_replay_passed':replayed,'passed':bool(passed),'api_audits':safe_audits(client)}
        except Exception as exc:
            record={**case,'error_type':type(exc).__name__,'passed':False,'api_audits':safe_audits(client)}
        if any(a.get('http_status') in (401,403) for a in record['api_audits']):stop.set()
        print(json.dumps({'id':case['id'],'passed':record['passed'],'mode':record.get('result',{}).get('answer_mode')}),flush=True)
        return record
    with ThreadPoolExecutor(max_workers=2) as pool:records=list(pool.map(execute,cases))
    after=implementation_snapshot()
    sources_unchanged=all(hashlib.sha256(store.verify_source(did,expected_sha256=digest).read_bytes()).hexdigest()==digest for did,digest in sources.items())
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':(
        'explicit_report_year_financial_development_cases_changed_questions_not_same_question_improvement'
        if args.explicit_report_year else 'fresh_generated_financial_development_cases_not_official_or_blind'),
        'questions_unchanged':bool(args.baseline and not args.explicit_report_year),
        'visible_report_version_condition_added':args.explicit_report_year,
        'gold_or_document_hint_sent_to_production':False,'model':'gpt-6-luna','reasoning':'medium',
        'run_directory':str(directory),'implementation_file_sha256_start':before,'implementation_file_sha256_end':after,
        'implementation_stable':before==after,'records':records,'passed':sum(r['passed'] for r in records),'total':len(records),
        'baseline_report':str(args.baseline) if args.baseline else None,
        'baseline_report_sha256':hashlib.sha256(args.baseline.read_bytes()).hexdigest() if args.baseline else None,
        'original_collection_sha256':sources,'original_collection_unchanged':sources_unchanged}
    with args.output.open('x',encoding='utf-8') as out:json.dump(report,out,ensure_ascii=False,indent=2)
    print(json.dumps({'passed':report['passed'],'total':report['total'],'stable':report['implementation_stable']}))
    return 0 if before==after and sources_unchanged else 1


if __name__=='__main__':raise SystemExit(main())
