"""Present verified calculations and signed differences without another API."""
import re


def calculation_answer(tasks,result,knowledge):
    if result.get('status')!='ok':return None
    results=result.get('results',{});lines=[];interpretations=[]
    def amount(value,unit):
        if unit=='CNY':return f'{value:,.2f}元'
        return f'{value:g}'+('' if unit in ('unknown',None) else unit)
    for task in tasks:
        step=results.get(task['id'],{})
        if task['tool']=='calculate' and step.get('parameter_semantics_validation',{}).get('status')=='verified':
            formula=results[task['args']['formula']['ref']]
            selectors=[]
            for name,reference in task['args']['parameters'].items():
                fact=results.get(reference['ref'],{})
                if 'rows' in fact:continue
                source=re.fullmatch(r'/api/v1/knowledge/documents/([^/]+)/original',fact.get('source_uri',''))
                if not source:continue
                title=knowledge.document(source[1])['title']
                label=fact.get('label') or fact.get('locator','').rsplit('/column:',1)[-1]
                value=fact.get('value');unit=fact.get('unit')
                if type(value) not in (int,float):continue
                text=f'{value*100:g}%' if unit=='ratio' else amount(value,unit)
                selectors.append(f'《{title}》{label}={text}')
            prefix='；'.join(selectors)
            lines.append(f'{prefix+"：" if prefix else ""}{formula["label"]}为{amount(step["value"],step.get("result_unit"))}。')
            if step.get('result_interpretation'):
                interpretations.append(step['result_interpretation'])
        elif task['tool']=='compare' and type(step.get('difference')) in (int,float):
            lines.append(f'两种口径的差额（前者减后者）为{amount(step["difference"],step.get("unit"))}。')
    return '\n'.join(lines+list(dict.fromkeys(interpretations))) if lines else None
