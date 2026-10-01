"""Inject actual filesystem/database/process faults into disposable local data.

No production source is changed, no model is called and no secret is copied.
The first outcome is retained; subsequent fixes are development regressions.
"""
import hashlib
import gc
import json
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.dependency_agent import DependencyAgent
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.seed import initialize_database
from backend.session import ConversationStore

RAW='客单价 = 销售额 / 订单数\n标准硬件保修期为12个月。'.encode()
PLAN=[
    {'id':'formula','tool':'document_formula','args':{'document_id':'manual','label':'客单价'}},
    {'id':'values','tool':'sql','args':{'question':'2025年华东地区销售额和订单数'}},
    {'id':'final','tool':'calculate','args':{'formula':{'ref':'formula','path':[]},'parameters':{
        '销售额':{'ref':'values','path':['rows',0,'销售额']},'订单数':{'ref':'values','path':['rows',0,'订单数']}}}},
]


def stopped(call,task):
    try:
        result=call()
        return {'pass':result.get('status')=='incomplete' and result.get('failed_task')==task and 'final' not in result.get('results',{}),
                'observed_status':result.get('status'),'failed_task':result.get('failed_task'),
                'error_code':result.get('error_code'),'trace_statuses':[x['status'] for x in result.get('trace',[])]}
    except Exception as exc:
        # Only report class, never raw storage errors or local paths.
        return {'pass':False,'uncaught_exception':type(exc).__name__}


