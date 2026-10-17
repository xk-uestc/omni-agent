"""Frozen independent scorer. Gold is evaluation-only, never planner/retrieval input."""
import math
import sqlite3


def metrics(sql):
    p=sql.get('plan',{});result={(x.get('table'),x.get('column'),x.get('function')) for x in p.get('metrics',[])}
    if p.get('metric_column'):result.add((p.get('metric_table') or p.get('table'),p['metric_column'],p.get('metric_function')))
    return result


def scope_correct(sql,gold):
    filters=sql.get('plan',{}).get('filters',[])
    year=gold['year'];date=[f'{year}-01-01',f'{year+1}-01-01']
    time=any(f.get('table')=='sales_orders' and f.get('column')=='order_date' and f.get('operator')=='RANGE' and list(f.get('value') or [])==date for f in filters)
    region=('region' not in gold or any(f.get('table')=='sales_orders' and f.get('column')=='region' and f.get('operator')=='=' and f.get('value')==gold['region'] for f in filters))
    # No extra narrowing constraints may silently change the task.
    allowed={'order_date','region'} if 'region' in gold else {'order_date'}
    return time and region and all(f.get('column') in allowed for f in filters)


def score(gold,response,database,versions):
    r=response.get('result',{});tasks=r.get('execution_plan',[]);results=r.get('results',{})
    tools={t['tool'] for t in tasks if t['id'] in results}
    sqls=[results[t['id']] for t in tasks if t['tool']=='sql' and t['id'] in results]
    if r.get('sql'):sqls=[r];tools.add('sql')
    checks={'task_success':False,'operation_coverage':False,'result_correct':False,'source_correct':False,'clarification_correct':None}
    if gold['kind']=='clarify':
        good=response.get('status') in {'clarification','incomplete'} and not r.get('answer') and not any(t['tool']=='calculate' and t['id'] in results for t in tasks)
        checks.update(task_success=good,operation_coverage=good,result_correct=good,source_correct=good,clarification_correct=good)
        return checks
    with sqlite3.connect(database) as db:expected=db.execute(gold['sql']).fetchall()
    required={'A':{'document_formula','sql','calculate'},'B':{'sql','search'},'C':{'document_cell','sql'},'D':{'document_cell','compare','sql'},'SQL':{'sql'}}[gold['kind']]
    if gold['kind']=='A' and gold.get('rate') is not None:required.add('document_cell')
    checks['operation_coverage']=required<=tools
    docs=r.get('source_validation',{}).get('documents',{})
    checks['source_correct']=set(gold['documents'])<=set(docs) and all(versions.get(k)==v for k,v in docs.items())
    if gold['kind']=='SQL':checks['source_correct']=bool(r.get('provenance',{}).get('source_revision'))
    verified_sql=bool(sqls) and all(s.get('sql') and s.get('provenance',{}).get('source_revision') and scope_correct(s,gold) for s in sqls)
    present=set().union(*(metrics(s) for s in sqls)) if sqls else set()
    calculations=[results[t['id']] for t in tasks if t['tool']=='calculate' and t['id'] in results]
    if gold['kind']=='A':
        base,count=expected[0];want=base/count if gold['formula']=='客单价' else base*(1+gold['rate'])
        required_metrics={('sales_orders','sales_amount','SUM')}
        if gold['formula']=='客单价':required_metrics.add(('sales_orders','order_id','COUNT'))
        actual=bool(calculations) and any(isinstance(c.get('value'),(int,float)) and math.isclose(c['value'],want,abs_tol=.0001,rel_tol=1e-7)
                and c.get('parameter_semantics_validation',{}).get('status')=='verified' for c in calculations)
        # The user-visible answer must actually expose the verified result.
        answer=r.get('answer','').replace(',','')
        shown=any(format(want,spec) in answer for spec in ('.2f','.1f','.0f'))
        checks['result_correct']=verified_sql and required_metrics<=present and actual and shown
    elif gold['kind']=='B':
        top=expected[0][1];winners={row[0] for row in expected if row[1]==top}
        returned={row.get('region') for s in sqls for row in s.get('rows',[])}
        searches=[results[t['id']] for t in tasks if t['tool']=='search' and t['id'] in results]
        cited='\n'.join(h.get('snippet',h.get('text','')) for s in searches for h in s.get('hits',[]) if h.get('metadata',{}).get('document_id')=='methods' and h.get('metadata',{}).get('source_sha256')==versions.get('methods'))
        bindings=all(s.get('dependency_reference_validation',{}).get('status')=='verified' and s['dependency_reference_validation'].get('target_text')==gold['target'] for s in searches)
        answer=r.get('answer','')
        checks['result_correct']=verified_sql and ('sales_orders',gold['column'],'SUM') in present and returned==winners and bool(searches) and bindings and all(w in cited and w in answer for w in winners) and r.get('answer_status')!='insufficient_evidence'
    else:
        base=expected[0][0];values=[v for s in sqls for row in s.get('rows',[]) for v in row.values()]
        good=verified_sql and ('sales_orders','sales_amount','SUM') in present and base in values
        if gold['kind']=='D':good=good and any((results[t['id']].get('status')=='equal') is gold['comparison'] for t in tasks if t['tool']=='compare' and t['id'] in results)
        checks['result_correct']=good
    checks['task_success']=response.get('status')=='ok' and all(checks[k] for k in ('operation_coverage','result_correct','source_correct'))
    return checks
