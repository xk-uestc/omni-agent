"""New-schema audit with frozen SQL/result gold; no model or per-domain aliases.

These are authored audit cases, not a public benchmark or blinded holdout.
After fixes they become development regressions, with the first result retained.
"""
import hashlib
import json
import platform
import sqlite3
import sys
import time
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine
from backend.knowledge_store import KnowledgeStore
from backend.omni_agent import OmniAgent
from backend.session import ConversationStore


def main():
    output=ROOT/'runtime/domain-transfer';output.mkdir(parents=True,exist_ok=True)
    db=output/'claims.sqlite'
    with sqlite3.connect(db) as connection:
        connection.executescript('DROP TABLE IF EXISTS Claims; CREATE TABLE Claims(ClaimId INTEGER PRIMARY KEY,Employee TEXT,Department TEXT,ClaimDate TEXT,ApprovedAmount REAL,State TEXT);')
        connection.executemany('INSERT INTO Claims VALUES(?,?,?,?,?,?)',[
            (1,'林安','研发一部','2025-01-10',150.5,'已报销'),(2,'林安','研发一部','2025-05-05',300,'已报销'),
            (3,'周平','营销二部','2025-08-12',800,'审核中'),(4,'孙新','研发一部','2024-03-20',500,'已报销'),
            (5,'周平','营销二部','2024-11-01',250,'已报销'),(6,'孙新','研发一部','2025-12-09',100,'已报销')])
    cases=[
        ('claim_sum','Claims ApprovedAmount合计','SELECT SUM(ApprovedAmount) FROM Claims'),
        ('claim_average','Claims ApprovedAmount平均值','SELECT AVG(ApprovedAmount) FROM Claims'),
        ('department_sum','按Claims Department统计ApprovedAmount','SELECT Department,SUM(ApprovedAmount) FROM Claims GROUP BY Department'),
        ('year_filter','2025年Claims ApprovedAmount合计',"SELECT SUM(ApprovedAmount) FROM Claims WHERE ClaimDate>='2025-01-01' AND ClaimDate<'2026-01-01'"),
        ('status_filter','已报销的Claims ApprovedAmount合计',"SELECT SUM(ApprovedAmount) FROM Claims WHERE State='已报销'"),
        ('combined_filter','2025年研发一部已报销的Claims ApprovedAmount合计',"SELECT SUM(ApprovedAmount) FROM Claims WHERE ClaimDate>='2025-01-01' AND ClaimDate<'2026-01-01' AND Department='研发一部' AND State='已报销'"),
        ('min_amount','Claims ApprovedAmount最小值','SELECT MIN(ApprovedAmount) FROM Claims'),
        ('max_amount','Claims ApprovedAmount最大值','SELECT MAX(ApprovedAmount) FROM Claims'),
    ]
    engine=Nl2SqlEngine(db,metric_catalog_path=output/'absent_catalog.json')
    agent=OmniAgent(engine,KnowledgeStore(output/'knowledge'),ConversationStore())
    records=[]
    normalize=lambda rows:sorted([tuple(round(v,6) if isinstance(v,(int,float)) else v for v in row) for row in rows],key=repr)
    for name,question,gold in cases:
        with sqlite3.connect(db) as connection:
            expected=connection.execute(gold).fetchall()
        start=time.perf_counter()
        answer=agent.query(question)
        result=answer['result'];columns=result.get('columns',[])
        observed=[tuple(row[col] for col in columns) for row in result.get('rows',[])]
        passed=answer['route']=='sql' and answer['status']=='ok' and normalize(expected)==normalize(observed)
        records.append({'id':name,'question':question,'gold_sql':gold,'expected':expected,'observed':observed,
                        'pass':passed,'route':answer['route'],'status':answer['status'],
                        'latency_ms':round((time.perf_counter()-start)*1000,3),'result':result})
        print(json.dumps({'id':name,'pass':passed,'status':answer['status']},ensure_ascii=False))
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'authored_new_schema_audit_not_blind_holdout',
            'model':None,'few_shot_examples':0,'domain_alias_rules':0,'python':platform.python_version(),
            'case_sha256':hashlib.sha256(json.dumps(cases,ensure_ascii=False).encode()).hexdigest(),
            'passed':sum(r['pass'] for r in records),'total':len(records),'cases':records}
    report_path=ROOT/'docs/DOMAIN_TRANSFER_REPORT.json'
    first=ROOT/'docs/DOMAIN_TRANSFER_FIRST_RUN.json'
    if not first.exists():
        first.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