def main():
    records=[]
    def add(name,result):
        records.append({'id':name,**result})
        print(json.dumps(records[-1],ensure_ascii=False),flush=True)
    with tempfile.TemporaryDirectory(prefix='ict8-fault-') as directory:
        work=Path(directory)
        database=initialize_database(work/'sales.sqlite')
        store=KnowledgeStore(work/'knowledge')
        store.ingest(RAW,document_id='manual',title='保修与指标',modality='txt',filename='manual.txt')
        source,_=store.original('manual')
        agent=DependencyAgent(Nl2SqlEngine(database),store)
        source.write_bytes(b'tampered original')
        add('tampered_source_stops_formula',stopped(lambda:agent.run(PLAN),'formula'))
        try:
            result=store.answer('标准硬件保修期')
            add('tampered_source_stops_rag',{'pass':False,'observed_status':result['status'],'citations':len(result['citations'])})
        except ValueError:
            add('tampered_source_stops_rag',{'pass':True,'observed_status':'rejected'})
        source.write_bytes(RAW)
        source.rename(source.with_suffix('.held'))
        add('missing_source_stops_formula',stopped(lambda:agent.run(PLAN),'formula'))
        source.with_suffix('.held').rename(source)
        # Isolate the missing-file check from baseline SQLite handles that are
        # released only by cyclic GC. The engine closure has its own regression.
        gc.collect()
        database.rename(database.with_suffix('.held'))
        add('missing_database_stops_dependants',stopped(lambda:agent.run(PLAN),'values'))
        database.with_suffix('.held').rename(database)
        gc.collect()
        with closing(sqlite3.connect(database)) as locked:
            locked.execute('BEGIN EXCLUSIVE')
            add('database_lock_stops_dependants',stopped(lambda:agent.run(PLAN),'values'))
            locked.rollback()
        recovered=agent.run(PLAN)
        add('same_chain_after_restoration',{'pass':recovered['status']=='ok' and abs(recovered['results']['final']['value']-29584/3)<1e-8,
            'observed_status':recovered['status']})
        gc.collect()

        sessions=work/'sessions.sqlite'
        ConversationStore(storage_path=sessions)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        base=f'http://127.0.0.1:{port}'
        env=os.environ.copy()
        env.update({'ICT8_ENV':'development','ICT8_DB_PATH':str(database),'ICT8_KNOWLEDGE_ROOT':str(store.root),
                    'ICT8_SESSION_DB':str(sessions),'ICT8_DENSE_MODEL_PATH':'',
                    'ICT8_GENERATION_PROVIDER':'','ICT8_PLAN_PROVIDER':'','ICT8_PLAN_URL':'','ICT8_MANUAL_RETRIEVER_URL':''})
        for name in ('ICT8_OPENAI_API_KEY','OPENAI_API_KEY','ICT8_OCR_URL'):
            env.pop(name,None)
        process=None
        with (work/'server.log').open('w',encoding='utf-8') as log:
            def start():
                child=subprocess.Popen([sys.executable,str(ROOT/'tools/run_server.py'),'--port',str(port)],
                    cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    if child.poll() is not None:
                        raise RuntimeError('disposable_service_failed')
                    try:
                        if requests.get(base+'/health',timeout=.5).ok:return child
                    except requests.RequestException:pass
                    time.sleep(.2)
                child.kill();child.wait(timeout=5)
                raise RuntimeError('disposable_service_timeout')
            def post(path,payload):
                return requests.post(base+path,json=payload,timeout=12)
            try:
                process=start()
                first=post('/api/v1/omni/query',{'question':'2025年华东地区销售额','session_id':'restart-five'}).json()
                pending=post('/api/v1/omni/query',{'question':'2025年华东地区的情况','session_id':'restart-pending'}).json()
                process.kill();process.wait(timeout=5)
                process=start()
                followups=[('那华南呢','销售额',22992),('改成2024年','销售额',4998),('换成订单数','订单数',1),('那华北呢','订单数',1)]
                turns=[]
                for question,label,expected in followups:
                    answer=post('/api/v1/omni/query',{'question':question,'session_id':'restart-five'}).json()
                    turns.append({'question':question,'context_turns':answer.get('context_turns'),
                        'pass':answer.get('status')=='ok' and answer['result']['rows'][0].get(label)==expected})
                add('five_turns_across_hard_process_restart',{'pass':first['status']=='ok' and all(t['pass'] and t['context_turns']==i+1 for i,t in enumerate(turns)),'turns':turns})
                clarified=post('/api/v1/omni/clarify',{'original_question':pending['effective_question'],
                    'clarification_code':'missing_metric','selected_value':'unit_price','session_id':'restart-pending'})
                payload=clarified.json()
                add('pending_choice_across_hard_restart',{'pass':clarified.ok and payload.get('status')=='ok' and abs(payload['result']['rows'][0]['平均单价']-6797/3)<1e-8,'http_status':clarified.status_code})
                before=ConversationStore(storage_path=sessions).context('restart-five')
                with closing(sqlite3.connect(sessions)) as locked:
                    locked.execute('BEGIN EXCLUSIVE')
                    response=post('/api/v1/omni/query',{'question':'2025年销售额','session_id':'restart-five'})
                    add('locked_session_returns_sanitized_503',{'pass':response.status_code==503 and response.json().get('detail',{}).get('code')=='storage_unavailable' and response.headers.get('Retry-After')=='2',
                        'http_status':response.status_code})
                    locked.rollback()
                after=ConversationStore(storage_path=sessions).context('restart-five')
                answer=post('/api/v1/omni/query',{'question':'那华南呢','session_id':'restart-five'}).json()
                add('session_retry_after_unlock_preserves_history',{'pass':before==after and answer.get('status')=='ok' and answer['result']['rows'][0].get('订单数')==1,
                    'turns_before':len(before),'turns_after_failed_request':len(after)})
            finally:
                if process and process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    suite={'original':hashlib.sha256(RAW).hexdigest(),'plan':PLAN,
           'faults':[r['id'] for r in records]}
    signature=hashlib.sha256(json.dumps(suite,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'actual_local_fault_injection_development_audit',
        'model_called':False,'environment':{'python':platform.python_version(),'platform':platform.platform()},
        'case_sha256':signature,'passed':sum(r['pass'] for r in records),'total':len(records),'cases':records,
        'limitations':['合成资料与演示库，非公开鲁棒性基准','只在临时副本注入故障，不代表掉电磁盘恢复或所有生产故障']}
    first=ROOT/'docs/FAULT_RECOVERY_FIRST_RUN.json'
    if first.exists():
        if json.loads(first.read_text(encoding='utf-8'))['case_sha256']!=signature:raise ValueError('fault_cases_changed')
    else:first.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'docs/FAULT_RECOVERY_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'total':report['total']}))
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':raise SystemExit(main())
