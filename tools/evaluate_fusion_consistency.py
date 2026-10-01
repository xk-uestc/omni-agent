"""Actual mid-plan writer/source changes in temporary data; no API calls."""
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.dependency_agent import DependencyAgent
from backend.knowledge_store import KnowledgeStore
from backend.nl2sql.engine import Nl2SqlEngine

RAW = '平均订单金额 = 销售额 / 订单数'.encode()
PLAN = [
    {'id':'formula','tool':'document_formula','args':{'document_id':'formula','label':'平均订单金额'}},
    {'id':'sales','tool':'sql','args':{'question':'2025年华东销售额'}},
    {'id':'count','tool':'sql','args':{'question':'2025年华东订单数'}},
    {'id':'result','tool':'calculate','args':{'formula':{'ref':'formula','path':[]},'parameters':{
        '销售额':{'ref':'sales','path':['rows',0,'销售额']},'订单数':{'ref':'count','path':['rows',0,'订单数']}}}},
]


def scenario(parent,kind):
    parent.mkdir()
    with closing(sqlite3.connect(parent/'business.sqlite')) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.executescript("CREATE TABLE sales_orders(order_id TEXT PRIMARY KEY,region TEXT,order_date TEXT,sales_amount REAL); INSERT INTO sales_orders VALUES('A','华东','2025-01-01',20),('B','华东','2025-02-01',40);")
        writer.commit()
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        store = KnowledgeStore(parent/'knowledge')
        store.ingest(RAW,document_id='formula',title='订单金额定义',modality='txt',filename='formula.txt')
        source,_ = store.original('formula')
        engine = Nl2SqlEngine(parent/'business.sqlite')
        changed = []
        def intervene(event):
            if kind=='writer_between_sql' and event['task_id']=='sales':
                writer.execute("INSERT INTO sales_orders VALUES('C','华东','2025-03-01',90)")
                writer.commit()
                changed.append(True)
            if event['task_id']=='formula':
                if kind=='tampered_after_formula':
                    source.write_bytes(b'tampered original')
                    changed.append(True)
                elif kind=='missing_after_formula':
                    source.rename(source.with_suffix('.held'))
                    changed.append(True)
                elif kind=='reingested_after_formula':
                    store.ingest('平均订单金额 = 销售额 * 2 / 订单数'.encode(),document_id='formula',title='新口径',modality='txt',filename='formula.txt')
                    changed.append(True)
        result = DependencyAgent(engine,store).run(PLAN,on_event=intervene)
        actual = result.get('results',{}).get('result',{}).get('value')
        expected_stop = kind in {'tampered_after_formula','missing_after_formula','reingested_after_formula'}
        passed = (result['status']=='incomplete' and 'result' not in result['results']) if expected_stop else result['status']=='ok' and actual==30
        checkpoint = writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]
        followup = engine.answer('2025年华东销售额和订单数')
        expected_sum,expected_count = (150,3) if kind=='writer_between_sql' else (60,2)
        refreshed = followup.status=='ok' and followup.rows[0]['销售额']==expected_sum and followup.rows[0]['订单数']==expected_count
        return {'id':kind,'passed':passed and checkpoint==0 and refreshed,
            'status':result['status'],'actual_value':actual,'expected_value':None if expected_stop else 30,
            'expected_stop':expected_stop,'failed_task':result.get('failed_task'),
            'error_code':result.get('error_code'),'mutation_executed':bool(changed),
            'read_transaction_released':checkpoint==0,'next_query_sees_latest_commit':refreshed}


def main():
    kinds = ['stable_plan','writer_between_sql','tampered_after_formula','missing_after_formula','reingested_after_formula']
    with tempfile.TemporaryDirectory(prefix='ict8-fusion-consistency-') as directory:
        records = [scenario(Path(directory)/kind,kind) for kind in kinds]
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'actual_mid_plan_mutation_development_audit_not_blind_model_accuracy',
        'model_called':False,'passed':sum(r['passed'] for r in records),'total':len(records),'cases':records}
    first = ROOT/'docs/FUSION_CONSISTENCY_FIRST_RUN.json'
    if not first.exists():
        first.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'docs/FUSION_CONSISTENCY_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
