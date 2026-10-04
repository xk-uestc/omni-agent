"""Authored relational/document probes; real API, references scoring-only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading

import fitz

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from model_runtime import enable_local_model, local_model_headers
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
from backend.responses_client import StructuredResponses, GenerationError
from backend.grounded_generation import GroundedGenerator
from backend.knowledge_store import KnowledgeStore
from backend.dependency_agent import DependencyAgent
from backend.nl2sql.security import SqlSafetyError


def hashes():
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}


SQL_CASES=[
 ('nonnull','查询payments记录总数、rental_id非空记录数以及rental_id为空的数量。',
  'SELECT COUNT(*),COUNT(rental_id),SUM(CASE WHEN rental_id IS NULL THEN 1 ELSE 0 END) FROM payments'),
 ('conditional','按payments.customer_id分组，分别统计amount=0和amount>0的付款记录数，按customer_id升序。',
  'SELECT customer_id,SUM(CASE WHEN amount=0 THEN 1 ELSE 0 END),SUM(CASE WHEN amount>0 THEN 1 ELSE 0 END) FROM payments GROUP BY customer_id ORDER BY customer_id'),
 ('nested','先按payments.customer_id和rental_id合计amount，再按customer_id求每次租赁合计付款的平均值，按customer_id升序。',
  'WITH x AS(SELECT customer_id,rental_id,SUM(amount) v FROM payments GROUP BY customer_id,rental_id) SELECT customer_id,AVG(v) FROM x GROUP BY customer_id ORDER BY customer_id'),
 ('aggregate_threshold','先按payments.customer_id合计amount，只返回合计金额高于所有客户合计金额平均值的customer_id和合计金额，按customer_id升序。',
  'WITH x AS(SELECT customer_id,SUM(amount) v FROM payments GROUP BY customer_id) SELECT customer_id,v FROM x WHERE v>(SELECT AVG(v) FROM x) ORDER BY customer_id'),
 ('anti_join','列出没有任何payments付款记录的customers.id和name，关联payments.customer_id=customers.id，按id升序。',
  'SELECT c.id,c.name FROM customers c WHERE NOT EXISTS(SELECT 1 FROM payments p WHERE p.customer_id=c.id) ORDER BY c.id'),
 ('latest','对每个payments.customer_id取created_at最新一条，同一时刻id最大，只返回id、customer_id、amount，按customer_id升序。',
  'WITH x AS(SELECT *,ROW_NUMBER() OVER(PARTITION BY customer_id ORDER BY created_at DESC,id DESC) rn FROM payments) SELECT id,customer_id,amount FROM x WHERE rn=1 ORDER BY customer_id'),
 ('weighted','查询payments.amount的加权平均，权重是id，即SUM(amount*id)/SUM(id)，分母为零返回NULL。',
  'SELECT SUM(amount*id)/NULLIF(SUM(id),0) FROM payments'),
 ('fanout','按customers.id统计payments付款记录数和shipments配送记录数，分别独立聚合，不能扇出；没有对应记录时为0，按customers.id升序。',
  'WITH p AS(SELECT customer_id,COUNT(*) n FROM payments GROUP BY customer_id),s AS(SELECT customer_id,COUNT(*) n FROM shipments GROUP BY customer_id) SELECT c.id,COALESCE(p.n,0),COALESCE(s.n,0) FROM customers c LEFT JOIN p ON c.id=p.customer_id LEFT JOIN s ON c.id=s.customer_id ORDER BY c.id'),
]


def pdf_table():
    doc=fitz.open();page=doc.new_page(width=680,height=380)
    page.insert_text((40,40),'Service operating costs. Cost comparison for 2031 and 2032.')
    for x in (230,460):page.insert_text((x,65),"Cost USD '000",fontsize=10)
    for x,y in zip((230,460),('2031','2032')):page.insert_text((x,85),y,fontsize=10)
    for i,label in enumerate(('Network','Storage','Support')):
        y=115+i*25;page.insert_text((40,y),label,fontsize=10)
        for x,value in zip((230,460),(str(i+1),str((i+1)*3))):page.insert_text((x,y),value,fontsize=10)
    raw=doc.tobytes();doc.close();return raw


def pdf_formula():
    doc=fitz.open();page=doc.new_page();page.insert_text((40,60),'Engineering specification.')
    page.insert_text((40,740),'Efficiency = useful /',fontsize=11)
    page=doc.new_page();page.insert_text((40,60),'(input + losses)',fontsize=11)
    page.insert_text((40,110),'Parameter definitions follow below.')
    raw=doc.tobytes();doc.close();return raw


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.resolve().parent!=(ROOT/'docs').resolve() or args.output.exists():parser.error('New docs report required')
    enable_local_model('gpt-6-luna');before=hashes();stop=threading.Event()
    root=ROOT/'runtime'/('complex-probes-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));root.mkdir()
    database=root/'source.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript('''CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,customer_id INTEGER REFERENCES customers(id),amount REAL,rental_id INTEGER,created_at TEXT);
        CREATE TABLE shipments(id INTEGER PRIMARY KEY,customer_id INTEGER REFERENCES customers(id));
        INSERT INTO customers VALUES(1,'alpha'),(2,'beta'),(3,'empty');
        INSERT INTO payments VALUES(1,1,10,101,'2026-01-01'),(2,1,0,101,'2026-01-02'),(3,1,30,102,'2026-01-02'),(4,2,5,NULL,'2026-01-03');
        INSERT INTO shipments VALUES(1,1),(2,1),(3,1),(4,2);''')
    db_sha=hashlib.sha256(database.read_bytes()).hexdigest()
    def provider():
        return ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'],os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna',reasoning_effort='medium',reference_date=date(2026,1,1),
            http_headers=local_model_headers(),max_retries=0)
    def sql_case(row):
        identifier,question,reference=row
        if stop.is_set():return {'id':identifier,'pass':False,'status':'not_run'}
        if hashes()!=before:raise ValueError('source_changed')
        p=provider();engine=Nl2SqlEngine(database,model_plan_provider=p,metric_catalog_path=root/'none.json')
        try:
            result=engine.answer(question).to_dict()
            with sqlite3.connect(database) as db:
                expected=db.execute(reference).fetchall()
                actual=[tuple(record[col] for col in result['columns']) for record in result['rows']]
                replay=db.execute(result['sql'],result['parameters']).fetchall() if result['sql'] else None
            matched=result['status']=='ok' and actual==expected and actual==replay
            return {'id':identifier,'question':question,'pass':matched,'result':result,
                    'actual_rows':actual,'scoring_only_reference_rows':expected,'api_audits':p.audit_history}
        except (GenerationError, SqlSafetyError) as exc:
            if isinstance(exc,GenerationError) and exc.status in (401,403):stop.set()
            return {'id':identifier,'pass':False,'status':'failed','error_type':type(exc).__name__,'api_audits':p.audit_history}
    with ThreadPoolExecutor(max_workers=3) as pool:cases=list(pool.map(sql_case,SQL_CASES))
    with (root/'sql-cases.json').open('x',encoding='utf-8') as stream:
        json.dump(cases,stream,ensure_ascii=False,indent=2)
    p=provider();store=KnowledgeStore(root/'knowledge',generator=GroundedGenerator(p.client))
    store.ingest(pdf_table(),document_id='cost',title='Operating cost report',modality='pdf',filename='cost.pdf')
    store.ingest(pdf_formula(),document_id='formula',title='Engineering specification',modality='pdf',filename='formula.pdf')
    narrative=b'The service is intended to protect records. Alice manages the service.'
    store.ingest(narrative,document_id='guide',title='Service guide',modality='md',filename='guide.md')
    doc_cases=[('table_sum',"What is the total cost in 2031 for Network, Storage and Support?",'cost','6 USD thousand'),
               ('table_difference','For Network, subtract its 2031 cost from its 2032 cost.','cost','2 USD thousand'),
               ('whole_question','What is the purpose of the service and who manages it?','guide',None)]
    for identifier,question,document,expected in doc_cases:
        if stop.is_set():cases.append({'id':identifier,'pass':False,'status':'not_run'});continue
        p.reset_audit()
        try:
            result=store.answer(question,document_id=document)
            ok=result['status']=='ok' and (result.get('answer')==expected if expected else
                 'protect records' in result.get('answer','') and 'Alice' in result.get('answer','')
                 and any(a.get('operation')=='grounded_whole_question_review' for a in p.audit_history))
            cases.append({'id':identifier,'question':question,'pass':ok,'result':result,'api_audits':p.audit_history})
        except GenerationError as exc:
            if exc.status in (401,403):stop.set()
            cases.append({'id':identifier,'pass':False,'error_type':type(exc).__name__,'api_audits':p.audit_history})
    # Production tool path, explicit parameters; this is not a model planning test.
    formula=DependencyAgent(Nl2SqlEngine(database),store).execute('document_formula',{'document_id':'formula','label':'Efficiency'},{},{})
    cases.append({'id':'cross_page_formula','pass':formula.get('expression')=='useful/(input+losses)'
                  and len(formula.get('cross_page_literal_proof',{}).get('parts',[]))==2,
                  'model_planning_test':False,'result':formula})
    stable=before==hashes() and hashlib.sha256(database.read_bytes()).hexdigest()==db_sha
    report={'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'authored_development_probes_not_official_or_blind',
        'model':'gpt-6-luna','reasoning':'medium','reference_used_as_model_input':False,
        'implementation_file_sha256':before,'implementation_file_sha256_end':hashes(),
        'implementation_stable':stable,'database_sha256':db_sha,'cases':cases,
        'passed':sum(c['pass'] for c in cases),'total':len(cases),'auth_failure_stop':stop.is_set()}
    with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'passed':report['passed'],'total':len(cases),'stable':stable,
        'failed':[c['id'] for c in cases if not c['pass']]}))


if __name__=='__main__':main()
