"""Actual local HTTP XLSX preview+ingest+restart acceptance, no model requests."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.seed import initialize_database
from excel_irregular_cases import build_cases,evaluate_case


def main():
    output=ROOT/'runtime'/('excel-irregular-http-'+uuid4().hex);output.mkdir()
    cases=build_cases();KnowledgeStore(output/'knowledge');initialize_database(output/'business.sqlite')
    env=os.environ.copy();env.update({'ICT8_KNOWLEDGE_ROOT':str(output/'knowledge'),'ICT8_DB_PATH':str(output/'business.sqlite'),
                                    'ICT8_SESSION_DB':str(output/'sessions.sqlite'),'ICT8_DENSE_MODEL_PATH':'','ICT8_SCHEMA_ALIASES':'','PYTHONIOENCODING':'utf-8'})
    for key in ('ICT8_API_KEY','ICT8_MODEL_API_KEY'):env.pop(key,None)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}';records=[];process=None;log=None;pids=[]
    files=['ict-track8/backend/excel_layout.py','ict-track8/backend/chunk_cleaning.py','ict-track8/backend/knowledge_store.py','ict-track8/backend/dependency_agent.py','ict-track8/backend/app.py']
    hashes={f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files}
    def stop():
        nonlocal process,log
        if process:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
            process=None
        if log:log.close();log=None
    def start():
        nonlocal process,log
        log=(output/f'server-{len(pids)+1}.log').open('wb')
        process=subprocess.Popen([sys.executable,str(ROOT/'tools/run_server.py'),'--port',str(port)],cwd=ROOT,env=env,
            stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0);pids.append(process.pid)
        deadline=time.monotonic()+50
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('isolated server exited')
            try:
                with urlopen(base+'/health',timeout=1) as response:
                    if response.status==200:return
            except OSError:pass
            time.sleep(.25)
        raise RuntimeError('isolated server readiness timeout')
    def http(path,payload=None):
        request=Request(base+path,data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None,
                        headers={'Content-Type':'application/json'})
        try:
            with urlopen(request,timeout=60) as response:return response.status,json.load(response)
        except HTTPError as exc:return exc.code,json.load(exc)
    def check(name,passed,details):
        records.append({'name':name,'passed':bool(passed),'details':details});print(json.dumps({'check':len(records),'name':name,'passed':bool(passed)},ensure_ascii=False),flush=True)
    try:
        start()
        for case in cases:
            encoded=base64.b64encode(case.raw).decode()
            payload={'document_id':case.name,'modality':'xlsx','file_base64':encoded}
            status,preview=http('/api/v1/documents/chunks-preview',payload)
            result=evaluate_case(case,preview) if status==200 else preview
            (output/(case.name+'-preview.json')).write_text(json.dumps(preview,ensure_ascii=False,indent=2),encoding='utf-8')
            check(case.name+'-preview',status==200 and result['passed'],result)
            status,ingested=http('/api/v1/knowledge/ingest',{**payload,'title':case.description,'filename':case.name+'.xlsx'})
            check(case.name+'-ingest',status==200 and ingested.get('sha256')==hashlib.sha256(case.raw).hexdigest(),ingested)
            status,document=http('/api/v1/knowledge/documents/'+case.name)
            result=evaluate_case(case,{'chunks':document.get('chunks',[]),'stats':document.get('stats',{}),'warnings':document.get('warnings',[])}) if status==200 else document
            check(case.name+'-persisted',status==200 and result['passed'],result)
        case=next(c for c in cases if c.name=='10_headerless_text')
        payload={'document_id':'user-confirmed','modality':'xlsx','title':'已确认表头','filename':'confirmed.xlsx',
                 'file_base64':base64.b64encode(case.raw).decode(),'excel_tables':[{'sheet_name':'数据','range':'A1:B2','header_rows':1}]}
        status,confirmed=http('/api/v1/knowledge/ingest',payload)
        check('explicit-header-confirmation',status==200 and confirmed['stats']['table_layouts'][0]['header_decision']=='user_selected',confirmed)
        for label,options in [('overlap',[{'sheet_name':'数据','range':'A1:B2','header_rows':1},{'sheet_name':'数据','range':'B1:C2','header_rows':1}]),
                              ('wrong-sheet',[{'sheet_name':'不存在','range':'A1:B2','header_rows':1}]),
                              ('bad-count',[{'sheet_name':'数据','range':'A1:B2','header_rows':7}])]:
            status,result=http('/api/v1/documents/chunks-preview',{**payload,'excel_tables':options})
            check('reject-'+label,status==400,result)
        stop();start()
        for case in cases:
            status,document=http('/api/v1/knowledge/documents/'+case.name)
            result=evaluate_case(case,{'chunks':document.get('chunks',[]),'stats':document.get('stats',{}),'warnings':document.get('warnings',[])}) if status==200 else document
            check(case.name+'-restart',status==200 and result['passed'],result)
        report={'not_official_benchmark':True,'model_requests':0,'passed':sum(r['passed'] for r in records),'total':len(records),
                'independent_server_pids':pids,'source_hashes':hashes,'source_stable':all(hashlib.sha256((ROOT/f).read_bytes()).hexdigest()==v for f,v in hashes.items()),'checks':records}
        (output/'http-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(str(output/'http-report.json'),flush=True)
        return 0 if report['passed']==report['total'] and report['source_stable'] else 1
    finally:stop()


if __name__=='__main__':raise SystemExit(main())
