"""Independent definition/data-boundary probes; gold is scorer-only.

These supplemental authored probes do not modify the frozen 185-turn set.
No external model or paid API is used.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / 'benchmarks/context_semantics_20261009'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', required=True)
    parser.add_argument('--implementation-root', type=Path, default=ROOT/'ict-track8')
    args = parser.parse_args()
    output = BENCH / f'{args.label}.json'
    if output.exists():
        parser.error('Preserve prior evidence: choose a new label')
    implementation = args.implementation_root.resolve()
    sys.path.insert(0, str(implementation))
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.knowledge_store import KnowledgeStore
    from backend.session import ConversationStore
    from backend.omni_agent import OmniAgent
    from evaluate_context_semantics_20261009 import score, input_population
    work = ROOT/'runtime/context-semantics-20261009-routing-audit'
    database = work/'rich.sqlite'
    before = {p.relative_to(implementation).as_posix(): sha(p)
              for p in (implementation/'backend').rglob('*.py')}
    db_before = sha(database)
    engine = Nl2SqlEngine(database, reference_date=date(2026, 10, 9),
        metric_catalog_path=BENCH/'demo_metric_catalog-frozen.json',
        result_artifact_dir=ROOT/'runtime'/f'context-boundary-{args.label}-results')
    agent = OmniAgent(engine, KnowledgeStore(work/'knowledge'), ConversationStore())
    definitions = ['本金是什么','本钱是什么','利润是什么','成本是什么','营收是什么','工资是什么',
                   '本金怎么算','本钱怎么计算','成本怎么计算',
                   '查询文档《财务术语手册》，本金是什么',
                   '《财务术语手册》中本钱是什么','财务术语手册中工资发了多少钱',
                   '2025年财务术语手册中本金是多少',
                   '2025年各设备类型网页浏览量的指标定义']
    probes = [dict(question=q, kind='document_no_sql') for q in definitions]
    probes += [dict(question='2025年华东净利润是多少', kind='net_profit_safety')]
    stats = [
        ('2025年各地区销售额排名', 'sales_orders','sales_amount','order_date','region',None,None),
        ('2025年销售额最高的地区是什么','sales_orders','sales_amount','order_date','region',None,1),
        ('2025年华东工资发了多少钱','payroll_records','salary_amount','payroll_date',None,'华东',None),
        ('2025年华东发了多少工资','payroll_records','salary_amount','payroll_date',None,'华东',None),
        ('2025年华东发出去的工资','payroll_records','salary_amount','payroll_date',None,'华东',None),
        ('2025年各设备类型网页浏览量','web_traffic_daily','page_views','visit_date','device_type',None,None),
    ]
    for q, table, metric, day, group, region, limit in stats:
        expression=f'SUM({metric})'
        # The requested ranking may expose an explicit rank column. Its gold
        # is independent SQL, not a projection copied from production output.
        rank=f',DENSE_RANK() OVER (ORDER BY {expression} DESC)' if '排名' in q else ''
        gold='SELECT '+(f'{group},' if group else '')+expression+rank+f' FROM {table}'
        gold+=f" WHERE {day}>='2025-01-01' AND {day}<'2026-01-01'"
        if region: gold+=f" AND region='{region}'"
        if group: gold+=f' GROUP BY {group} ORDER BY {expression} DESC'
        if limit: gold+=f' LIMIT {limit}'
        probes.append(dict(question=q, kind='statistic', gold_sql=gold,
            metric_fields=[metric], dimension_fields=[group] if group else [],
            expected_status=None, expected_filters=[]))
    observations=[]
    for i, probe in enumerate(probes):
        start=time.monotonic()
        response=agent.query(probe['question'],session_id=f'boundary-{i}')
        elapsed=round((time.monotonic()-start)*1000,3)
        result=response.get('result') or {}
        if probe['kind']=='document_no_sql':
            # Definition evidence may be insufficient, but executing a numeric
            # SQL answer or rewriting an unknown definition to cost is unsafe.
            passed=(response.get('route')=='document' and not result.get('sql')
                    and not response.get('language_normalization',{}).get('rewrites'))
            evidence={'expected_route':'document','sql_executed':bool(result.get('sql'))}
        elif probe['kind']=='net_profit_safety':
            passed=response.get('status')!='ok' and not result.get('sql')
            evidence={'expected':'clarify unavailable net-profit definition instead of using gross profit'}
        else:
            passed,evidence=score(probe,response,database)
            if (not passed and probe.get('gold_sql') and result.get('sql')
                    and all(evidence.get(k) for k in ('field_pass','dimension_pass','result_pass'))
                    and not evidence.get('source_filter_population_pass')):
                ast=sqlglot.parse_one(result['sql'],read='sqlite')
                source=ast.args.get('from_')
                subtree=source.this.this if source and isinstance(source.this,exp.Subquery) else None
                # Only unwrap the known rank wrapper: aggregation lives in
                # one inner SELECT; outer filtering may refer only to its
                # rank output. Raw source filters remain independently checked.
                rank_aliases={item.alias for item in subtree.expressions
                              if isinstance(item,exp.Alias) and item.find(exp.DenseRank)} if subtree else set()
                outer_where=ast.args.get('where')
                outer_columns={c.name for c in outer_where.find_all(exp.Column)} if outer_where else set()
                if (subtree is not None and rank_aliases and outer_columns<=rank_aliases
                        and not ast.args.get('joins')):
                    bind=result.get('parameters',[])[:sum(1 for _ in subtree.find_all(exp.Placeholder))]
                    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as connection:
                        actual=input_population(connection,subtree.sql(dialect='sqlite'),bind)
                        expected=input_population(connection,probe['gold_sql'])
                    passed=actual is not None and actual==expected
                    evidence['rank_wrapper_population_pass']=passed
                    evidence['actual_source_table']=actual[0] if actual else None
                    evidence['actual_contributing_records']=len(actual[1]) if actual else None
            passed=passed and response.get('route')=='sql'
        observations.append({'question':probe['question'],'kind':probe['kind'],
            'pass':passed,'wall_ms':elapsed,'evidence':evidence,'response':response})
        print(json.dumps({'question':probe['question'],'pass':passed,'route':response.get('route'),
            'status':response.get('status'),'effective_question':response.get('effective_question'),
            'rows':result.get('rows')},ensure_ascii=False),flush=True)
    after = {p.relative_to(implementation).as_posix(): sha(p)
             for p in (implementation/'backend').rglob('*.py')}
    report={'label':args.label,'created_at':datetime.now(timezone.utc).isoformat(),
        'implementation_root':str(implementation),'type':'authored supplementary boundary audit',
        'external_api_used':False,'total':len(observations),
        'passed':sum(o['pass'] for o in observations),'source_stable':before==after,
        'database_stable':db_before==sha(database),'source_before':before,'source_after':after,
        'cases':observations}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(output),'passed':report['passed'],'total':report['total'],
        'source_stable':report['source_stable'],'database_stable':report['database_stable']},ensure_ascii=False))
    return 0 if report['passed']==report['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
