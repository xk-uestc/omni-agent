"""Actual SQLite writer/read-only-reader audit, no model and no production DB."""
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine


def fingerprint(path):
    result = {}
    for label, candidate in [('database',path),('wal',Path(str(path)+'-wal'))]:
        if candidate.exists():
            stat = candidate.stat()
            result[label] = [stat.st_size,stat.st_mtime_ns]
    return result


def summarize(result):
    return {'status':result.status,'rows':result.rows,'parameters':result.parameters,
            'clarification_code':result.clarification_code,'sql':result.sql}


def audit(parent):
    records = []
    with closing(sqlite3.connect(parent/'live.sqlite')) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.executescript('CREATE TABLE sales_orders(order_id TEXT PRIMARY KEY,region TEXT,order_date INTEGER,sales_amount REAL);')
        writer.execute("INSERT INTO sales_orders VALUES('A','华东',1735689600,10)")
        writer.commit()
        writer.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        engine = Nl2SqlEngine(parent/'live.sqlite')
        before = engine.answer('2025年华东地区销售额')
        metadata = fingerprint(engine.database_path)
        writer.execute("INSERT INTO sales_orders VALUES('B','华中',1740960000,27)")
        writer.commit()
        after = engine.answer('2025年华中地区销售额')
        records.append({'id':'wal_new_value','before':summarize(before),'after':summarize(after),
            'before_file_state':metadata,'after_file_state':fingerprint(engine.database_path),
            'expected':27,'passed':after.status=='ok' and after.rows[0].get('销售额')==27})
        writer.execute('DELETE FROM sales_orders')
        writer.execute("INSERT INTO sales_orders VALUES('C','华东',1735689600000,42)")
        writer.commit()
        result = engine.answer('2025年华东地区销售额')
        records.append({'id':'wal_date_seconds_to_milliseconds','after':summarize(result),
            'expected':42,'passed':result.status=='ok' and result.rows[0].get('销售额')==42 and result.parameters[:2]==(1735689600000,1767225600000)})
        # A completed query must not keep its read transaction and block checkpointing.
        state = writer.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        records.append({'id':'query_releases_read_snapshot','checkpoint':state,'passed':state[0]==0})
    path = parent/'aliases.json'
    path.write_text(json.dumps([{'table':'sales_orders','column':'region','value':'华东','aliases':['东区']}],ensure_ascii=False),encoding='utf-8')
    engine = Nl2SqlEngine(parent/'live.sqlite',value_aliases_path=path)
    first = engine.answer('2025年东区销售额')
    path.write_text(json.dumps([{'table':'sales_orders','column':'region','value':'华东','aliases':['东区','东方区']}],ensure_ascii=False),encoding='utf-8')
    second = engine.answer('2025年东方区销售额')
    records.append({'id':'live_business_alias_refresh','before':summarize(first),'after':summarize(second),
        'expected':42,'passed':second.status=='ok' and second.rows[0].get('销售额')==42})
    unusual = parent/'含#和%20的业务库.sqlite'
    with closing(sqlite3.connect(unusual)) as connection:
        connection.executescript("CREATE TABLE sales_orders(order_id TEXT PRIMARY KEY,sales_amount REAL); INSERT INTO sales_orders VALUES('A',19);")
        connection.commit()
    try:
        result = Nl2SqlEngine(unusual).answer('销售额')
        passed = result.status=='ok' and result.rows[0].get('销售额')==19
        detail = summarize(result)
    except Exception as exc:
        passed, detail = False, {'error_type':type(exc).__name__}
    records.append({'id':'sqlite_uri_reserved_filename','after':detail,'expected':19,'passed':passed})
    return records


def main():
    with tempfile.TemporaryDirectory(prefix='ict8-live-sql-') as directory:
        records = audit(Path(directory))
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'actual_temporary_sqlite_change_audit_not_blind_query_accuracy',
        'model_called':False,'passed':sum(row['passed'] for row in records),'total':len(records),'cases':records}
    first = ROOT/'docs/LIVE_SQL_FIRST_RUN.json'
    if not first.exists():
        first.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'docs/LIVE_SQL_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'total':report['total'],
                     'cases':[{ 'id':r['id'],'passed':r['passed']} for r in records]},ensure_ascii=False))
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
