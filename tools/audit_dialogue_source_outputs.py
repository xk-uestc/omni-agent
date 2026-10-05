"""Recheck real HTTP numeric outputs by physical projection, not an unordered number bag."""
import json
from pathlib import Path
import re
import sqlite3
import sys
from sqlglot import parse_one,exp


def main():
    path=Path(sys.argv[1])
    report=json.loads(path.read_text(encoding='utf-8'))
    records=[]
    with sqlite3.connect(path.parent/'business.sqlite') as connection:
        for index,turn in enumerate(report['turns'],1):
            response=turn.get('response',{})
            if response.get('route')!='sql' or response.get('status')!='ok':continue
            result=response['result'];scope=response['effective_question']
            year=re.search(r'(\d{4})年',scope)
            region=re.search(r'customers\.region=(华东|华南)',scope)
            channel=re.search(r'payments\.channel=(线上|线下)',scope)
            tree=parse_one(result['sql'],read='sqlite')
            sources={t.alias_or_name:t.name for t in tree.find_all(exp.Table)}
            roles={}
            for expression in tree.expressions:
                value=expression.this if isinstance(expression,exp.Alias) else expression
                label=expression.alias_or_name
                if isinstance(value,exp.Column) and sources.get(value.table)== 'customers' and value.name=='name':
                    roles['name']=label
                elif isinstance(value,(exp.Sum,exp.Count)) and isinstance(value.this,exp.Column):
                    column=value.this
                    if sources.get(column.table)=='payments' and isinstance(value,exp.Sum) and column.name=='amount':roles['sum']=label
                    if sources.get(column.table)=='payments' and isinstance(value,exp.Count) and column.name=='id':roles['count']=label
            passed=False
            expected=[]
            if year and region and channel and set(roles)=={'name','sum','count'}:
                y=int(year[1])
                expected=list(connection.execute('SELECT c.name,SUM(p.amount),COUNT(p.id) FROM payments p JOIN customers c ON p.customer_id=c.id '
                    'WHERE c.region=? AND p.channel=? AND p.created_at>=? AND p.created_at<? GROUP BY c.name',
                    (region[1],channel[1],f'{y}-01-01',f'{y+1}-01-01')))
                observed=[(row[roles['name']],row[roles['sum']],row[roles['count']]) for row in result['rows']]
                passed=sorted(observed)==sorted(expected)
            records.append({'http_turn':index,'passed':passed,'physical_column_roles':roles,'expected':expected})
    audit={'not_official_benchmark':True,'http_report':str(path),'passed':sum(r['passed'] for r in records),
        'total':len(records),'all_columns_mapped':all(set(r['physical_column_roles'])=={'name','sum','count'} for r in records),'turns':records}
    output=path.with_name('physical-output-audit.json')
    output.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'audit':str(output),'passed':audit['passed'],'total':audit['total']},ensure_ascii=False))
    return 0 if records and all(r['passed'] for r in records) else 1


if __name__=='__main__':raise SystemExit(main())
