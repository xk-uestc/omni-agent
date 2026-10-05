"""Isolated live gpt-6-luna dialogue acceptance; developer fixtures, not a benchmark."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.knowledge_store import KnowledgeStore


def main():
    case=ROOT/'runtime'/('dialogue-sources-'+uuid.uuid4().hex)
    case.mkdir()
    db=case/'business.sqlite'
    with sqlite3.connect(db) as con:
        con.executescript('''CREATE TABLE customers(id INTEGER PRIMARY KEY,name TEXT,region TEXT);
        CREATE TABLE payments(id INTEGER PRIMARY KEY,customer_id INTEGER REFERENCES customers(id),amount REAL,channel TEXT,created_at TEXT);
        INSERT INTO customers VALUES(1,'甲','华东'),(2,'乙','华南'),(3,'丙','华东');
        INSERT INTO payments VALUES(1,1,10,'线上','2025-02-01'),(2,1,50,'线下','2024-03-01'),
        (3,2,30,'线上','2025-03-01'),(4,2,80,'线下','2024-04-01'),(5,2,20,'线下','2024-06-01'),(6,3,100,'线上','2025-01-01');''')
    knowledge=KnowledgeStore(case/'knowledge')
    for identifier,title,period,days in [('alpha','甲产品说明',24,7),('beta','乙产品说明',36,14)]:
        knowledge.ingest(f'保修期限为{period}个月。退货期限为{days}天。'.encode(),document_id=identifier,
            title=title,modality='txt',filename=identifier+'.txt')
    files=['backend/conversation_sources.py','backend/document_dialogue.py','backend/relational_scope_edit.py',
        'backend/session.py','backend/omni_agent.py']
    hashes={f:hashlib.sha256((ROOT/'ict-track8'/f).read_bytes()).hexdigest() for f in files}
    env=os.environ.copy()
    env.update({'ICT8_DB_PATH':str(db),'ICT8_KNOWLEDGE_ROOT':str(case/'knowledge'),
        'ICT8_SESSION_DB':str(case/'sessions.sqlite'),'ICT8_DENSE_MODEL_PATH':'','ICT8_SCHEMA_ALIASES':'',
        'PYTHONIOENCODING':'utf-8'})
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    session='source-stress-'+uuid.uuid4().hex
    process=None;log=None;records=[];pids=[]
    def stop():
        nonlocal process,log
        if process is not None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
            process=None
        if log is not None:log.close();log=None
    def start():
        nonlocal process,log
        log=(case/f'server-{len(pids)+1}.log').open('wb')
        process=subprocess.Popen([sys.executable,str(ROOT/'tools/run_server.py'),'--port',str(port),'--with-model'],
            cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        pids.append(process.pid)
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('isolated server exited; inspect its local log')
            try:
                with urlopen(base+'/health',timeout=1) as response:
                    if response.status==200:return
            except OSError:pass
            time.sleep(.25)
        raise RuntimeError('isolated server did not become ready')
    def ask(question,check,*,sid=session,reset=False):
        request=Request(base+'/api/v1/omni/query',data=json.dumps({'question':question,'session_id':sid,
            'reset_context':reset},ensure_ascii=False).encode(),headers={'Content-Type':'application/json'})
        with urlopen(request,timeout=300) as response:data=json.load(response)
        passed=bool(check(data))
        records.append({'question':question,'passed':passed,'response':data})
        print(json.dumps({'turn':len(records),'status':data['status'],'passed':passed},ensure_ascii=False),flush=True)
        return data
    def doc(identifier,value):
        return lambda data:data.get('status')=='ok' and value in data.get('result',{}).get('answer','') and {
            h['metadata']['document_id'] for h in data['result'].get('citations',[])}=={identifier}
    def rejected(data):return data.get('status')=='clarification' and not data.get('result',{}).get('rows')
    try:
        start()
        first=ask('2025年payments.created_at，customers.region=华东且payments.channel=线上，按customers.name分组，统计payments.amount合计和payments.id记录数',
            lambda data:data.get('status')=='ok' and bool(data.get('result',{}).get('sql')))
        if first['status']!='ok':
            return 1
        original_sql=first['result']['sql']
        def expected(region,channel,year):
            from contextlib import closing
            with closing(sqlite3.connect(db)) as reference:
                return [{'name':n,'total':v,'n':c} for n,v,c in reference.execute(
                    'SELECT c.name,SUM(p.amount),COUNT(p.id) FROM payments p JOIN customers c ON p.customer_id=c.id '
                    'WHERE c.region=? AND p.channel=? AND p.created_at>=? AND p.created_at<? GROUP BY c.name ORDER BY c.name',
                    (region,channel,f'{year}-01-01',f'{year+1}-01-01'))]
        def sql_check(region,channel,year):
            gold=expected(region,channel,year)
            def check(data):
                result=data.get('result',{});rows=result.get('rows',[])
                # Aliases belong to the model; compare ordered numeric/name cells
                # independently and assert exact SQL bytes for every edit.
                observed=[(next(v for v in row.values() if isinstance(v,str)),sorted(v for v in row.values() if isinstance(v,(int,float)))) for row in rows]
                wanted=[(row['name'],sorted([row['total'],row['n']])) for row in gold]
                return data.get('status')=='ok' and sorted(observed)==sorted(wanted) and result.get('sql')==original_sql
            return check
        ask('customers.region改成华南，payments.channel改成线下，时间改成2024年',sql_check('华南','线下',2024))
        ask('customers.region改成华东，payments.channel改成无效',rejected)
        ask('payments.channel改成线上，时间改成2025年',sql_check('华南','线上',2025))
        first_doc=ask('查询文档“甲产品说明”：保修期限是多少',doc('alpha','24'))
        first_id=first_doc.get('document_reference_id','')
        ask('同样的问题换成文档“乙产品说明”',doc('beta','36'))
        pending=ask('之前那份文档的退货期限是多少',lambda d:rejected(d) and len(d['result'].get('clarification_options',[]))==2)
        ask('选第99个',rejected)
        ask('选第一个',doc('alpha','7'))
        # Document return is tested beyond the 8-turn planning window. Reuse
        # the successful SQL source through a previously offered reference.
        sql_source=first.get('query_reference_id','')
        selection=ask('它呢',lambda d:rejected(d) and len(d['result'].get('clarification_options',[]))>=3)
        offered=selection.get('result',{}).get('clarification_options',[])
        chosen=next((o for o in offered if o['value']==sql_source),None)
        if chosen:
            replay=ask(chosen['question'],lambda d:d.get('status')=='ok')
            original_sql=replay.get('result',{}).get('sql',original_sql)
            for index in range(24):
                region=('华南','华东')[index%2];channel=('线下','线上')[index//2%2];year=2024+index//4%2
                ask(f'customers.region改成{region}，payments.channel改成{channel}，时间改成{year}年',sql_check(region,channel,year))
        else:
            records.append({'question':'return SQL via offered reference','passed':False})
        stop();start()
        ask(f'回到文档查询编号{first_id}，退货期限是多少',doc('alpha','7'))
        ask(f'回到文档查询编号{first_id}，保修期限是多少',rejected,sid='another-session')
        stop()
        knowledge.ingest('保修期限为48个月。退货期限为21天。'.encode(),document_id='alpha',title='甲产品说明',modality='txt',filename='new.txt')
        start()
        ask(f'回到文档查询编号{first_id}，退货期限是多少',rejected)
        ask('查询文档“甲产品说明”：退货期限是多少',doc('alpha','21'))
        ask(f'回到文档查询编号{first_id}，保修期限是多少',rejected,reset=True)
        ask('查询文档“甲产品说明”：退货期限是多少',doc('alpha','21'))
        ask('查询文档“缺失的文档”：保修期限是多少',rejected)
        ask('它的保修期限是多少',lambda d:rejected(d) and len(d['result'].get('clarification_options',[]))==1)
        ask('选第一个',doc('alpha','48'))
    finally:
        stop()
        report={'not_official_benchmark':True,'model_allowed':'gpt-6-luna','source_hashes':hashes,
            'planned_turns':44,'all_planned_executed':len(records)==44,
            'source_stable':all(hashlib.sha256((ROOT/'ict-track8'/f).read_bytes()).hexdigest()==h for f,h in hashes.items()),
            'independent_server_pids':pids,'passed':sum(r['passed'] for r in records),'total':len(records),'turns':records}
        path=case/'http-report.json'
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'report':str(path),'passed':report['passed'],'total':report['total']},ensure_ascii=False),flush=True)
    return 0 if report['source_stable'] and all(r['passed'] for r in records) else 1


if __name__=='__main__':raise SystemExit(main())
