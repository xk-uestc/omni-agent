"""Real forty-table distractor and field-grounding evaluation, no model few-shots."""
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ict-track8'))
from backend.nl2sql.engine import Nl2SqlEngine


def main():
    output=ROOT/'runtime/schema-scale';output.mkdir(parents=True,exist_ok=True)
    source=ROOT/'benchmarks/chinook/Chinook.sqlite'
    results=[]
    for total in (11,40,80):
        target=output/f'chinook-{total}-tables.sqlite'
        if not target.exists():
            with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as original, sqlite3.connect(target) as connection:
                original.backup(connection)
                for i in range(total-11):
                    connection.execute(f'CREATE TABLE Distractor{i}(Id INTEGER PRIMARY KEY, Name TEXT, Country TEXT, Amount REAL, Total REAL, Date TEXT)')
                    connection.executemany(f'INSERT INTO Distractor{i} VALUES(?,?,?,?,?,?)',[(j,f'Decoy_{i}_{j}','NV',i*100+j,i*10+j,'2025-01-01') for j in range(50)])
        engine=Nl2SqlEngine(target,metric_catalog_path=output/'no_catalog.json')
        with engine._connect() as connection:
            tables=engine.introspector.introspect(connection)
        cases=[('按Genre Name统计Track Milliseconds的平均值',{'Genre.Name','Track.Milliseconds'},'SELECT g.Name,AVG(t.Milliseconds) FROM Track t JOIN Genre g ON t.GenreId=g.GenreId GROUP BY g.Name'),
               ('按Customer City统计Invoice Total',{'Customer.City','Invoice.Total'},'SELECT c.City,SUM(i.Total) FROM Invoice i JOIN Customer c ON i.CustomerId=c.CustomerId GROUP BY c.City'),
               ('Invoice Total按BillingCountry排名',{'Invoice.BillingCountry','Invoice.Total'},'SELECT BillingCountry,SUM(Total) FROM Invoice GROUP BY BillingCountry')]
        for question,expected_fields,gold_sql in cases:
            started=time.perf_counter()
            linked=engine.planner.linker.link(question,tables)
            observed_fields={hit.table+'.'+hit.column for hit in linked}
            answer=engine.answer(question)
            with engine._connect() as c:
                gold=c.execute(gold_sql).fetchall()
            observed=[tuple(row[column] for column in answer.columns) for row in answer.rows]
            if '排名' in answer.columns:
                columns=[column for column in answer.columns if column!='排名']
                observed=[tuple(row[column] for column in columns) for row in answer.rows]
            equal=lambda rows:sorted([tuple(round(x,6) if type(x) in (int,float) else x for x in row) for row in rows],key=repr)
            passed=observed_fields==expected_fields and answer.status=='ok' and equal(gold)==equal(observed)
            results.append({'tables':len(tables),'question':question,'expected_fields':sorted(expected_fields),'observed_fields':sorted(observed_fields),
                            'pass':passed,'status':answer.status,'latency_ms':round((time.perf_counter()-started)*1000,3)})
            print(json.dumps({'tables':total,'pass':passed,'status':answer.status},ensure_ascii=False),flush=True)
    report={'created_at':datetime.now(timezone.utc).isoformat(),'scope':'public_db_with_synthetic_schema_distractors',
            'few_shot_examples':0,'cases':results,'passed':sum(row['pass'] for row in results),'total':len(results)}
    (ROOT/'docs/SCHEMA_SCALE_REPORT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(row['pass'] for row in results) else 1


if __name__=='__main__':
    raise SystemExit(main())
