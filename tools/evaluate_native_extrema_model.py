"""Same-source extrema development replay, scoring data never enters answer()."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
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
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists() or a.output.resolve().parent!=(ROOT/'docs').resolve():p.error('New docs report required')
    enable_local_model('gpt-6-luna');before=implementation_snapshot()
    if a.baseline:
        previous=json.loads(a.baseline.read_text(encoding='utf-8'));directory=Path(previous['run_directory']).resolve()
        if not directory.is_relative_to((ROOT/'runtime').resolve()):p.error('Original runtime corpus required')
        cases=[{k:r[k] for k in ('id','question','expected_scoring_only','source_sha256')} for r in previous['records']]
    else:
        directory=Path(tempfile.mkdtemp(prefix='native-extrema-',dir=ROOT/'runtime'));cases=[]
        s=KnowledgeStore(directory/'knowledge');rng=random.SystemRandom()
        for i in range(6):
            labels=['Unit'+''.join(rng.choice('abcdefghijklmnpqrstuvwxyz') for _ in range(8)) for _ in range(4)]
            values=[Decimal(rng.randrange(10000,90000))/100 for _ in labels]
            if i==2:values[1]=values[3]=max(values)+Decimal('100')
            if i==3:values=[-v for v in values]
            if i==4:values[0]=Decimal('999999')
            selected=list(range(1,4)) if i==4 else list(range(4))
            operation='argmin' if i in (1,5) else 'argmax'
            target=(min if operation=='argmin' else max)(values[j] for j in selected)
            winners=[labels[j] for j in selected if values[j]==target]
            title='Ledger'+''.join(rng.choice('abcdef') for _ in range(12))
            with fitz.open() as d:
                page=d.new_page();page.insert_text((40,40),title+' budget for FY 2041')
                for n,(label,value) in enumerate(zip(labels,values)):
                    page.insert_text((40,100+n*30),label);page.insert_text((300,100+n*30),'$'+format(value,',.2f'))
                raw=d.tobytes()
            did='extrema-'+str(i);s.ingest(raw,document_id=did,title=title,modality='pdf',filename=did+'.pdf')
            question=(f"Which units have the {'lowest' if operation=='argmin' else 'highest'} budget in FY 2041 "
                      f"among {', '.join(labels[j] for j in selected)} in {title}? Include all ties.")
            cases.append({'id':i,'question':question,'source_sha256':hashlib.sha256(raw).hexdigest(),
                'expected_scoring_only':{'document_id':did,'operation':operation,'winners':winners,'numeric_result':str(target)}})
    store=KnowledgeStore(directory/'knowledge')
    if len(cases)!=6:p.error('All six cases required')
    for c in cases:store.verify_source(c['expected_scoring_only']['document_id'],expected_sha256=c['source_sha256'])
    def run(c):
        client=StructuredResponses(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],model='gpt-6-luna',reasoning='medium',http_headers=local_model_headers())
        s=KnowledgeStore(directory/'knowledge',generator=GroundedGenerator(client));result=s.answer(c['question'],top_k=4)
        e=c['expected_scoring_only'];comp=result.get('computation',{});replayed=False
        if result.get('answer_mode')=='native_table_model_reviewed' and result.get('status')=='ok':
            selected=[]
            for cit in result['citations']:
                m=cit['metadata'];raw=s.verify_source(cit['document_id'],expected_sha256=m['source_sha256']).read_bytes()
                parsed=extract_native_text_tables(raw,page_no=m['page_no'],expected_source_sha256=m['source_sha256'])
                match=[f for f in parsed['facts'] if f['fact_id']==m['fact']['fact_id']]
                if match!=[m['fact']]:break
                selected.append(m['fact'])
            else:replayed=annotation_arithmetic(selected,comp['operation'])==comp
        passed=bool(replayed and comp.get('operation')==e['operation'] and Decimal(comp['numeric_result'])==Decimal(e['numeric_result'])
            and set(comp.get('winner_labels',[]))==set(e['winners']) and result['answer']==' | '.join(e['winners'])
            and {cit['document_id'] for cit in result['citations']}=={e['document_id']})
        print(json.dumps({'id':c['id'],'passed':passed,'mode':result.get('answer_mode')}),flush=True)
        return {**c,'result':result,'literal_replay_passed':replayed,'passed':passed,'api_audits':safe_audits(client)}
    with ThreadPoolExecutor(max_workers=2) as pool:records=list(pool.map(run,cases))
    after=implementation_snapshot()
    for c in cases:store.verify_source(c['expected_scoring_only']['document_id'],expected_sha256=c['source_sha256'])
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'six_generated_extrema_development_cases_not_official_accuracy',
        'run_directory':str(directory),'gold_or_document_hint_sent_to_production':False,'model':'gpt-6-luna','reasoning':'medium',
        'implementation_file_sha256_start':before,'implementation_file_sha256_end':after,'implementation_stable':before==after,
        'baseline':str(a.baseline) if a.baseline else None,'records':records,'passed':sum(r['passed'] for r in records),'total':6}
    with a.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    return 0 if before==after else 1

if __name__=='__main__':raise SystemExit(main())
