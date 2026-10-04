"""Fresh randomized schemas with actual SQL execution; references scorer-only.

Authored first-run stress probes, not an independent blind benchmark.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'ict-track8'))
from model_runtime import enable_local_model, local_model_headers
from backend.nl2sql.engine import Nl2SqlEngine
from backend.nl2sql.responses_provider import ResponsesModelPlanProvider
from backend.nl2sql.security import SqlSafetyError
from backend.responses_client import GenerationError


def hashes():
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT/'ict-track8/backend').rglob('*.py'))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().parent != (ROOT/'docs').resolve() or args.output.exists():
        parser.error('New report directly in docs required')
    enable_local_model('gpt-6-luna')
    before = hashes()
    directory = ROOT/'runtime'/('round9-schema-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.mkdir()
    suffix = secrets.token_hex(3)
    entities, events, deliveries = ('entities_'+suffix, 'events_'+suffix, 'deliveries_'+suffix)
    key, owner, amount = ('key_'+suffix, 'owner_'+suffix, 'amount_'+suffix)
    database = directory/'source.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript(f'''
        CREATE TABLE {entities}({key} INTEGER PRIMARY KEY,name TEXT);
        CREATE TABLE {events}({key} TEXT PRIMARY KEY,{owner} INTEGER REFERENCES {entities}({key}),
            {amount} REAL, batch TEXT, observed_at TEXT);
        CREATE TABLE {deliveries}(id INTEGER PRIMARY KEY,{owner} INTEGER REFERENCES {entities}({key}));
        INSERT INTO {entities} VALUES(1,'alpha'),(2,'beta'),(3,'empty');
        INSERT INTO {events} VALUES
            ('p1',1,10,'x','2030-01-01T01:00:00'),
            ('p2',1,30,'x','2030-01-01 23:00:00'),
            ('p3',1,20,'y','2030-01-02T02:00:00'),
            ('p4',2,5,NULL,'2030-01-02 02:00:00'),
            (NULL,NULL,100,'z','2030-01-03 00:00:00');
        INSERT INTO {deliveries} VALUES(1,1),(2,1),(3,1),(4,2);
        ''')
    database_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    cases = [
        ('nullable_text_pk', f'查询{events}的记录总数、{key}非空记录数和{key}为空的记录数。',
         f'SELECT COUNT(*),COUNT({key}),SUM(CASE WHEN {key} IS NULL THEN 1 ELSE 0 END) FROM {events}'),
        ('mixed_time_latest', f'对{events}每个{owner}取observed_at最新记录，包括{owner}为空的分组。'
         f'observed_at是日期时间；T和空格都代表同一时区的时间分隔符，按实际时间比较。'
         f'同一时刻按{key}最大决胜，只返回{key}、{owner}、{amount}，按{owner}升序。',
         f'WITH x AS(SELECT *,ROW_NUMBER() OVER(PARTITION BY {owner} ORDER BY JULIANDAY(observed_at) DESC,{key} DESC) rn '
         f'FROM {events}) SELECT {key},{owner},{amount} FROM x WHERE rn=1 ORDER BY {owner}'),
        ('group_population', f'先按{events}.{owner}合计{amount}（包括空值分组），然后只返回该合计高于'
         f'前一步所有分组合计平均值的{owner}和合计，按{owner}升序。不加入{entities}的无记录实体。',
         f'WITH x AS(SELECT {owner},SUM({amount}) v FROM {events} GROUP BY {owner}) '
         f'SELECT {owner},v FROM x WHERE v>(SELECT AVG(v) FROM x) ORDER BY {owner}'),
        ('nested_batch_average', f'先按{events}.{owner}和batch合计{amount}，再按{owner}求这些批次合计的平均值，'
         f'包括空值分组，按{owner}升序。',
         f'WITH x AS(SELECT {owner},batch,SUM({amount}) v FROM {events} GROUP BY {owner},batch) '
         f'SELECT {owner},AVG(v) FROM x GROUP BY {owner} ORDER BY {owner}'),
        ('anti_join', f'列出{entities}没有任何{events}记录的{key}和name。'
         f'关联{events}.{owner}={entities}.{key}，按{entities}.{key}升序。',
         f'SELECT e.{key},e.name FROM {entities} e WHERE NOT EXISTS '
         f'(SELECT 1 FROM {events} x WHERE x.{owner}=e.{key}) ORDER BY e.{key}'),
        ('fanout', f'按{entities}.{key}分别统计{events}记录数和{deliveries}记录数，'
         f'分别以各自{owner}关联{entities}.{key}并独立聚合，不能扇出，不能用COUNT({events}.{key})'
         f'漏掉空主键记录；没有记录时为0，按{entities}.{key}升序。',
         f'WITH x AS(SELECT {owner},COUNT(*) n FROM {events} GROUP BY {owner}),'
         f'y AS(SELECT {owner},COUNT(*) n FROM {deliveries} GROUP BY {owner}) '
         f'SELECT e.{key},COALESCE(x.n,0),COALESCE(y.n,0) FROM {entities} e '
         f'LEFT JOIN x ON x.{owner}=e.{key} LEFT JOIN y ON y.{owner}=e.{key} ORDER BY e.{key}'),
    ]
    stop = threading.Event()
    def execute(case):
        identifier, question, reference = case
        if stop.is_set():
            return {'id':identifier,'pass':False,'status':'not_run'}
        if hashes() != before:
            raise ValueError('source_changed')
        provider = ResponsesModelPlanProvider(os.environ['ICT8_OPENAI_BASE_URL'], os.environ['ICT8_OPENAI_API_KEY'],
            model='gpt-6-luna', reasoning_effort='medium', http_headers=local_model_headers(), max_retries=0)
        engine = Nl2SqlEngine(database, model_plan_provider=provider, metric_catalog_path=directory/'none.json')
        try:
            result = engine.answer(question).to_dict()
            with sqlite3.connect(database) as db:
                # Reference is first opened by scoring AFTER the production answer.
                expected = [list(r) for r in db.execute(reference)]
                replay = [list(r) for r in db.execute(result['sql'],result['parameters'])] if result.get('sql') else None
            actual = [[r[c] for c in result['columns']] for r in result['rows']]
            audits = provider.audit_history
            passed = result['status']=='ok' and actual==expected==replay and bool(audits) and all(
                a.get('status')=='completed' and a.get('model_verified') is True for a in audits)
            return {'id':identifier,'question':question,'pass':passed,'result':result,
                    'actual_rows':actual,'reference_rows_scoring_only':expected,'api_audits':audits}
        except (GenerationError, SqlSafetyError) as exc:
            if isinstance(exc, GenerationError) and exc.status in (401,403):
                stop.set()
            return {'id':identifier,'question':question,'pass':False,'error_type':type(exc).__name__,
                    'api_audits':provider.audit_history}
    with ThreadPoolExecutor(max_workers=2) as pool:
        observations = list(pool.map(execute, cases))
    after = hashes()
    stable = before==after and database_hash==hashlib.sha256(database.read_bytes()).hexdigest()
    report = {'created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'authored_fresh_random_schema_first_run_stress_probes_not_independent_blind_benchmark',
        'model':'gpt-6-luna','reasoning':'medium','reference_used_as_model_input':False,
        'database_sha256':database_hash,'implementation_file_sha256_start':before,
        'implementation_file_sha256_end':after,'implementation_stable':stable,
        'total':len(cases),'passed':sum(r['pass'] for r in observations),'cases':observations,
        'limitations':['Six tiny SQLite cases with explicit field definitions; not unknown business semantics or other database engines.']}
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'passed':report['passed'],'total':report['total'],'stable':stable}))
    return 0 if stable and report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
