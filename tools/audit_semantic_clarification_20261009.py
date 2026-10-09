"""Independent semantic-clarification scope audit, with real SQLite gold.

Supplemental probes are distinct from the frozen 185-turn regression.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
BENCH=ROOT/'benchmarks/context_semantics_20261009'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gold(field, year=None, region=None):
    table='finance_loans' if field=='loan_amount' else 'sales_orders'
    day='loan_date' if table=='finance_loans' else 'order_date'
    query=f'SELECT SUM({field}) FROM {table}'
    conditions=[]
    if year:conditions.append(f"{day}>='{year}-01-01' AND {day}<'{year+1}-01-01'")
    if region:conditions.append(f"region='{region}'")
    return query+(' WHERE '+' AND '.join(conditions) if conditions else '')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label',required=True)
    parser.add_argument('--implementation-root',type=Path,default=ROOT/'ict-track8')
    parser.add_argument('--policy',choices=('original-conservative','explicit-new-topic'),
        default='original-conservative',help='Keep original exploratory gold or explicitly reset domain before ambiguous terms')
    parser.add_argument('--freeze-only',action='store_true')
    args=parser.parse_args()
    output=BENCH/f'{args.label}.json'
    if output.exists():parser.error('Preserve prior evidence: choose a new label')
    implementation=args.implementation_root.resolve()
    sys.path.insert(0,str(implementation))
    from backend.nl2sql.engine import Nl2SqlEngine
    from backend.knowledge_store import KnowledgeStore
    from backend.session import ConversationStore
    from backend.omni_agent import OmniAgent
    from evaluate_context_semantics_20261009 import score
    fixture=ROOT/'runtime/context-semantics-20261009-run-candidate-3'
    database=fixture/'mixed.sqlite'
    work=ROOT/'runtime'/f'context-clarification-{args.label}'
    agent=OmniAgent(Nl2SqlEngine(database,aliases_path=fixture/'mixed-aliases.json',
        reference_date=date(2026,10,9),metric_catalog_path=fixture/'no-catalog.json',
        result_artifact_dir=work/'results'),KnowledgeStore(work/'knowledge'),ConversationStore())
    before={p.relative_to(implementation).as_posix():digest(p)
            for p in (implementation/'backend').rglob('*.py')}
    db_before=digest(database)
    cases=[]
    def add(session,question,field=None,year=None,region=None,reset=False,clarify=False):
        cases.append(dict(session=session,question=question,reset_context=reset,
            expected_status='clarification' if clarify else None,
            gold_sql=gold(field,year,region) if field else None,
            metric_fields=[field] if field else [],dimension_fields=[]))
    for choice,field in [('成本','cost_amount'),('借款本金','loan_amount')]:
        sid=f'new-{field}'
        add(sid,'2025年华东本金是多少',clarify=True)
        add(sid,choice,field,2025,'华东')
        sid=f'previous-{field}'
        add(sid,'2024年华南销售额','sales_amount',2024,'华南')
        independent='换个主题，2025年华东本金是多少' if args.policy=='explicit-new-topic' else '2025年华东本金是多少'
        add(sid,independent,clarify=True)
        add(sid,choice,field,2025,'华东')
    add('abandon','2025年华东本金是多少',clarify=True)
    add('abandon','换个主题，2024年华南销售额','sales_amount',2024,'华南')
    add('abandon','成本','cost_amount',2024,'华南')
    add('reset','2025年华东本金是多少',clarify=True)
    add('reset','成本','cost_amount',reset=True)
    add('reset','2025年华东销售额','sales_amount',2025,'华东')
    add('isolation-a','2025年华东本金是多少',clarify=True)
    add('isolation-b','成本','cost_amount')
    add('isolation-a','成本','cost_amount',2025,'华东')
    add('switch-canonical','2025年华东本金是多少',clarify=True)
    add('switch-canonical','2024年华南销售额','sales_amount',2024,'华南')
    add('switch-canonical','成本','cost_amount',2024,'华南')
    input_text=json.dumps(cases,ensure_ascii=False,sort_keys=True)
    input_sha=hashlib.sha256(input_text.encode('utf-8')).hexdigest()
    frozen=BENCH/f'clarification-cases-{args.policy}.json'
    if frozen.exists():
        saved=json.loads(frozen.read_text(encoding='utf-8'))
        if saved['input_sha256']!=input_sha or saved['cases']!=cases:
            raise RuntimeError('Supplemental frozen input changed; preserve it and version a new policy')
    else:
        frozen.write_text(json.dumps({'input_policy':args.policy,'input_sha256':input_sha,
            'total':len(cases),'cases':cases},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if args.freeze_only:
        print(json.dumps({'input':str(frozen),'total':len(cases),'input_sha256':input_sha}))
        return 0
    observations=[]
    for case in cases:
        start=time.monotonic()
        response=agent.query(case['question'],session_id=case['session'],
                             reset_context=case['reset_context'])
        wall_ms=round((time.monotonic()-start)*1000,3)
        passed,evidence=score(case,response,database)
        if case['expected_status']:
            passed=passed and not (response.get('result') or {}).get('sql')
        observations.append({**case,'pass':passed,'wall_ms':wall_ms,
            'evidence':evidence,'response':response})
        print(json.dumps({'session':case['session'],'question':case['question'],
            'pass':passed,'status':response.get('status'),
            'effective_question':response.get('effective_question'),
            'rows':(response.get('result') or {}).get('rows')},ensure_ascii=False),flush=True)
    after={p.relative_to(implementation).as_posix():digest(p)
           for p in (implementation/'backend').rglob('*.py')}
    report={'label':args.label,'created_at':datetime.now(timezone.utc).isoformat(),
        'implementation_root':str(implementation),'external_api_used':False,
        'input_policy':args.policy,'input_sha256':input_sha,'input_file':str(frozen),
        'type':'authored supplementary pending-semantic-scope audit',
        'total':len(observations),'passed':sum(o['pass'] for o in observations),
        'source_stable':before==after,'database_stable':db_before==digest(database),
        'source_before':before,'source_after':after,'cases':observations}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report':str(output),'passed':report['passed'],
        'total':report['total'],'source_stable':report['source_stable']},ensure_ascii=False))
    return 0 if report['total']==report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
